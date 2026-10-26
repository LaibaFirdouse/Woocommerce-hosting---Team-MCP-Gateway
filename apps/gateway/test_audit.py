import asyncio
import sys
from unittest.mock import MagicMock

import httpx
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.types import Tool as UpstreamTool

from audit.service import (
    GATEWAY_SERVER_NAME,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    classify_outcome,
    record_tool_invocation,
    run_audited_tool,
    sanitize_error_message,
)
from models.audit_log import AuditLog
from models.user import UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


def _access_for_user(user_id: int, email: str) -> AccessToken:
    return AccessToken(
        token=f"jwt:{user_id}",
        client_id=str(user_id),
        scopes=[],
        subject=str(user_id),
        claims={
            "user_id": user_id,
            "email": email,
            "role": UserRole.member.value,
            "auth_method": "jwt",
        },
    )


def _set_principal(user_id: int, email: str):
    return auth_context_var.set(AuthenticatedUser(_access_for_user(user_id, email)))


def test_sanitize_error_message_redacts_credentials() -> None:
    raw = (
        "failed Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def "
        "and uk_pubid.super-secret-value-here"
    )
    cleaned = sanitize_error_message(raw)
    assert cleaned is not None
    assert "Bearer eyJ" not in cleaned
    assert "uk_pubid.super-secret" not in cleaned
    assert "[REDACTED]" in cleaned


def test_classify_outcome_timeout() -> None:
    assert classify_outcome(httpx.TimeoutException("timeout")) == OUTCOME_TIMEOUT
    assert classify_outcome(RuntimeError("Timed out after 20s")) == OUTCOME_TIMEOUT
    assert classify_outcome(RuntimeError("boom")) == OUTCOME_ERROR


def test_record_tool_invocation_success() -> None:
    session = MagicMock()
    added: list[AuditLog] = []
    session.add.side_effect = lambda entry: added.append(entry)

    ok = record_tool_invocation(
        user_id=7,
        server_name=GATEWAY_SERVER_NAME,
        tool_name="gateway_health",
        duration_ms=12,
        outcome=OUTCOME_SUCCESS,
        session_factory=lambda: session,
    )

    assert ok is True
    session.commit.assert_called_once()
    session.close.assert_called_once()
    assert len(added) == 1
    entry = added[0]
    assert entry.user_id == 7
    assert entry.server_name == GATEWAY_SERVER_NAME
    assert entry.tool_name == "gateway_health"
    assert entry.duration_ms == 12
    assert entry.outcome == OUTCOME_SUCCESS
    assert entry.error_message is None
    assert entry.timestamp is not None


def test_record_tool_invocation_returns_false_on_db_error() -> None:
    session = MagicMock()
    session.commit.side_effect = RuntimeError("disk full")

    ok = record_tool_invocation(
        user_id=1,
        server_name="woocommerce",
        tool_name="list_products",
        duration_ms=5,
        outcome=OUTCOME_SUCCESS,
        session_factory=lambda: session,
    )

    assert ok is False
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_run_audited_tool_success_records_principal() -> None:
    async def _run() -> None:
        session = MagicMock()
        added: list[AuditLog] = []
        session.add.side_effect = lambda entry: added.append(entry)

        token = _set_principal(11, "auditor@example.com")
        try:

            async def _op() -> dict:
                return {"status": "ok"}

            result = await run_audited_tool(
                server_name=GATEWAY_SERVER_NAME,
                tool_name="gateway_health",
                operation=_op,
                session_factory=lambda: session,
            )
        finally:
            auth_context_var.reset(token)

        assert result == {"status": "ok"}
        assert len(added) == 1
        assert added[0].user_id == 11
        assert added[0].outcome == OUTCOME_SUCCESS
        assert added[0].duration_ms is not None
        assert added[0].duration_ms >= 0
        assert added[0].error_message is None

    asyncio.run(_run())


def test_run_audited_tool_failure_records_error() -> None:
    async def _run() -> None:
        session = MagicMock()
        added: list[AuditLog] = []
        session.add.side_effect = lambda entry: added.append(entry)

        token = _set_principal(22, "fail@example.com")
        try:

            async def _op() -> None:
                raise RuntimeError("Upstream tool failed: stockout")

            with pytest.raises(RuntimeError, match="stockout"):
                await run_audited_tool(
                    server_name="inventory",
                    tool_name="get_inventory",
                    operation=_op,
                    session_factory=lambda: session,
                )
        finally:
            auth_context_var.reset(token)

        assert len(added) == 1
        assert added[0].user_id == 22
        assert added[0].server_name == "inventory"
        assert added[0].tool_name == "get_inventory"
        assert added[0].outcome == OUTCOME_ERROR
        assert added[0].duration_ms is not None
        assert added[0].error_message is not None
        assert "stockout" in added[0].error_message

    asyncio.run(_run())


def test_run_audited_tool_timeout_outcome() -> None:
    async def _run() -> None:
        session = MagicMock()
        added: list[AuditLog] = []
        session.add.side_effect = lambda entry: added.append(entry)

        token = _set_principal(33, "slow@example.com")
        try:

            async def _op() -> None:
                raise httpx.ReadTimeout("Timed out after 20s reaching upstream")

            with pytest.raises(httpx.ReadTimeout):
                await run_audited_tool(
                    server_name="shipping",
                    tool_name="get_shipping_status",
                    operation=_op,
                    session_factory=lambda: session,
                )
        finally:
            auth_context_var.reset(token)

        assert len(added) == 1
        assert added[0].user_id == 33
        assert added[0].outcome == OUTCOME_TIMEOUT
        assert added[0].duration_ms is not None

    asyncio.run(_run())


def test_run_audited_tool_never_trusts_tool_argument_user_id() -> None:
    async def _run() -> None:
        session = MagicMock()
        added: list[AuditLog] = []
        session.add.side_effect = lambda entry: added.append(entry)

        token = _set_principal(44, "real@example.com")
        try:

            async def _op(*, user_id: int = 999) -> dict:
                return {"claimed": user_id}

            result = await run_audited_tool(
                server_name=GATEWAY_SERVER_NAME,
                tool_name="gateway_health",
                operation=lambda: _op(user_id=999),
                session_factory=lambda: session,
            )
        finally:
            auth_context_var.reset(token)

        assert result == {"claimed": 999}
        assert added[0].user_id == 44

    asyncio.run(_run())


def test_run_audited_tool_redacts_secrets_in_error_audit() -> None:
    async def _run() -> None:
        session = MagicMock()
        added: list[AuditLog] = []
        session.add.side_effect = lambda entry: added.append(entry)

        secret = "uk_abc.this-is-the-raw-api-key-secret"
        jwt = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        token = _set_principal(55, "safe@example.com")
        try:

            async def _op() -> None:
                raise RuntimeError(f"upstream rejected {jwt} and {secret}")

            with pytest.raises(RuntimeError):
                await run_audited_tool(
                    server_name="woocommerce",
                    tool_name="list_orders",
                    operation=_op,
                    session_factory=lambda: session,
                )
        finally:
            auth_context_var.reset(token)

        assert len(added) == 1
        msg = added[0].error_message or ""
        assert secret not in msg
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in msg
        assert "[REDACTED]" in msg

    asyncio.run(_run())


def test_concurrent_audited_calls_keep_distinct_users() -> None:
    async def _run() -> None:
        records: list[AuditLog] = []

        def session_factory() -> MagicMock:
            session = MagicMock()
            session.add.side_effect = lambda entry: records.append(entry)
            return session

        async def call_as(user_id: int, email: str) -> int:
            reset = _set_principal(user_id, email)
            try:

                async def _op() -> int:
                    await asyncio.sleep(0.02)
                    return user_id

                return await run_audited_tool(
                    server_name=GATEWAY_SERVER_NAME,
                    tool_name="gateway_health",
                    operation=_op,
                    session_factory=session_factory,
                )
            finally:
                auth_context_var.reset(reset)

        results = await asyncio.gather(
            call_as(1, "a@example.com"),
            call_as(2, "b@example.com"),
        )
        assert results == [1, 2]
        user_ids = sorted(r.user_id for r in records)
        assert user_ids == [1, 2]
        assert all(r.outcome == OUTCOME_SUCCESS for r in records)
        assert all(r.duration_ms is not None for r in records)

    asyncio.run(_run())


def test_upstream_server_name_mapping_and_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    sys.modules.pop("server", None)
    import server as server_mod

    assert (
        server_mod._server_name_for_upstream_url("http://127.0.0.1:8000/mcp")
        == "woocommerce"
    )
    assert (
        server_mod._server_name_for_upstream_url("http://127.0.0.1:8002/mcp")
        == "inventory"
    )
    assert (
        server_mod._server_name_for_upstream_url("http://127.0.0.1:8003/mcp")
        == "shipping"
    )

    name = "audit_probe_unique_tool"
    server_mod.TOOL_UPSTREAM_URL.pop(name, None)
    server_mod.TOOL_SERVER_NAME.pop(name, None)
    server_mod.mcp._tool_manager._tools.pop(name, None)

    upstream = UpstreamTool(
        name=name,
        inputSchema={"type": "object", "properties": {}},
    )
    assert server_mod._register_proxy_tool(upstream, "http://127.0.0.1:8002/mcp") is True
    assert server_mod.TOOL_SERVER_NAME[name] == "inventory"

    server_mod.TOOL_UPSTREAM_URL.pop(name, None)
    server_mod.TOOL_SERVER_NAME.pop(name, None)
    server_mod.mcp._tool_manager._tools.pop(name, None)
    sys.modules.pop("server", None)
