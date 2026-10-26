"""Unit tests for shared admin-only tool authorization."""

from __future__ import annotations

from auth.permissions import (
    ADMIN_ONLY_TOOLS,
    role_is_admin,
    role_may_invoke_tool,
    tool_requires_admin,
    user_is_admin,
)
from models.user import UserRole


def test_admin_only_tools_include_mutations() -> None:
    assert "create_product" in ADMIN_ONLY_TOOLS
    assert "update_order_status" in ADMIN_ONLY_TOOLS
    assert "list_products" not in ADMIN_ONLY_TOOLS


def test_role_may_invoke_tool() -> None:
    assert role_may_invoke_tool(UserRole.member, "list_products") is True
    assert role_may_invoke_tool(UserRole.member, "create_product") is False
    assert role_may_invoke_tool(UserRole.admin, "create_product") is True
    assert role_may_invoke_tool(UserRole.member, "update_order_status") is False
    assert tool_requires_admin("create_product") is True
    assert role_may_invoke_tool("admin", "create_product") is True
    assert role_may_invoke_tool("member", "create_product") is False


def test_user_is_admin_helpers() -> None:
    assert role_is_admin(UserRole.admin) is True
    assert role_is_admin("admin") is True
    assert role_is_admin(UserRole.member) is False

    class _U:
        def __init__(self, role, active=True):
            self.role = role
            self.is_active = active

    assert user_is_admin(_U(UserRole.admin)) is True
    assert user_is_admin(_U("admin")) is True
    assert user_is_admin(_U(UserRole.admin, active=False)) is False
    assert user_is_admin(_U(UserRole.member)) is False
    assert user_is_admin(None) is False
