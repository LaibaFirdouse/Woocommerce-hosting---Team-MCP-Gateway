"""Tests for upstream MCP client error handling and tool-result materialization."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from servers.upstream import (
    call_upstream_tool,
    format_upstream_error,
    materialize_tool_result,
    safe_tool_error_message,
    _unwrap_exception_group,
)


def test_unwrap_exception_group_prefers_nested_runtime_error() -> None:
    inner = RuntimeError("Upstream tool failed: validation error for search")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert _unwrap_exception_group(group) is inner


def test_format_upstream_error_unwraps_exception_group() -> None:
    inner = RuntimeError(
        "Upstream tool failed: Error executing tool list_products: "
        "search Input should be a valid string"
    )
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    message = format_upstream_error("http://127.0.0.1:8000/mcp", group)
    assert "ExceptionGroup" not in message
    assert "list_products" in message
    assert "valid string" in message


def test_format_upstream_error_connect() -> None:
    message = format_upstream_error(
        "http://127.0.0.1:8000/mcp",
        httpx.ConnectError("refused"),
    )
    assert "Could not connect" in message
    assert "8000" in message


def test_safe_tool_error_and_materialize() -> None:
    err = SimpleNamespace(
        content=[SimpleNamespace(text="validation boom")],
        isError=True,
        structuredContent=None,
    )
    assert "validation boom" in safe_tool_error_message(err)

    ok = SimpleNamespace(
        content=[SimpleNamespace(text='{"result":[{"id":22}]}')],
        isError=False,
        structuredContent=None,
    )
    assert materialize_tool_result(ok) == {"result": [{"id": 22}]}


def test_call_upstream_tool_raises_tool_error_outside_session() -> None:
    """isError must become RuntimeError without ExceptionGroup wrapping."""

    class FakeResult:
        isError = True
        content = [SimpleNamespace(text="search must be a string")]
        structuredContent = None

    session = MagicMock()
    session.call_tool = AsyncMock(return_value=FakeResult())

    @asynccontextmanager
    async def session_cm(*_a, **_k):
        yield session

    with patch("servers.upstream.upstream_session", session_cm):
        with pytest.raises(RuntimeError, match="search must be a string") as info:
            asyncio.run(
                call_upstream_tool(
                    "http://127.0.0.1:8000/mcp",
                    "list_products",
                    {"search": 22},
                )
            )
        assert not isinstance(info.value, BaseExceptionGroup)
        assert "ExceptionGroup" not in str(info.value)


def test_omit_null_arguments_drops_none_keeps_values() -> None:
    from servers.upstream import omit_null_arguments

    assert omit_null_arguments(None) == {}
    assert omit_null_arguments({"a": None, "b": 1, "c": "", "d": 0}) == {
        "b": 1,
        "c": "",
        "d": 0,
    }


def test_call_upstream_tool_strips_null_optional_args() -> None:
    """Regression: LLM null optionals must not be forwarded to upstream schemas."""

    class FakeResult:
        isError = False
        content = [SimpleNamespace(text='{"result":[]}')]
        structuredContent = None

    session = MagicMock()
    session.call_tool = AsyncMock(return_value=FakeResult())

    @asynccontextmanager
    async def session_cm(*_a, **_k):
        yield session

    with patch("servers.upstream.upstream_session", session_cm):
        asyncio.run(
            call_upstream_tool(
                "http://127.0.0.1:8000/mcp",
                "list_products",
                {"search": None, "per_page": None, "page": 1},
            )
        )

    session.call_tool.assert_awaited_once_with("list_products", {"page": 1})


def test_call_upstream_tool_success() -> None:
    class FakeResult:
        isError = False
        content = [SimpleNamespace(text='{"result":[{"id":22,"name":"MCP Test Product"}]}')]
        structuredContent = None

    session = MagicMock()
    session.call_tool = AsyncMock(return_value=FakeResult())

    @asynccontextmanager
    async def session_cm(*_a, **_k):
        yield session

    with patch("servers.upstream.upstream_session", session_cm):
        result = asyncio.run(
            call_upstream_tool(
                "http://127.0.0.1:8000/mcp",
                "list_products",
                {},
            )
        )

    assert result["result"][0]["id"] == 22
    session.call_tool.assert_awaited_once_with("list_products", {})


def test_call_upstream_tool_flattens_session_exception_group() -> None:
    @asynccontextmanager
    async def boom_session(*_a, **_k):
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("Upstream tool failed: boom")],
        )
        yield  # pragma: no cover

    with patch("servers.upstream.upstream_session", boom_session):
        with pytest.raises(RuntimeError, match="boom") as info:
            asyncio.run(
                call_upstream_tool(
                    "http://127.0.0.1:8000/mcp",
                    "list_products",
                    {},
                )
            )
        assert "ExceptionGroup" not in str(info.value)
