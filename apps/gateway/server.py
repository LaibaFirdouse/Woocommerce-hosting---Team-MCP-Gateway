import atexit
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.types import Tool as UpstreamTool
from pydantic import Field, create_model
from starlette.applications import Starlette

from audit.service import GATEWAY_SERVER_NAME, run_audited_tool
from auth.mcp_settings import fastmcp_auth_kwargs, validate_mcp_auth_startup
from db.lifecycle import shutdown_database, startup_database
from servers.registry import (
    TOOL_OWNER,
    TOOL_SERVER_NAME,
    TOOL_UPSTREAM_URL,
    UPSTREAM_AUTH_HEADERS,
    UpstreamConfig,
    env_upstream_defaults,
    is_tool_callable,
    load_enabled_upstream_configs,
    refresh_upstream_tools,
    seed_default_servers_if_empty,
)
from servers.upstream import (
    UPSTREAM_TIMEOUT_SECONDS,
    call_upstream_tool,
    discover_tools_at_url,
    format_upstream_error,
)

logger = logging.getLogger(__name__)

HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
PORT = int(os.getenv("GATEWAY_PORT", "8001"))
WOOCOMMERCE_MCP_URL = os.getenv(
    "WOOCOMMERCE_MCP_URL", "http://127.0.0.1:8000/mcp"
).strip()

RESERVED_TOOL_NAMES = frozenset(
    {"gateway_health", "list_upstream_tools", "list_all_upstream_tools"}
)

# Backward-compatible alias used by older tests/docs.
UPSTREAM_CONFIGS: tuple[UpstreamConfig, ...] = env_upstream_defaults()

# FastMCP re-enters MCP lifespan on every stateless /mcp request, but custom
# HTTP routes (/chat, /auth, /admin) do not enter that lifespan. Starlette's
# app lifespan only starts the StreamableHTTP session manager. Without an
# explicit HTTP-startup (or chat) ensure, TOOL_UPSTREAM_URL stays empty and
# the chat agent silently sends the LLM no tools.
_gateway_runtime_ready = False
_runtime_lock: asyncio.Lock | None = None


def _get_runtime_lock() -> asyncio.Lock:
    global _runtime_lock
    if _runtime_lock is None:
        _runtime_lock = asyncio.Lock()
    return _runtime_lock


def _server_name_for_upstream_url(url: str) -> str:
    for upstream in load_enabled_upstream_configs():
        if upstream.url == url:
            return upstream.upstream_id
    for upstream in env_upstream_defaults():
        if upstream.url == url:
            return upstream.upstream_id
    return "upstream"


async def _discover_upstream_snapshot(
    upstream: UpstreamConfig,
) -> dict[str, Any]:
    try:
        tools = await discover_tools_at_url(
            upstream.url,
            auth_header=upstream.auth_header,
        )
        return {
            "upstream_id": upstream.upstream_id,
            "upstream_url": upstream.url,
            "status": "ok",
            "error": None,
            "tools": [
                {"name": tool.name, "description": tool.description or ""}
                for tool in tools
            ],
            "_raw_tools": tools,
        }
    except Exception as exc:
        error = format_upstream_error(upstream.url, exc)
        logger.warning(
            "Upstream %s (%s) unavailable: %s",
            upstream.upstream_id,
            upstream.url,
            error,
        )
        return {
            "upstream_id": upstream.upstream_id,
            "upstream_url": upstream.url,
            "status": "unavailable",
            "error": error,
            "tools": [],
            "_raw_tools": [],
        }


# Backward-compatible aliases for tests that patch server._call_upstream_tool etc.
_call_upstream_tool = call_upstream_tool
_format_upstream_error = format_upstream_error
_discover_tools_at_url = discover_tools_at_url

def _arg_model_from_schema(tool_name: str, schema: dict[str, Any]) -> type[ArgModelBase]:
    properties = schema.get("properties") or {}
    if not properties:
        return create_model(f"{tool_name}Arguments", __base__=ArgModelBase)

    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        if field_name in required:
            fields[field_name] = (Any, Field(...))
        else:
            default = field_schema.get("default")
            fields[field_name] = (Any, Field(default=default))

    return create_model(f"{tool_name}Arguments", __base__=ArgModelBase, **fields)


def _register_proxy_tool(
    upstream: UpstreamTool,
    upstream_url: str,
    *,
    owner: str | None = None,
) -> bool:
    tool_name = upstream.name
    if tool_name in RESERVED_TOOL_NAMES:
        return False

    owner_name = owner or _server_name_for_upstream_url(upstream_url)

    existing_owner = TOOL_OWNER.get(tool_name)
    if existing_owner is not None and existing_owner != owner_name:
        logger.warning(
            "Skipping duplicate tool %r from %s; already owned by %s",
            tool_name,
            owner_name,
            existing_owner,
        )
        return False
    if tool_name in mcp._tool_manager._tools and existing_owner is None:
        # Gateway-owned tool name collision
        if tool_name in RESERVED_TOOL_NAMES:
            return False
        logger.warning(
            "Skipping duplicate tool %r from %s; already registered via gateway",
            tool_name,
            upstream_url,
        )
        return False

    input_schema = dict(upstream.inputSchema)
    description = upstream.description or ""

    async def proxy_handler(**kwargs: Any) -> Any:
        if not is_tool_callable(tool_name):
            raise RuntimeError(
                f"Tool {tool_name!r} is not available "
                "(upstream disabled or removed)."
            )
        route_url = TOOL_UPSTREAM_URL.get(tool_name, upstream_url)
        audit_server = TOOL_SERVER_NAME.get(tool_name, owner_name)
        owner_key = TOOL_OWNER.get(tool_name, owner_name)
        auth_header = UPSTREAM_AUTH_HEADERS.get(owner_key)

        async def _invoke() -> Any:
            from auth.permissions import role_may_invoke_tool
            from auth.principal import get_gateway_principal

            principal = get_gateway_principal()
            if principal is None:
                raise RuntimeError("Authentication required.")
            if not role_may_invoke_tool(principal.role, tool_name):
                raise RuntimeError(
                    f"Tool {tool_name!r} requires administrator privileges."
                )
            try:
                return await call_upstream_tool(
                    route_url,
                    tool_name,
                    kwargs,
                    auth_header=auth_header,
                )
            except Exception as exc:
                raise RuntimeError(format_upstream_error(route_url, exc)) from None

        return await run_audited_tool(
            server_name=audit_server,
            tool_name=tool_name,
            operation=_invoke,
        )

    proxy_handler.__name__ = f"proxy_{tool_name}"

    arg_model = _arg_model_from_schema(tool_name, input_schema)
    fn_metadata = FuncMetadata(arg_model=arg_model)

    tool = Tool(
        fn=proxy_handler,
        name=tool_name,
        title=upstream.title,
        description=description,
        parameters=input_schema,
        fn_metadata=fn_metadata,
        is_async=True,
        context_kwarg=None,
        annotations=upstream.annotations,
        icons=upstream.icons,
        meta=upstream.meta,
    )
    mcp._tool_manager._tools[tool_name] = tool
    TOOL_UPSTREAM_URL[tool_name] = upstream_url
    TOOL_SERVER_NAME[tool_name] = owner_name
    TOOL_OWNER[tool_name] = owner_name
    return True


async def _register_upstream_proxies() -> None:
    await refresh_upstream_tools(
        mcp,
        reserved_tool_names=RESERVED_TOOL_NAMES,
        discover_snapshot=_discover_upstream_snapshot,
        register_proxy_tool=_register_proxy_tool,
    )


async def ensure_gateway_runtime_ready() -> None:
    """
    Idempotent DB + upstream proxy registration for HTTP and MCP entrypoints.

    Must run before chat builds tool definitions; dashboard /chat never hits /mcp.
    """
    global _gateway_runtime_ready
    if _gateway_runtime_ready:
        return
    async with _get_runtime_lock():
        if _gateway_runtime_ready:
            return
        validate_mcp_auth_startup()
        startup_database()
        seed_default_servers_if_empty()
        await _register_upstream_proxies()
        _gateway_runtime_ready = True
        logger.info(
            "Gateway runtime ready; proxied_tools=%s",
            sorted(TOOL_UPSTREAM_URL.keys()),
        )


async def _on_servers_changed() -> None:
    """Refresh proxied tools after admin create/update/delete."""
    # Ensure DB/auth startup happened even if no /mcp request ran yet.
    await ensure_gateway_runtime_ready()
    await _register_upstream_proxies()


@asynccontextmanager
async def gateway_lifespan(_server: FastMCP):
    """
    Process-level gateway setup reused across stateless MCP requests.

    FastMCP with stateless_http=True enters this lifespan on each /mcp request.
    Startup is therefore idempotent; DB disposal happens via atexit on process exit.
    """
    await ensure_gateway_runtime_ready()
    yield {}


atexit.register(shutdown_database)


mcp = FastMCP(
    "Urumi Gateway",
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
    lifespan=gateway_lifespan,
    **fastmcp_auth_kwargs(),
)


@mcp.tool()
async def gateway_health() -> dict[str, Any]:
    """Diagnostic check that the gateway MCP server is running."""

    async def _invoke() -> dict[str, Any]:
        return {"status": "ok"}

    return await run_audited_tool(
        server_name=GATEWAY_SERVER_NAME,
        tool_name="gateway_health",
        operation=_invoke,
    )


@mcp.tool()
async def list_upstream_tools() -> dict[str, Any]:
    """List tool names and descriptions exposed by the WooCommerce MCP server."""

    async def _invoke() -> dict[str, Any]:
        configs = load_enabled_upstream_configs()
        woo = next((c for c in configs if c.upstream_id == "woocommerce"), None)
        if woo is None:
            # Fall back to env URL only for listing when woocommerce is disabled.
            url = WOOCOMMERCE_MCP_URL
            auth = None
        else:
            url = woo.url
            auth = woo.auth_header
        try:
            tools = await _discover_tools_at_url(url, auth_header=auth)
        except Exception as exc:
            raise RuntimeError(_format_upstream_error(url, exc)) from None

        return {
            "upstream_url": url,
            "tool_count": len(tools),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                }
                for tool in tools
            ],
        }

    return await run_audited_tool(
        server_name=GATEWAY_SERVER_NAME,
        tool_name="list_upstream_tools",
        operation=_invoke,
    )


@mcp.tool()
async def list_all_upstream_tools() -> dict[str, Any]:
    """List discovery status and tools for every configured upstream MCP server."""

    async def _invoke() -> dict[str, Any]:
        upstreams: list[dict[str, Any]] = []
        for upstream in load_enabled_upstream_configs():
            snapshot = await _discover_upstream_snapshot(upstream)
            upstreams.append(
                {
                    "upstream_id": snapshot["upstream_id"],
                    "upstream_url": snapshot["upstream_url"],
                    "status": snapshot["status"],
                    "error": snapshot["error"],
                    "tool_count": len(snapshot["tools"]),
                    "tools": snapshot["tools"],
                }
            )
        return {"upstreams": upstreams}

    return await run_audited_tool(
        server_name=GATEWAY_SERVER_NAME,
        tool_name="list_all_upstream_tools",
        operation=_invoke,
    )


from admin.servers_http import register_admin_server_routes
from admin.users_http import register_admin_user_routes
from audit.http import register_audit_routes
from auth.cors import apply_cors
from auth.http import register_auth_routes
from chat.http import register_chat_routes

register_auth_routes(mcp)
register_admin_server_routes(mcp, on_servers_changed=_on_servers_changed)
register_admin_user_routes(mcp)
register_audit_routes(mcp)
register_chat_routes(mcp, ensure_runtime_ready=ensure_gateway_runtime_ready)


def create_http_app() -> Starlette:
    """ASGI app with CORS; registers upstream tools on process startup."""
    app = apply_cors(mcp.streamable_http_app())
    session_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_: Starlette):
        async with session_lifespan(app_):
            await ensure_gateway_runtime_ready()
            yield

    app.router.lifespan_context = lifespan
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_http_app(), host=HOST, port=PORT)
