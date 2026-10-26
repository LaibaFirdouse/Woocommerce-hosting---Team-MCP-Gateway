"""HTTP tests for POST /chat."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from auth.principal import AuthMethod, GatewayPrincipal
from auth.tokens import create_access_token
from chat.llm import ChatLLMError, LLMClient, LLMConfig, LLMConfigurationError
from models.user import User, UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


@pytest.fixture(scope="module")
def chat_http_client():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")

    async def noop_upstream() -> None:
        return

    sys.modules.pop("server", None)
    try:
        import server as server_mod

        with (
            patch.object(server_mod, "_register_upstream_proxies", noop_upstream),
            patch.object(server_mod, "startup_database"),
            patch.object(server_mod, "shutdown_database"),
        ):
            with TestClient(server_mod.mcp.streamable_http_app()) as client:
                yield client, server_mod
    finally:
        monkeypatch.undo()
        sys.modules.pop("server", None)


@pytest.fixture(autouse=True)
def _reset_llm_factory():
    import chat.http as chat_http

    chat_http._llm_client_factory = None
    # Avoid live upstream discovery during unit tests (route hook is module-level).
    async def _noop_ready() -> None:
        return None

    chat_http._ensure_runtime_ready = _noop_ready
    yield
    chat_http._llm_client_factory = None
    chat_http._ensure_runtime_ready = None


def _user(
    *,
    user_id: int = 1,
    email: str = "member@example.com",
    role: UserRole = UserRole.member,
) -> User:
    return User(
        id=user_id,
        email=email,
        password_hash="hashed",
        role=role,
        is_active=True,
    )


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


def _llm_config(**overrides: Any) -> LLMConfig:
    base = {
        "api_key": "sk-test",
        "model": "gpt-test",
        "base_url": "https://llm.test/v1",
        "max_tool_rounds": 6,
        "timeout_seconds": 60.0,
    }
    base.update(overrides)
    return LLMConfig(**base)


def _mock_llm(responses: list[dict[str, Any]], *, config: LLMConfig | None = None) -> LLMClient:
    client = MagicMock(spec=LLMClient)
    client.config = config or _llm_config()
    client.chat_completions = AsyncMock(side_effect=list(responses))
    client.aclose = AsyncMock()
    return client


def test_chat_unauthenticated(chat_http_client) -> None:
    client, _ = chat_http_client
    response = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 401


def test_chat_rejects_api_key_bearer(chat_http_client) -> None:
    client, _ = chat_http_client
    response = client.post(
        "/chat",
        headers={"Authorization": "Bearer uk_abc.secret-value"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated."


def test_chat_authenticated_text_response(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    chat_http._llm_client_factory = lambda: _mock_llm(
        [
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "Hello from Urumi."}}
                ]
            }
        ]
    )
    ready = AsyncMock()
    chat_http._ensure_runtime_ready = ready

    with patch("chat.http._authenticated_user", return_value=user):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "Hello from Urumi."
    assert body["tool_activity"] == []
    assert body["rounds"] == 1
    ready.assert_awaited()


def test_chat_multi_tool_rounds_and_audit_principal(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user(user_id=7, email="shopper@example.com")
    import chat.http as chat_http

    llm_responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {
                                    "name": "get_inventory",
                                    "arguments": '{"sku":"ABC"}',
                                },
                            },
                            {
                                "id": "2",
                                "type": "function",
                                "function": {
                                    "name": "get_shipping_status",
                                    "arguments": '{"order_id":123}',
                                },
                            },
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "SKU ABC has 3 units; order 123 is in transit.",
                    }
                }
            ]
        },
    ]
    chat_http._llm_client_factory = lambda: _mock_llm(llm_responses)

    invoked: list[dict[str, Any]] = []

    async def fake_invoke(*, tool_name, arguments, principal, session_factory=None):
        assert isinstance(principal, GatewayPrincipal)
        assert principal.user_id == 7
        assert principal.auth_method is AuthMethod.jwt
        invoked.append({"tool_name": tool_name, "arguments": arguments, "user_id": principal.user_id})
        if tool_name == "get_inventory":
            return {"sku": "ABC", "qty": 3}
        return {"order_id": 123, "status": "in_transit"}

    with (
        patch("chat.http._authenticated_user", return_value=user),
        patch("chat.agent.invoke_chat_tool", side_effect=fake_invoke),
        patch("chat.agent.is_tool_callable", return_value=True),
    ):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Stock for ABC and shipping for order 123?",
                    }
                ],
                "user_id": 999,
                "role": "admin",
                "endpoint_url": "http://evil/mcp",
            },
        )

    assert response.status_code == 200
    body = response.json()
    content = body["message"]["content"]
    # Grounded summary echoes successful tool previews (may be JSON, not prose).
    assert "qty" in content.lower() or "in_transit" in content.lower() or "sku" in content.lower()
    assert body["rounds"] == 2
    assert len(body["tool_activity"]) == 2
    assert {row["name"] for row in body["tool_activity"]} == {
        "get_inventory",
        "get_shipping_status",
    }
    assert all(row["outcome"] == "success" for row in body["tool_activity"])
    assert [i["user_id"] for i in invoked] == [7, 7]
    assert all(i["user_id"] != 999 for i in invoked)


def test_chat_disallowed_tool_never_executes(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user(role=UserRole.member)
    import chat.http as chat_http
    import servers.registry as registry

    registry.TOOL_OWNER["create_product"] = "woocommerce"
    registry.TOOL_UPSTREAM_URL["create_product"] = "http://woo/mcp"
    registry.TOOL_SERVER_NAME["create_product"] = "woocommerce"

    chat_http._llm_client_factory = lambda: _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "create_product",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I cannot create products.",
                        }
                    }
                ]
            },
        ]
    )

    with (
        patch("chat.http._authenticated_user", return_value=user),
        patch(
            "chat.invoke.call_upstream_tool",
            new=AsyncMock(side_effect=AssertionError("must not call upstream")),
        ),
    ):
        # Real invoke_chat_tool + allowlist — no upstream call for members.
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "create a product"}]},
        )

    assert response.status_code == 200
    activity = response.json()["tool_activity"][0]
    assert activity["outcome"] == "error"
    assert activity["name"] == "create_product"
    assert "result_preview" not in activity
    assert "not allowed" in activity["error"].lower()


def test_chat_admin_can_create_product(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user(user_id=9, email="admin@example.com", role=UserRole.admin)
    import chat.http as chat_http
    import servers.registry as registry

    registry.TOOL_OWNER["create_product"] = "woocommerce"
    registry.TOOL_UPSTREAM_URL["create_product"] = "http://woo/mcp"
    registry.TOOL_SERVER_NAME["create_product"] = "woocommerce"
    registry.UPSTREAM_AUTH_HEADERS["woocommerce"] = "Bearer server-side-secret"

    chat_http._llm_client_factory = lambda: _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "create_product",
                                        "arguments": (
                                            '{"name":"HTTP Admin Mug",'
                                            '"regular_price":"9.00"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Product created.",
                        }
                    }
                ]
            },
        ]
    )

    upstream = AsyncMock(return_value={"id": 77, "name": "HTTP Admin Mug"})

    with (
        patch("chat.http._authenticated_user", return_value=user),
        patch("chat.invoke.call_upstream_tool", new=upstream),
        patch("audit.service.record_tool_invocation", return_value=True),
        patch("chat.agent.is_tool_callable", return_value=True),
    ):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "create a product"}]},
        )

    assert response.status_code == 200
    activity = response.json()["tool_activity"][0]
    assert activity["outcome"] == "success"
    assert activity["name"] == "create_product"
    upstream.assert_awaited()
    call_kwargs = upstream.await_args
    assert call_kwargs.args[0] == "http://woo/mcp"
    assert call_kwargs.args[1] == "create_product"
    assert call_kwargs.kwargs.get("auth_header") == "Bearer server-side-secret"
    assert "authorization" not in call_kwargs.args[2]


def test_chat_unknown_tool_never_succeeds(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    chat_http._llm_client_factory = lambda: _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "drop_database",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "No."}}
                ]
            },
        ]
    )

    with (
        patch("chat.http._authenticated_user", return_value=user),
        patch(
            "chat.invoke.call_upstream_tool",
            new=AsyncMock(side_effect=AssertionError("must not call upstream")),
        ),
    ):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "hack"}]},
        )

    assert response.status_code == 200
    activity = response.json()["tool_activity"][0]
    assert activity["outcome"] == "error"
    assert activity["name"] == "drop_database"
    assert "result_preview" not in activity


def test_chat_provider_not_configured(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    def boom():
        raise LLMConfigurationError("LLM_API_KEY is not configured.")

    chat_http._llm_client_factory = boom

    with patch("chat.http._authenticated_user", return_value=user):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_chat_provider_timeout(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    llm = _mock_llm([])
    llm.chat_completions = AsyncMock(
        side_effect=ChatLLMError("The language model request timed out.")
    )
    chat_http._llm_client_factory = lambda: llm

    with patch("chat.http._authenticated_user", return_value=user):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 502
    assert "timed out" in response.json()["detail"].lower()
    assert "Traceback" not in response.text


def test_chat_malformed_provider_response(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    chat_http._llm_client_factory = lambda: _mock_llm([{"choices": []}])

    with patch("chat.http._authenticated_user", return_value=user):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 502
    assert "invalid response" in response.json()["detail"].lower()


def test_chat_overall_timeout(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    chat_http._llm_client_factory = lambda: _mock_llm(
        [
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "too late"}}
                ]
            }
        ],
        config=_llm_config(timeout_seconds=0.0001),
    )

    with (
        patch("chat.http._authenticated_user", return_value=user),
        patch("chat.agent._monotonic", side_effect=[0.0, 1.0]),
    ):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"].lower()


def test_chat_validation_errors(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    with patch("chat.http._authenticated_user", return_value=user):
        assert (
            client.post("/chat", headers=_headers(user), json={}).status_code
            == 400
        )
        assert (
            client.post(
                "/chat",
                headers=_headers(user),
                json={"messages": [{"role": "assistant", "content": "only"}]},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/chat",
                headers=_headers(user),
                json={"messages": [{"role": "system", "content": "x"}]},
            ).status_code
            == 400
        )


def test_chat_response_hides_secrets_and_urls(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    chat_http._llm_client_factory = lambda: _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "Done."}}
                ]
            },
        ]
    )

    async def fake_invoke(**kwargs):
        raise RuntimeError(
            "Failed Authorization: Bearer eyJhbGciOi.secret "
            "at http://127.0.0.1:8002/mcp upstream"
        )

    with (
        patch("chat.http._authenticated_user", return_value=user),
        patch("chat.agent.invoke_chat_tool", side_effect=fake_invoke),
        patch("chat.agent.is_tool_callable", return_value=True),
    ):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "stock?"}]},
        )

    assert response.status_code == 200
    text = response.text
    assert "eyJhbGciOi" not in text
    assert "Bearer eyJ" not in text
    # Internal URL may appear redacted or truncated in sanitized error — Bearer must not.
    activity = response.json()["tool_activity"][0]
    assert activity["outcome"] == "error"
    assert "[REDACTED]" in activity["error"] or "Bearer" not in activity["error"]


def test_chat_tool_failure_still_returns_assistant(chat_http_client) -> None:
    client, _ = chat_http_client
    user = _user()
    import chat.http as chat_http

    chat_http._llm_client_factory = lambda: _mock_llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "get_order",
                                        "arguments": '{"order_id":1}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I could not load that order.",
                        }
                    }
                ]
            },
        ]
    )

    async def fake_invoke(**kwargs):
        raise RuntimeError("upstream unavailable")

    with (
        patch("chat.http._authenticated_user", return_value=user),
        patch("chat.agent.invoke_chat_tool", side_effect=fake_invoke),
        patch("chat.agent.is_tool_callable", return_value=True),
    ):
        response = client.post(
            "/chat",
            headers=_headers(user),
            json={"messages": [{"role": "user", "content": "order 1"}]},
        )

    assert response.status_code == 200
    assert response.json()["message"]["content"] == "I could not load that order."
    assert response.json()["tool_activity"][0]["outcome"] == "error"
