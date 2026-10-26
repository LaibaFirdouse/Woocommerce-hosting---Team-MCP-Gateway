"""Server-side authorization helpers shared by chat and MCP proxies."""

from __future__ import annotations

from typing import Any

from models.user import UserRole

# Mutating WooCommerce tools restricted to admins for both chat and MCP.
ADMIN_ONLY_TOOLS = frozenset(
    {
        "create_product",
        "update_order_status",
    }
)


def tool_requires_admin(tool_name: str) -> bool:
    return (tool_name or "").strip() in ADMIN_ONLY_TOOLS


def role_is_admin(role: UserRole | str | Any) -> bool:
    """True when role is admin. Prefer equality over identity for ORM/enum safety."""
    if isinstance(role, UserRole):
        return role == UserRole.admin
    return str(role).strip().lower() == UserRole.admin.value


def user_is_admin(user: Any) -> bool:
    """Active database user with admin role (never trust JWT claims alone)."""
    if user is None:
        return False
    if not bool(getattr(user, "is_active", False)):
        return False
    return role_is_admin(getattr(user, "role", None))


def role_may_invoke_tool(role: UserRole | str, tool_name: str) -> bool:
    if not tool_requires_admin(tool_name):
        return True
    return role_is_admin(role)
