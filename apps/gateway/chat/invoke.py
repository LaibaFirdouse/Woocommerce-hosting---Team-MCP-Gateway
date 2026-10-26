"""
Chat-scoped invocation of gateway-registered MCP tools.

Routing, credentials, and enablement always come from the live in-process
registry — never from client-supplied URLs or Authorization headers.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit.service import run_audited_tool
from auth.principal import GatewayPrincipal, use_gateway_principal
from servers.registry import (
    TOOL_OWNER,
    TOOL_SERVER_NAME,
    TOOL_UPSTREAM_URL,
    UPSTREAM_AUTH_HEADERS,
    is_tool_callable,
)
from servers.upstream import call_upstream_tool, format_upstream_error

logger = logging.getLogger(__name__)

# Read-only tools available to every authenticated chat user.
CHAT_BASE_TOOLS = frozenset(
    {
        "list_products",
        "list_orders",
        "get_order",
        "get_inventory",
        "get_shipping_status",
    }
)

# Extra chat tools for admins only. update_order_status stays out of chat
# (see auth.permissions.ADMIN_ONLY_TOOLS for MCP-wide mutation policy).
CHAT_ADMIN_TOOLS = frozenset(
    {
        "create_product",
    }
)

# Union of every tool that chat may offer under some authorized role.
CHAT_ALLOWED_TOOLS = CHAT_BASE_TOOLS | CHAT_ADMIN_TOOLS


def chat_tools_for_principal(principal: GatewayPrincipal) -> frozenset[str]:
    """Return the chat tool allowlist for the authenticated principal's role."""
    from auth.permissions import role_is_admin

    if role_is_admin(principal.role):
        return CHAT_BASE_TOOLS | CHAT_ADMIN_TOOLS
    return CHAT_BASE_TOOLS


# Never forward these as tool arguments — they are not part of upstream schemas
# and must not influence routing or authentication.
_CLIENT_CONTROL_KEYS = frozenset(
    {
        "authorization",
        "authorization_header",
        "auth_header",
        "endpoint_url",
        "upstream_url",
        "server_url",
        "api_key",
        "raw_key",
        "password",
        "token",
        "user_id",
        "auth_header_ciphertext",
    }
)


class ChatToolInvocationError(ValueError):
    """Tool rejected before execution (allowlist, disabled, unknown)."""


def _sanitize_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    if not arguments:
        return {}
    if not isinstance(arguments, dict):
        raise ChatToolInvocationError("Tool arguments must be an object.")
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if str(key).lower() in _CLIENT_CONTROL_KEYS:
            continue
        # Explicit nulls from the model mean "omit" for optional MCP fields.
        if value is None:
            continue
        cleaned[str(key)] = value
    return cleaned


def assert_chat_tool_allowed(
    tool_name: str,
    *,
    principal: GatewayPrincipal,
) -> None:
    if principal is None:
        raise ChatToolInvocationError("Authenticated principal is required.")
    name = (tool_name or "").strip()
    if not name:
        raise ChatToolInvocationError("Tool name is required.")
    allowed = chat_tools_for_principal(principal)
    if name not in allowed:
        raise ChatToolInvocationError(
            f"Tool {name!r} is not allowed for chat."
        )
    if not is_tool_callable(name):
        raise ChatToolInvocationError(
            f"Tool {name!r} is not available (unknown, disabled, or removed)."
        )


async def invoke_chat_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    principal: GatewayPrincipal,
    session_factory: Any | None = None,
) -> Any:
    """
    Validate and execute a chat-allowed tool under ``principal``.

    Audit ``user_id`` comes only from ``principal`` (via ``use_gateway_principal``),
    never from ``arguments``. Upstream credentials stay server-side.
    """
    if principal is None:
        raise ChatToolInvocationError("Authenticated principal is required.")

    name = (tool_name or "").strip()
    # Re-check immediately before execution (registry can change after planning).
    assert_chat_tool_allowed(name, principal=principal)
    clean_args = _sanitize_arguments(arguments)

    route_url = TOOL_UPSTREAM_URL.get(name)
    owner = TOOL_OWNER.get(name)
    audit_server = TOOL_SERVER_NAME.get(name) or owner or "upstream"
    if not route_url or not owner:
        raise ChatToolInvocationError(
            f"Tool {name!r} is not available (unknown, disabled, or removed)."
        )

    auth_header = UPSTREAM_AUTH_HEADERS.get(owner)
    # Final callable check right before the network call.
    if not is_tool_callable(name):
        raise ChatToolInvocationError(
            f"Tool {name!r} is not available (unknown, disabled, or removed)."
        )

    started = time.perf_counter()

    async def _invoke() -> Any:
        # Refuse client-supplied routing: always use registry URL + server auth.
        try:
            return await call_upstream_tool(
                route_url,
                name,
                clean_args,
                auth_header=auth_header,
            )
        except Exception as exc:
            raise RuntimeError(format_upstream_error(route_url, exc)) from None

    with use_gateway_principal(principal):
        try:
            result = await run_audited_tool(
                server_name=audit_server,
                tool_name=name,
                operation=_invoke,
                session_factory=session_factory,
            )
        except ChatToolInvocationError:
            raise
        except Exception:
            # Upstream/runtime failures propagate; audit already recorded.
            raise

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "chat tool=%s server=%s user_id=%s duration_ms=%s",
        name,
        audit_server,
        principal.user_id,
        duration_ms,
    )
    return result
