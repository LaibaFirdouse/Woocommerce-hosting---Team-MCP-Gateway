"""Shared upstream MCP session + tool call helpers (used by gateway proxies and chat)."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool as UpstreamTool

logger = logging.getLogger(__name__)

UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("GATEWAY_UPSTREAM_TIMEOUT", "20"))


def upstream_timeout() -> httpx.Timeout:
    return httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS)


def _unwrap_exception_group(exc: BaseException) -> BaseException:
    """
    Streamable HTTP client sessions use anyio TaskGroups; errors raised while
    the session context is open are often re-raised as ExceptionGroup on exit.
    Prefer the first nested Exception so callers see the real tool/transport
    failure instead of a bare ``ExceptionGroup`` label.
    """
    current: BaseException = exc
    seen: set[int] = set()
    while isinstance(current, BaseExceptionGroup):
        if id(current) in seen or not current.exceptions:
            break
        seen.add(id(current))
        current = current.exceptions[0]
    return current


def format_upstream_error(url: str, exc: Exception) -> str:
    """Return a client-safe message; never include secrets or response bodies."""
    root = _unwrap_exception_group(exc)
    if root is not exc and isinstance(root, Exception):
        return format_upstream_error(url, root)

    if isinstance(exc, httpx.ConnectError):
        return (
            f"Could not connect to upstream MCP at {url}. "
            "Check that the server is running and the URL is correct."
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Timed out after {UPSTREAM_TIMEOUT_SECONDS:g}s reaching upstream MCP "
            f"at {url}."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            f"Upstream MCP at {url} returned HTTP {exc.response.status_code}."
        )
    if isinstance(exc, RuntimeError):
        return str(exc)
    if isinstance(exc, BaseExceptionGroup):
        nested = _unwrap_exception_group(exc)
        if nested is not exc and isinstance(nested, Exception):
            return format_upstream_error(url, nested)
        return f"Failed to reach upstream MCP at {url}: ExceptionGroup."
    return f"Failed to reach upstream MCP at {url}: {type(exc).__name__}."


@asynccontextmanager
async def upstream_session(url: str, auth_header: str | None = None):
    headers: dict[str, str] = {}
    if auth_header:
        headers["Authorization"] = auth_header
    async with httpx.AsyncClient(
        timeout=upstream_timeout(),
        headers=headers or None,
    ) as http_client:
        async with streamable_http_client(
            url,
            http_client=http_client,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


def safe_tool_error_message(result: CallToolResult) -> str:
    parts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text.strip()[:300])
    if parts:
        return "Upstream tool failed: " + " ".join(parts)
    return "Upstream tool returned an error."


def materialize_tool_result(result: CallToolResult) -> Any:
    if result.structuredContent is not None:
        return result.structuredContent

    texts = [
        block.text
        for block in result.content
        if getattr(block, "text", None)
    ]
    if not texts:
        return None
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return texts


def omit_null_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """
    Drop keys whose value is ``None``.

    Local LLMs often emit explicit nulls for unused optional parameters
    (``per_page: null``). Many MCP/Pydantic schemas treat null as an invalid
    integer/string rather than "use the default". Omitting the key preserves
    schema defaults without tool-specific branches.
    """
    if not arguments:
        return {}
    return {key: value for key, value in arguments.items() if value is not None}


async def call_upstream_tool(
    upstream_url: str,
    name: str,
    arguments: dict[str, Any],
    *,
    auth_header: str | None = None,
) -> Any:
    """
    Call an upstream MCP tool and return materialized content.

    Tool-level failures (``isError``) are raised *after* the Streamable HTTP
    session closes so anyio does not wrap them as ExceptionGroup on context
    exit — that previously hid validation errors as
    ``Failed to reach upstream MCP …: ExceptionGroup``.
    """
    clean_arguments = omit_null_arguments(arguments)
    tool_error: str | None = None
    materialized: Any = None
    try:
        async with upstream_session(upstream_url, auth_header=auth_header) as session:
            result = await session.call_tool(name, clean_arguments)
            if result.isError:
                tool_error = safe_tool_error_message(result)
            else:
                materialized = materialize_tool_result(result)
    except BaseExceptionGroup as exc:
        nested = _unwrap_exception_group(exc)
        if isinstance(nested, Exception):
            raise RuntimeError(format_upstream_error(upstream_url, nested)) from None
        raise RuntimeError(format_upstream_error(upstream_url, Exception(str(exc)))) from None
    except Exception as exc:
        raise RuntimeError(format_upstream_error(upstream_url, exc)) from None

    if tool_error is not None:
        raise RuntimeError(tool_error)
    return materialized


async def discover_tools_at_url(
    url: str,
    auth_header: str | None = None,
) -> list[UpstreamTool]:
    async with upstream_session(url, auth_header=auth_header) as session:
        result = await session.list_tools()
        return list(result.tools)
