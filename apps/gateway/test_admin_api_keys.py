"""Tests for admin team API-key management routes."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from auth.tokens import create_access_token
from models.api_key import ApiKey
from models.user import User, UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


@pytest.fixture(scope="module")
def admin_keys_client():
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
                yield client, server_mod
    finally:
        monkeypatch.undo()
        sys.modules.pop("server", None)


def _user(*, user_id: int, role: UserRole, email: str) -> User:
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
    return {"Authorization": "Bearer " + token}


def _key(*, key_id: int, user: User, name: str = "demo") -> ApiKey:
    key = ApiKey(
        id=key_id,
        user_id=user.id,
        name=name,
        key_prefix=f"uk_prefix{key_id}",
        key_hash="hash",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        revoked_at=None,
    )
    key.user = user
    return key


def test_member_cannot_list_team_keys(admin_keys_client) -> None:
    client, _ = admin_keys_client
    member = _user(user_id=8, role=UserRole.member, email="user.b.member@urumi.local")
    with patch("admin.servers_http._authenticated_user", return_value=member):
        response = client.get("/admin/api-keys", headers=_headers(member))
    assert response.status_code == 403


def test_member_cannot_issue_key_for_another_user(admin_keys_client) -> None:
    client, _ = admin_keys_client
    member = _user(user_id=8, role=UserRole.member, email="user.b.member@urumi.local")
    with patch("admin.servers_http._authenticated_user", return_value=member):
        response = client.post(
            "/admin/users/7/api-keys",
            headers=_headers(member),
            json={"name": "stolen"},
        )
    assert response.status_code == 403


def test_admin_lists_keys_with_owners(admin_keys_client) -> None:
    client, _ = admin_keys_client
    admin = _user(user_id=7, role=UserRole.admin, email="user.a.admin@urumi.local")
    member = _user(user_id=8, role=UserRole.member, email="user.b.member@urumi.local")
    keys = [_key(key_id=1, user=admin, name="admin-key"), _key(key_id=2, user=member)]

    session = MagicMock()
    with (
        patch("admin.servers_http._authenticated_user", return_value=admin),
        patch("admin.users_http._open_db_session", return_value=session),
        patch("admin.users_http.list_all_api_keys", return_value=keys),
    ):
        response = client.get("/admin/api-keys", headers=_headers(admin))

    assert response.status_code == 200
    payload = response.json()["api_keys"]
    assert len(payload) == 2
    assert payload[0]["user_id"] == 7
    assert payload[0]["owner_email"] == admin.email
    assert payload[1]["user_id"] == 8
    assert "raw_key" not in payload[0]
    assert "key_hash" not in payload[0]


def test_admin_issues_key_for_member(admin_keys_client) -> None:
    client, _ = admin_keys_client
    admin = _user(user_id=7, role=UserRole.admin, email="user.a.admin@urumi.local")
    member = _user(user_id=8, role=UserRole.member, email="user.b.member@urumi.local")
    record = _key(key_id=12, user=member, name="for-b")

    session = MagicMock()
    session.get.return_value = member
    session.scalar.return_value = record

    with (
        patch("admin.servers_http._authenticated_user", return_value=admin),
        patch("admin.users_http._open_db_session", return_value=session),
        patch(
            "admin.users_http.create_api_key",
            return_value=(record, "uk_prefix12.secret-once"),
        ) as mock_create,
        patch("admin.users_http.record_tool_invocation", return_value=True) as mock_audit,
    ):
        response = client.post(
            "/admin/users/8/api-keys",
            headers=_headers(admin),
            json={"name": "for-b", "user_id": 999},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["raw_key"] == "uk_prefix12.secret-once"
    assert body["api_key"]["user_id"] == 8
    assert body["api_key"]["owner_email"] == member.email
    assert mock_create.call_args.args[1] is member
    assert mock_audit.call_args.kwargs["user_id"] == 7
    assert mock_audit.call_args.kwargs["tool_name"] == "admin_issue_api_key"
    assert "target_user_id=8" in mock_audit.call_args.kwargs["error_message"]


def test_admin_revokes_member_key(admin_keys_client) -> None:
    client, _ = admin_keys_client
    admin = _user(user_id=7, role=UserRole.admin, email="user.a.admin@urumi.local")
    member = _user(user_id=8, role=UserRole.member, email="user.b.member@urumi.local")
    record = _key(key_id=12, user=member, name="for-b")
    record.revoked_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    session = MagicMock()
    session.scalar.return_value = record

    with (
        patch("admin.servers_http._authenticated_user", return_value=admin),
        patch("admin.users_http._open_db_session", return_value=session),
        patch("admin.users_http.revoke_api_key", return_value=record) as mock_revoke,
        patch("admin.users_http.record_tool_invocation", return_value=True) as mock_audit,
    ):
        response = client.delete("/admin/api-keys/12", headers=_headers(admin))

    assert response.status_code == 200
    assert response.json()["api_key"]["revoked"] is True
    assert mock_revoke.call_args.kwargs.get("as_admin") is True
    assert mock_audit.call_args.kwargs["user_id"] == 7
    assert mock_audit.call_args.kwargs["tool_name"] == "admin_revoke_api_key"
    assert "target_user_id=8" in mock_audit.call_args.kwargs["error_message"]


def test_admin_can_get_servers(admin_keys_client) -> None:
    client, _ = admin_keys_client
    admin = _user(user_id=7, role=UserRole.admin, email="user.a.admin@urumi.local")
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    with (
        patch("admin.servers_http._authenticated_user", return_value=admin),
        patch("admin.servers_http._open_db_session", return_value=session),
    ):
        response = client.get("/admin/servers", headers=_headers(admin))
    assert response.status_code == 200
    assert response.json()["servers"] == []
