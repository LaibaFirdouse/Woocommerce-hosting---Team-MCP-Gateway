"""
Matrix coverage for every required Urumi tool: chat policy, gateway natives,
proxy registration, and multi-tool partial failure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import Tool as UpstreamTool

from auth.principal import AuthMethod, GatewayPrincipal
from chat.agent import run_chat_agent
from chat.invoke import (
    CHAT_ADMIN_TOOLS,
    CHAT_ALLOWED_TOOLS,
    CHAT_BASE_TOOLS,
    ChatToolInvocationError,
    assert_chat_tool_allowed,
)
from chat.llm import LLMClient, LLMConfig
from models.user import User, UserRole
import servers.registry as registry


REQUIRED_PROXY_TOOLS = (
    "list_products",
    "create_product",
    "list_orders",
    "get_order",
    "update_order_status",
    "get_inventory",
    "get_shipping_status",
)

REQUIRED_GATEWAY_TOOLS = (
    "gateway_health",
    "list_upstream_tools",
    "list_all_upstream_tools",
)

CHAT_READ_ONLY = frozenset(
    {
        "list_products",
        "list_orders",
        "get_order",
        "get_inventory",
        "get_shipping_status",
    }
)

# Always blocked in chat for every role (no documented chat policy for these).
CHAT_FORBIDDEN = frozenset(
    {
        "update_order_status",
        "gateway_health",
        "list_upstream_tools",
        "list_all_upstream_tools",
    }
)


@pytest.fixture(autouse=True)
def _clear_registry():
    registry.TOOL_OWNER.clear()
    registry.TOOL_UPSTREAM_URL.clear()
    registry.TOOL_SERVER_NAME.clear()
    registry.UPSTREAM_AUTH_HEADERS.clear()
    yield
    registry.TOOL_OWNER.clear()
    registry.TOOL_UPSTREAM_URL.clear()
    registry.TOOL_SERVER_NAME.clear()
    registry.UPSTREAM_AUTH_HEADERS.clear()


def _register(tool_name: str, *, owner: str = "upstream", url: str = "http://u/mcp") -> None:
    registry.TOOL_OWNER[tool_name] = owner
    registry.TOOL_UPSTREAM_URL[tool_name] = url
    registry.TOOL_SERVER_NAME[tool_name] = owner


def test_chat_allowlist_matches_required_read_only_plus_admin_create() -> None:
    assert CHAT_BASE_TOOLS == CHAT_READ_ONLY
    assert CHAT_ADMIN_TOOLS == frozenset({"create_product"})
    assert CHAT_ALLOWED_TOOLS == CHAT_READ_ONLY | frozenset({"create_product"})
    assert "update_order_status" not in CHAT_ALLOWED_TOOLS


def _member() -> GatewayPrincipal:
    return GatewayPrincipal(
        user_id=3,
        email="member@example.com",
        role=UserRole.member,
        auth_method=AuthMethod.jwt,
    )


def _admin() -> GatewayPrincipal:
    return GatewayPrincipal(
        user_id=1,
        email="admin@example.com",
        role=UserRole.admin,
        auth_method=AuthMethod.jwt,
    )


@pytest.mark.parametrize("tool_name", sorted(CHAT_READ_ONLY))
def test_required_read_only_tools_allowed_in_chat(tool_name: str) -> None:
    _register(tool_name)
    assert_chat_tool_allowed(tool_name, principal=_member())


def test_create_product_allowed_for_admin_blocked_for_member() -> None:
    _register("create_product")
    assert_chat_tool_allowed("create_product", principal=_admin())
    with pytest.raises(ChatToolInvocationError, match="not allowed for chat"):
        assert_chat_tool_allowed("create_product", principal=_member())


@pytest.mark.parametrize("tool_name", sorted(CHAT_FORBIDDEN))
@pytest.mark.parametrize("principal_factory", [_member, _admin])
def test_required_forbidden_tools_blocked_in_chat(
    tool_name: str, principal_factory
) -> None:
    _register(tool_name)
    with pytest.raises(ChatToolInvocationError, match="not allowed for chat"):
        assert_chat_tool_allowed(tool_name, principal=principal_factory())


def test_register_proxy_for_every_required_upstream_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-key-with-enough-length!!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    import importlib
    import sys

    sys.modules.pop("server", None)
    import server as server_mod

    owners = {
        "list_products": ("woocommerce", "http://127.0.0.1:8000/mcp"),
        "create_product": ("woocommerce", "http://127.0.0.1:8000/mcp"),
        "list_orders": ("woocommerce", "http://127.0.0.1:8000/mcp"),
        "get_order": ("woocommerce", "http://127.0.0.1:8000/mcp"),
        "update_order_status": ("woocommerce", "http://127.0.0.1:8000/mcp"),
        "get_inventory": ("inventory", "http://127.0.0.1:8002/mcp"),
        "get_shipping_status": ("shipping", "http://127.0.0.1:8003/mcp"),
    }

    for name, (owner, url) in owners.items():
        server_mod.TOOL_UPSTREAM_URL.pop(name, None)
        server_mod.TOOL_SERVER_NAME.pop(name, None)
        server_mod.TOOL_OWNER.pop(name, None)
        server_mod.mcp._tool_manager._tools.pop(name, None)
        upstream = UpstreamTool(
            name=name,
            description=f"Tool {name}",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        )
        assert server_mod._register_proxy_tool(upstream, url, owner=owner) is True
        assert name in server_mod.mcp._tool_manager._tools
        assert server_mod.TOOL_OWNER[name] == owner
        assert registry.is_tool_callable(name)

    # Cleanup
    for name in owners:
        server_mod.TOOL_UPSTREAM_URL.pop(name, None)
        server_mod.TOOL_SERVER_NAME.pop(name, None)
        server_mod.TOOL_OWNER.pop(name, None)
        server_mod.mcp._tool_manager._tools.pop(name, None)
    sys.modules.pop("server", None)
    importlib.invalidate_caches()


def test_gateway_native_tools_are_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-key-with-enough-length!!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    import sys

    sys.modules.pop("server", None)
    import server as server_mod

    registered = set(server_mod.mcp._tool_manager._tools.keys())
    for name in REQUIRED_GATEWAY_TOOLS:
        assert name in registered

    sys.modules.pop("server", None)


def test_gateway_health_invoke_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-key-with-enough-length!!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    import sys

    sys.modules.pop("server", None)
    import server as server_mod

    with patch("audit.service.record_tool_invocation", return_value=True):
        result = asyncio.run(server_mod.gateway_health())

    assert result == {"status": "ok"}
    sys.modules.pop("server", None)


def test_list_all_upstream_tools_preserves_partial_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-key-with-enough-length!!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    import sys

    sys.modules.pop("server", None)
    import server as server_mod

    async def fake_snapshot(upstream):
        if upstream.upstream_id == "broken":
            return {
                "upstream_id": "broken",
                "upstream_url": upstream.url,
                "status": "unavailable",
                "error": "Could not connect",
                "tools": [],
                "_raw_tools": [],
            }
        return {
            "upstream_id": upstream.upstream_id,
            "upstream_url": upstream.url,
            "status": "ok",
            "error": None,
            "tools": [{"name": "probe_tool", "description": "x"}],
            "_raw_tools": [],
        }

    configs = [
        registry.UpstreamConfig("healthy", "http://h/mcp", None),
        registry.UpstreamConfig("broken", "http://b/mcp", None),
    ]

    with (
        patch("server.load_enabled_upstream_configs", return_value=configs),
        patch.object(server_mod, "_discover_upstream_snapshot", side_effect=fake_snapshot),
        patch("audit.service.record_tool_invocation", return_value=True),
    ):
        result = asyncio.run(server_mod.list_all_upstream_tools())

    by_id = {row["upstream_id"]: row for row in result["upstreams"]}
    assert by_id["healthy"]["status"] == "ok"
    assert by_id["healthy"]["tool_count"] == 1
    assert by_id["broken"]["status"] == "unavailable"
    assert by_id["broken"]["error"]
    sys.modules.pop("server", None)


def test_list_upstream_tools_surfaces_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-key-with-enough-length!!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    import sys

    sys.modules.pop("server", None)
    import server as server_mod

    async def boom(*_a, **_k):
        raise RuntimeError("Could not connect to upstream MCP at http://woo/mcp.")

    with (
        patch(
            "server.load_enabled_upstream_configs",
            return_value=[
                registry.UpstreamConfig("woocommerce", "http://woo/mcp", None)
            ],
        ),
        patch.object(server_mod, "_discover_tools_at_url", side_effect=boom),
        patch("audit.service.record_tool_invocation", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="Could not connect"):
            asyncio.run(server_mod.list_upstream_tools())

    sys.modules.pop("server", None)


def _principal() -> GatewayPrincipal:
    user = User(
        id=3,
        email="agent@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )
    return GatewayPrincipal.from_user(user, auth_method=AuthMethod.jwt)


def _llm(responses: list[dict[str, Any]]) -> LLMClient:
    client = MagicMock(spec=LLMClient)
    client.config = LLMConfig(
        api_key="sk-test",
        model="qwen-test",
        base_url="http://127.0.0.1:11434/v1",
        max_tool_rounds=6,
        timeout_seconds=60.0,
    )
    client.chat_completions = AsyncMock(side_effect=list(responses))
    return client


def _mcp_with_tools(names: list[str]) -> MagicMock:
    tools = {}
    for name in names:
        tool = MagicMock()
        tool.description = f"Tool {name}"
        tool.parameters = {"type": "object", "properties": {}}
        tools[name] = tool
    mcp = MagicMock()
    mcp._tool_manager._tools = tools
    return mcp


def test_chat_preserves_success_when_sibling_tool_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One tool succeeds and another fails — grounded answer keeps the success."""
    mcp = _mcp_with_tools(["get_inventory", "get_shipping_status"])
    llm = _llm(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "a",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": "{}",
                                    },
                                },
                                {
                                    "id": "b",
                                    "function": {
                                        "name": "get_shipping_status",
                                        "arguments": '{"order_id":1}',
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
                            "content": "ignored hallucinated summary",
                        }
                    }
                ]
            },
        ]
    )

    async def fake_invoke(*, tool_name, arguments, principal, session_factory=None):
        if tool_name == "get_inventory":
            return {
                "inventory": [
                    {"product_id": 7, "available_quantity": 12, "warehouse": "Zone A"}
                ]
            }
        raise RuntimeError("Upstream tool failed: shipping unavailable")

    for name in ("get_inventory", "get_shipping_status"):
        _register(name, owner=name)

    with (
        patch("chat.agent.is_tool_callable", return_value=True),
        caplog.at_level(logging.INFO),
    ):
        result = asyncio.run(
            run_chat_agent(
                messages=[{"role": "user", "content": "stock and shipping?"}],
                principal=_principal(),
                llm=llm,
                mcp=mcp,
                invoke_tool=fake_invoke,
            )
        )

    content = result["message"]["content"]
    activity = {row["name"]: row for row in result["tool_activity"]}
    assert activity["get_inventory"]["outcome"] == "success"
    assert activity["get_shipping_status"]["outcome"] == "error"
    assert "available_quantity=12" in content
    assert "get_inventory:" in content
    assert "ignored hallucinated" not in content
