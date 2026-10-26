"""Tests for GET /audit-logs and list_audit_logs authorization."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from audit.service import (
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    list_audit_logs,
)
from auth.exceptions import ForbiddenError, ValidationError
from auth.tokens import create_access_token
from models.audit_log import AuditLog
from models.user import User, UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


@pytest.fixture(scope="module")
def audit_http_client():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")

    async def noop_upstream() -> None:
        return

    sys.modules.pop("server", None)
    try:
        import server as server_mod

        with (
            patch.object(server_mod, "_register_upstream_proxies", noop_upstream),
            patch.object(server_mod, "startup_database"),
            patch.object(server_mod, "shutdown_database"),
        ):
            with TestClient(server_mod.mcp.streamable_http_app()) as client:
                yield client
    finally:
        monkeypatch.undo()
        sys.modules.pop("server", None)


def _user(*, user_id: int, email: str, role: UserRole) -> User:
    return User(
        id=user_id,
        email=email,
        password_hash="hashed",
        role=role,
        is_active=True,
    )


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


def _row(
    *,
    row_id: int,
    user_id: int | None,
    tool_name: str = "gateway_health",
    outcome: str = OUTCOME_SUCCESS,
    minutes_ago: int = 0,
    error_message: str | None = None,
) -> AuditLog:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return AuditLog(
        id=row_id,
        user_id=user_id,
        server_name="gateway",
        tool_name=tool_name,
        timestamp=ts,
        duration_ms=10 + row_id,
        outcome=outcome,
        error_message=error_message,
    )


@pytest.fixture()
def audit_db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AuditLog.__table__.create(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(session: Session, rows: list[AuditLog]) -> None:
    for row in rows:
        session.add(row)
    session.commit()


# --- Service-level authorization / filters (isolated SQLite) ---


def test_list_member_sees_only_own_rows(audit_db_session: Session) -> None:
    member = _user(user_id=1, email="m@example.com", role=UserRole.member)
    _seed(
        audit_db_session,
        [
            _row(row_id=1, user_id=1, tool_name="mine"),
            _row(row_id=2, user_id=2, tool_name="theirs"),
            _row(row_id=3, user_id=None, tool_name="orphan"),
        ],
    )

    rows, total = list_audit_logs(audit_db_session, actor=member)
    assert total == 1
    assert [r.tool_name for r in rows] == ["mine"]


def test_list_member_filter_user_id_forbidden(audit_db_session: Session) -> None:
    member = _user(user_id=1, email="m@example.com", role=UserRole.member)
    with pytest.raises(ForbiddenError):
        list_audit_logs(audit_db_session, actor=member, filter_user_id=1)


def test_list_admin_sees_all_including_null_user(audit_db_session: Session) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    _seed(
        audit_db_session,
        [
            _row(row_id=1, user_id=1, minutes_ago=2),
            _row(row_id=2, user_id=None, minutes_ago=1),
            _row(row_id=3, user_id=2, minutes_ago=0),
        ],
    )

    rows, total = list_audit_logs(audit_db_session, actor=admin)
    assert total == 3
    assert [r.id for r in rows] == [3, 2, 1]


def test_list_admin_filter_by_user_id(audit_db_session: Session) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    _seed(
        audit_db_session,
        [
            _row(row_id=1, user_id=1),
            _row(row_id=2, user_id=2),
            _row(row_id=3, user_id=None),
        ],
    )

    rows, total = list_audit_logs(
        audit_db_session, actor=admin, filter_user_id=2
    )
    assert total == 1
    assert rows[0].user_id == 2


def test_list_outcome_filter_applies_to_total(audit_db_session: Session) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    _seed(
        audit_db_session,
        [
            _row(row_id=1, user_id=1, outcome=OUTCOME_SUCCESS),
            _row(row_id=2, user_id=1, outcome=OUTCOME_ERROR),
            _row(row_id=3, user_id=1, outcome=OUTCOME_TIMEOUT),
        ],
    )

    rows, total = list_audit_logs(
        audit_db_session, actor=admin, outcome=OUTCOME_ERROR
    )
    assert total == 1
    assert rows[0].outcome == OUTCOME_ERROR


def test_list_pagination_and_ordering(audit_db_session: Session) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(5):
        rows.append(
            AuditLog(
                id=i + 1,
                user_id=1,
                server_name="gateway",
                tool_name=f"t{i}",
                timestamp=base + timedelta(minutes=i),
                duration_ms=i,
                outcome=OUTCOME_SUCCESS,
                error_message=None,
            )
        )
    # Same timestamp, higher id should come first among ties.
    rows.append(
        AuditLog(
            id=100,
            user_id=1,
            server_name="gateway",
            tool_name="tie",
            timestamp=base + timedelta(minutes=4),
            duration_ms=0,
            outcome=OUTCOME_SUCCESS,
            error_message=None,
        )
    )
    _seed(audit_db_session, rows)

    page, total = list_audit_logs(
        audit_db_session, actor=admin, limit=2, offset=0
    )
    assert total == 6
    assert [r.id for r in page] == [100, 5]

    page2, _ = list_audit_logs(
        audit_db_session, actor=admin, limit=2, offset=2
    )
    assert [r.id for r in page2] == [4, 3]


def test_list_invalid_limit_offset_outcome(audit_db_session: Session) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    with pytest.raises(ValidationError):
        list_audit_logs(audit_db_session, actor=admin, limit=0)
    with pytest.raises(ValidationError):
        list_audit_logs(audit_db_session, actor=admin, limit=101)
    with pytest.raises(ValidationError):
        list_audit_logs(audit_db_session, actor=admin, offset=-1)
    with pytest.raises(ValidationError):
        list_audit_logs(audit_db_session, actor=admin, outcome="nope")


# --- HTTP route tests ---


def test_audit_logs_unauthenticated(audit_http_client: TestClient) -> None:
    response = audit_http_client.get("/audit-logs")
    assert response.status_code == 401


def test_audit_logs_api_key_bearer_rejected(audit_http_client: TestClient) -> None:
    response = audit_http_client.get(
        "/audit-logs",
        headers={"Authorization": "Bearer uk_abc123.secret-token-value"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated."


def test_audit_logs_member_sees_only_own_via_http(
    audit_http_client: TestClient,
) -> None:
    member = _user(user_id=1, email="m@example.com", role=UserRole.member)
    own = _row(row_id=1, user_id=1, tool_name="mine")

    with patch("audit.http._authenticated_user", return_value=member):
        with patch(
            "audit.http.list_audit_logs",
            return_value=([own], 1),
        ) as mock_list:
            response = audit_http_client.get(
                "/audit-logs", headers=_headers(member)
            )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["audit_logs"][0]["tool_name"] == "mine"
    assert body["audit_logs"][0]["user_id"] == 1
    assert mock_list.call_args.kwargs["actor"].id == 1
    assert mock_list.call_args.kwargs["filter_user_id"] is None


def test_audit_logs_member_user_id_query_forbidden(
    audit_http_client: TestClient,
) -> None:
    member = _user(user_id=1, email="m@example.com", role=UserRole.member)
    with patch("audit.http._authenticated_user", return_value=member):
        with patch("audit.http.list_audit_logs") as mock_list:
            response = audit_http_client.get(
                "/audit-logs?user_id=1", headers=_headers(member)
            )

    assert response.status_code == 403
    mock_list.assert_not_called()


def test_audit_logs_admin_lists_all_and_filters(
    audit_http_client: TestClient,
) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    orphan = _row(row_id=3, user_id=None, tool_name="orphan")
    other = _row(row_id=2, user_id=2, tool_name="other")

    with patch("audit.http._authenticated_user", return_value=admin):
        with patch(
            "audit.http.list_audit_logs",
            return_value=([other, orphan], 2),
        ) as mock_list:
            response = audit_http_client.get(
                "/audit-logs", headers=_headers(admin)
            )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {row["user_id"] for row in body["audit_logs"]} == {2, None}
    assert mock_list.call_args.kwargs["filter_user_id"] is None

    with patch("audit.http._authenticated_user", return_value=admin):
        with patch(
            "audit.http.list_audit_logs",
            return_value=([other], 1),
        ) as mock_list:
            response = audit_http_client.get(
                "/audit-logs?user_id=2", headers=_headers(admin)
            )

    assert response.status_code == 200
    assert mock_list.call_args.kwargs["filter_user_id"] == 2
    assert response.json()["audit_logs"][0]["user_id"] == 2


def test_audit_logs_outcome_and_pagination_query_params(
    audit_http_client: TestClient,
) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    with patch("audit.http._authenticated_user", return_value=admin):
        with patch(
            "audit.http.list_audit_logs",
            return_value=([], 0),
        ) as mock_list:
            response = audit_http_client.get(
                "/audit-logs?limit=10&offset=20&outcome=timeout",
                headers=_headers(admin),
            )

    assert response.status_code == 200
    assert response.json() == {
        "audit_logs": [],
        "limit": 10,
        "offset": 20,
        "total": 0,
    }
    assert mock_list.call_args.kwargs["limit"] == 10
    assert mock_list.call_args.kwargs["offset"] == 20
    assert mock_list.call_args.kwargs["outcome"] == "timeout"


def test_audit_logs_invalid_query_params(audit_http_client: TestClient) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    with patch("audit.http._authenticated_user", return_value=admin):
        assert (
            audit_http_client.get(
                "/audit-logs?limit=abc", headers=_headers(admin)
            ).status_code
            == 400
        )
        assert (
            audit_http_client.get(
                "/audit-logs?offset=-1", headers=_headers(admin)
            ).status_code
            == 400
        )
        assert (
            audit_http_client.get(
                "/audit-logs?outcome=nope", headers=_headers(admin)
            ).status_code
            == 400
        )
        assert (
            audit_http_client.get(
                "/audit-logs?limit=0", headers=_headers(admin)
            ).status_code
            == 400
        )


def test_audit_logs_response_excludes_sensitive_fields(
    audit_http_client: TestClient,
) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    dirty = _row(
        row_id=1,
        user_id=1,
        error_message="failed Authorization: Bearer eyJhbGciOi.secret uk_x.y",
    )
    # Simulate ORM row that somehow also carried extra attrs (must not leak).
    dirty.auth_header_ciphertext = "cipher"  # type: ignore[attr-defined]
    dirty.key_hash = "hash"  # type: ignore[attr-defined]
    dirty.raw_key = "uk_x.y"  # type: ignore[attr-defined]

    with patch("audit.http._authenticated_user", return_value=admin):
        with patch(
            "audit.http.list_audit_logs",
            return_value=([dirty], 1),
        ):
            response = audit_http_client.get(
                "/audit-logs", headers=_headers(admin)
            )

    assert response.status_code == 200
    entry = response.json()["audit_logs"][0]
    assert set(entry.keys()) == {
        "id",
        "user_id",
        "server_name",
        "tool_name",
        "timestamp",
        "duration_ms",
        "outcome",
        "error_message",
    }
    assert "cipher" not in response.text
    assert "key_hash" not in response.text
    assert "raw_key" not in response.text
    assert "Bearer eyJ" not in entry["error_message"]
    assert "[REDACTED]" in entry["error_message"]


def test_audit_logs_database_failure_safe_error(
    audit_http_client: TestClient,
) -> None:
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    with patch("audit.http._authenticated_user", return_value=admin):
        with patch(
            "audit.http.list_audit_logs",
            side_effect=RuntimeError("connection refused to host db.internal"),
        ):
            response = audit_http_client.get(
                "/audit-logs", headers=_headers(admin)
            )

    assert response.status_code == 503
    assert response.json()["detail"] == "Unable to load audit logs."
    assert "db.internal" not in response.text


def test_audit_logs_uses_open_db_session(audit_http_client: TestClient) -> None:
    """End-to-end wiring: auth → session → list_audit_logs → close."""
    admin = _user(user_id=9, email="a@example.com", role=UserRole.admin)
    session = MagicMock()
    with patch("audit.http._authenticated_user", return_value=admin):
        with patch(
            "audit.http._open_db_session", return_value=session
        ) as open_session:
            with patch(
                "audit.http.list_audit_logs",
                return_value=([], 0),
            ) as mock_list:
                response = audit_http_client.get(
                    "/audit-logs", headers=_headers(admin)
                )

    assert response.status_code == 200
    open_session.assert_called_once()
    mock_list.assert_called_once()
    assert mock_list.call_args.args[0] is session
    session.close.assert_called_once()
