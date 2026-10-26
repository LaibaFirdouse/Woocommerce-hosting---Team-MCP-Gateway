"""Tests for chat tool allowlist + audited invocation helper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from auth.principal import AuthMethod, GatewayPrincipal, get_gateway_principal
from chat.invoke import (
    CHAT_ADMIN_TOOLS,
    CHAT_ALLOWED_TOOLS,
    CHAT_BASE_TOOLS,
    ChatToolInvocationError,
    assert_chat_tool_allowed,
    chat_tools_for_principal,
    invoke_chat_tool,
)
from models.user import UserRole
import servers.registry as registry


def _principal(
    user_id: int = 42,
    *,
    role: UserRole = UserRole.member,
) -> GatewayPrincipal:
    return GatewayPrincipal(
        user_id=user_id,
        email="member@example.com" if role is UserRole.member else "admin@example.com",
        role=role,
        auth_method=AuthMethod.jwt,
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


def _register(
    tool_name: str, *, owner: str = "inventory", url: str = "http://u/mcp"
) -> None:
    registry.TOOL_OWNER[tool_name] = owner
    registry.TOOL_UPSTREAM_URL[tool_name] = url
    registry.TOOL_SERVER_NAME[tool_name] = owner
    registry.UPSTREAM_AUTH_HEADERS[owner] = "Bearer upstream-secret"


def test_chat_allowed_tools_union_includes_admin_create_product() -> None:
    assert CHAT_BASE_TOOLS.isdisjoint({"create_product", "update_order_status"})
    assert "create_product" in CHAT_ADMIN_TOOLS
    assert "update_order_status" not in CHAT_ALLOWED_TOOLS
    assert CHAT_ALLOWED_TOOLS == CHAT_BASE_TOOLS | CHAT_ADMIN_TOOLS


def test_chat_tools_for_principal_by_role() -> None:
    assert chat_tools_for_principal(_principal(role=UserRole.member)) == CHAT_BASE_TOOLS
    assert chat_tools_for_principal(_principal(role=UserRole.admin)) == (
        CHAT_BASE_TOOLS | CHAT_ADMIN_TOOLS
    )


@pytest.mark.parametrize("tool_name", sorted(CHAT_BASE_TOOLS))
def test_allowed_read_only_tools_accepted_for_member(tool_name: str) -> None:
    _register(tool_name, owner="upstream")
    assert_chat_tool_allowed(tool_name, principal=_principal(role=UserRole.member))


def test_create_product_allowed_for_admin_only() -> None:
    _register("create_product", owner="woocommerce")
    assert_chat_tool_allowed(
        "create_product", principal=_principal(role=UserRole.admin)
    )
    with pytest.raises(ChatToolInvocationError, match="not allowed for chat"):
        assert_chat_tool_allowed(
            "create_product", principal=_principal(role=UserRole.member)
        )


@pytest.mark.parametrize(
    "tool_name",
    [
        "update_order_status",
        "gateway_health",
        "list_upstream_tools",
        "list_all_upstream_tools",
    ],
)
@pytest.mark.parametrize("role", [UserRole.member, UserRole.admin])
def test_non_chat_tools_rejected_for_all_roles(tool_name: str, role: UserRole) -> None:
    _register(tool_name)
    with pytest.raises(ChatToolInvocationError, match="not allowed for chat"):
        assert_chat_tool_allowed(tool_name, principal=_principal(role=role))


def test_unknown_tool_rejected() -> None:
    with pytest.raises(ChatToolInvocationError, match="not available"):
        assert_chat_tool_allowed(
            "get_inventory", principal=_principal(role=UserRole.member)
        )


def test_disabled_tool_rejected() -> None:
    _register("get_inventory")
    registry.TOOL_UPSTREAM_URL.pop("get_inventory")
    with pytest.raises(ChatToolInvocationError, match="not available"):
        assert_chat_tool_allowed(
            "get_inventory", principal=_principal(role=UserRole.member)
        )


def test_invoke_rejects_before_upstream_when_not_callable() -> None:
    _register("get_inventory")
    registry.TOOL_OWNER.pop("get_inventory")

    async def _run() -> None:
        with pytest.raises(ChatToolInvocationError):
            await invoke_chat_tool(
                tool_name="get_inventory",
                arguments={"sku": "A"},
                principal=_principal(),
            )

    asyncio.run(_run())


def test_sanitize_arguments_omits_null_optionals() -> None:
    from chat.invoke import _sanitize_arguments

    cleaned = _sanitize_arguments(
        {
            "per_page": None,
            "page": 1,
            "search": None,
            "authorization": "Bearer x",
        }
    )
    assert cleaned == {"page": 1}
    assert "per_page" not in cleaned
    assert "authorization" not in cleaned


def test_invoke_preserves_principal_for_audit() -> None:
    _register("get_inventory", owner="inventory")
    recorded: list[dict] = []

    def fake_record(**kwargs):
        recorded.append(kwargs)
        return True

    async def fake_upstream(url, name, arguments, *, auth_header=None):
        assert get_gateway_principal() is not None
        assert get_gateway_principal().user_id == 42
        assert url == "http://u/mcp"
        assert auth_header == "Bearer upstream-secret"
        assert "endpoint_url" not in arguments
        assert "authorization" not in arguments
        assert arguments.get("sku") == "ABC"
        return {"sku": "ABC", "qty": 3}

    async def _run() -> None:
        with (
            patch("chat.invoke.call_upstream_tool", side_effect=fake_upstream),
            patch("audit.service.record_tool_invocation", side_effect=fake_record),
        ):
            result = await invoke_chat_tool(
                tool_name="get_inventory",
                arguments={
                    "sku": "ABC",
                    "endpoint_url": "http://evil.example/mcp",
                    "authorization": "Bearer stolen",
                    "user_id": 999,
                },
                principal=_principal(42),
            )

        assert result == {"sku": "ABC", "qty": 3}

    asyncio.run(_run())
    assert len(recorded) == 1
    assert recorded[0]["user_id"] == 42
    assert recorded[0]["tool_name"] == "get_inventory"
    assert recorded[0]["server_name"] == "inventory"
    assert recorded[0]["outcome"] == "success"
    assert recorded[0]["user_id"] != 999


def test_admin_invoke_create_product_uses_registry_auth_not_client() -> None:
    _register("create_product", owner="woocommerce", url="http://woo/mcp")
    recorded: list[dict] = []

    async def fake_upstream(url, name, arguments, *, auth_header=None):
        assert name == "create_product"
        assert url == "http://woo/mcp"
        assert auth_header == "Bearer upstream-secret"
        assert "authorization" not in arguments
        assert arguments.get("name") == "Demo Widget"
        return {"id": 101, "name": "Demo Widget"}

    def fake_record(**kwargs):
        recorded.append(kwargs)
        return True

    async def _run() -> None:
        with (
            patch("chat.invoke.call_upstream_tool", side_effect=fake_upstream),
            patch("audit.service.record_tool_invocation", side_effect=fake_record),
        ):
            result = await invoke_chat_tool(
                tool_name="create_product",
                arguments={
                    "name": "Demo Widget",
                    "regular_price": "19.99",
                    "authorization": "Bearer stolen",
                },
                principal=_principal(7, role=UserRole.admin),
            )
        assert result["id"] == 101

    asyncio.run(_run())
    assert recorded[0]["user_id"] == 7
    assert recorded[0]["tool_name"] == "create_product"
    assert recorded[0]["outcome"] == "success"


def test_member_invoke_create_product_rejected() -> None:
    _register("create_product", owner="woocommerce")

    async def _run() -> None:
        with patch(
            "chat.invoke.call_upstream_tool",
            new=AsyncMock(side_effect=AssertionError("must not call upstream")),
        ):
            with pytest.raises(ChatToolInvocationError, match="not allowed for chat"):
                await invoke_chat_tool(
                    tool_name="create_product",
                    arguments={"name": "X", "regular_price": "1"},
                    principal=_principal(role=UserRole.member),
                )

    asyncio.run(_run())


def test_invoke_does_not_log_authorization_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _register("list_orders", owner="woocommerce", url="http://woo/mcp")
    registry.UPSTREAM_AUTH_HEADERS["woocommerce"] = "Bearer never-log-this-token"

    async def _run() -> None:
        with (
            patch(
                "chat.invoke.call_upstream_tool",
                new=AsyncMock(return_value={"orders": []}),
            ),
            patch("audit.service.record_tool_invocation", return_value=True),
            caplog.at_level("INFO"),
        ):
            await invoke_chat_tool(
                tool_name="list_orders",
                arguments={},
                principal=_principal(),
            )

    asyncio.run(_run())
    joined = " ".join(record.message for record in caplog.records)
    assert "never-log-this-token" not in joined


def test_invoke_requires_principal() -> None:
    _register("get_shipping_status", owner="shipping")

    async def _run() -> None:
        with pytest.raises(ChatToolInvocationError, match="principal"):
            await invoke_chat_tool(
                tool_name="get_shipping_status",
                arguments={},
                principal=None,  # type: ignore[arg-type]
            )

    asyncio.run(_run())
