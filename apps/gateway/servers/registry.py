"""
In-process upstream MCP tool registry driven by RegisteredServer rows.

Tool-refresh behavior
---------------------
``refresh_upstream_tools`` loads enabled registered servers (seeding env defaults
into the DB when the table is empty), discovers tools per upstream independently
(one failure does not block others), unregisters tools owned by disabled/deleted
servers, and registers/updates proxies for healthy enabled upstreams.

``TOOL_OWNER`` maps each proxied tool name to its owning server ``name`` so
disable/delete can remove the correct tools.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func, select

from db.lifecycle import is_database_configured
from db.session import get_session_factory
from models.registered_server import RegisteredServer
from servers.credentials import decrypt_authorization_header

logger = logging.getLogger(__name__)

# tool_name -> owning registered server name
TOOL_OWNER: dict[str, str] = {}
# tool_name -> upstream MCP URL
TOOL_UPSTREAM_URL: dict[str, str] = {}
# tool_name -> audit/server label
TOOL_SERVER_NAME: dict[str, str] = {}
# server name -> decrypted Authorization header (process memory only)
UPSTREAM_AUTH_HEADERS: dict[str, str | None] = {}


@dataclass(frozen=True)
class UpstreamConfig:
    upstream_id: str
    url: str
    auth_header: str | None = None


def env_upstream_defaults() -> tuple[UpstreamConfig, ...]:
    return (
        UpstreamConfig(
            "woocommerce",
            os.getenv("WOOCOMMERCE_MCP_URL", "http://127.0.0.1:8000/mcp").strip(),
        ),
        UpstreamConfig(
            "inventory",
            os.getenv("INVENTORY_MCP_URL", "http://127.0.0.1:8002/mcp").strip(),
        ),
        UpstreamConfig(
            "shipping",
            os.getenv("SHIPPING_MCP_URL", "http://127.0.0.1:8003/mcp").strip(),
        ),
    )


def seed_default_servers_if_empty() -> None:
    """When the table is empty, insert env-based defaults so admin can manage them."""
    if not is_database_configured():
        return
    session = get_session_factory()()
    try:
        count = session.scalar(select(func.count()).select_from(RegisteredServer)) or 0
        if count > 0:
            return
        for cfg in env_upstream_defaults():
            session.add(
                RegisteredServer(
                    name=cfg.upstream_id,
                    server_type="mcp",
                    endpoint_url=cfg.url,
                    is_enabled=True,
                )
            )
        session.commit()
        logger.info("Seeded default registered MCP servers from environment URLs.")
    except Exception:
        session.rollback()
        logger.exception("Failed to seed default registered servers")
    finally:
        session.close()


def load_enabled_upstream_configs() -> list[UpstreamConfig]:
    """
    Return enabled upstreams for discovery/proxying.

    Prefer DB registered servers when the database is configured. If the table is
    empty or DB is unavailable, fall back to environment defaults.
    """
    if not is_database_configured():
        return list(env_upstream_defaults())

    seed_default_servers_if_empty()

    session = get_session_factory()()
    try:
        rows = list(
            session.scalars(
                select(RegisteredServer).where(RegisteredServer.is_enabled.is_(True))
            ).all()
        )
        if not rows:
            # Table may exist but all disabled — do not fall back to env.
            all_count = session.scalar(
                select(func.count()).select_from(RegisteredServer)
            ) or 0
            if all_count > 0:
                return []
            return list(env_upstream_defaults())

        configs: list[UpstreamConfig] = []
        for row in rows:
            configs.append(
                UpstreamConfig(
                    upstream_id=row.name,
                    url=row.endpoint_url,
                    auth_header=decrypt_authorization_header(row.auth_header_ciphertext),
                )
            )
        return configs
    except Exception:
        logger.exception(
            "Failed to load registered servers; falling back to environment upstreams"
        )
        return list(env_upstream_defaults())
    finally:
        session.close()


def unregister_tool(mcp: Any, tool_name: str) -> None:
    mcp._tool_manager._tools.pop(tool_name, None)
    TOOL_OWNER.pop(tool_name, None)
    TOOL_UPSTREAM_URL.pop(tool_name, None)
    TOOL_SERVER_NAME.pop(tool_name, None)


def unregister_tools_for_owner(mcp: Any, owner: str) -> int:
    removed = 0
    for tool_name, tool_owner in list(TOOL_OWNER.items()):
        if tool_owner == owner:
            unregister_tool(mcp, tool_name)
            removed += 1
    UPSTREAM_AUTH_HEADERS.pop(owner, None)
    return removed


def sync_auth_headers(configs: list[UpstreamConfig]) -> None:
    UPSTREAM_AUTH_HEADERS.clear()
    for cfg in configs:
        UPSTREAM_AUTH_HEADERS[cfg.upstream_id] = cfg.auth_header


def is_tool_callable(tool_name: str) -> bool:
    return tool_name in TOOL_UPSTREAM_URL and tool_name in TOOL_OWNER


async def refresh_upstream_tools(
    mcp: Any,
    *,
    reserved_tool_names: frozenset[str],
    discover_snapshot: Callable[[UpstreamConfig], Any],
    register_proxy_tool: Callable[..., bool],
) -> None:
    """
    Reconcile in-memory proxied tools with enabled registered servers.

    ``discover_snapshot`` and ``register_proxy_tool`` are injected from server.py
    to avoid circular imports while keeping registration logic colocated.
    """
    configs = load_enabled_upstream_configs()
    active_owners = {cfg.upstream_id for cfg in configs}
    sync_auth_headers(configs)

    # Drop tools for owners that are disabled, deleted, or no longer active.
    for tool_name, owner in list(TOOL_OWNER.items()):
        if owner not in active_owners:
            unregister_tool(mcp, tool_name)

    for cfg in configs:
        # Clear previous tools for this owner before re-registering so URL/schema
        # changes and removed upstream tools are reflected.
        unregister_tools_for_owner(mcp, cfg.upstream_id)
        UPSTREAM_AUTH_HEADERS[cfg.upstream_id] = cfg.auth_header

        try:
            snapshot = await discover_snapshot(cfg)
        except Exception as exc:
            logger.warning(
                "Upstream %s discovery raised unexpectedly: %s",
                cfg.upstream_id,
                type(exc).__name__,
            )
            continue

        registered = 0
        for tool in snapshot.get("_raw_tools") or []:
            if tool.name in reserved_tool_names:
                continue
            if register_proxy_tool(tool, cfg.url, owner=cfg.upstream_id):
                registered += 1

        logger.info(
            "Upstream %s (%s): status=%s registered=%d discovered=%d",
            cfg.upstream_id,
            cfg.url,
            snapshot.get("status"),
            registered,
            len(snapshot.get("tools") or []),
        )
