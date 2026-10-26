"""Tests for /admin/users member provisioning."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from auth.tokens import create_access_token
from models.user import User, UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


@pytest.fixture(scope="module")
def admin_users_client():
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


def _user(*, user_id: int = 1, role: UserRole = UserRole.admin) -> User:
    return User(
        id=user_id,
        email="admin@example.com" if role is UserRole.admin else "m@example.com",
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


def test_member_cannot_list_users(admin_users_client) -> None:
    client, _ = admin_users_client
    member = _user(user_id=2, role=UserRole.member)
    with patch("admin.servers_http._authenticated_user", return_value=member):
        response = client.get("/admin/users", headers=_headers(member))
    assert response.status_code == 403


def test_admin_lists_and_creates_member(admin_users_client) -> None:
    client, _ = admin_users_client
    admin = _user(user_id=1, role=UserRole.admin)
    created = User(
        id=9,
        email="new.member@example.com",
        password_hash="hashed",
        role=UserRole.member,
        is_active=True,
    )

    session = MagicMock()
    session.scalars.return_value.all.return_value = [admin, created]

    with (
        patch("admin.servers_http._authenticated_user", return_value=admin),
        patch("admin.users_http._open_db_session", return_value=session),
        patch("admin.users_http.create_user", return_value=created) as mock_create,
    ):
        listed = client.get("/admin/users", headers=_headers(admin))
        assert listed.status_code == 200
        assert len(listed.json()["users"]) == 2

        created_resp = client.post(
            "/admin/users",
            headers=_headers(admin),
            json={
                "email": "new.member@example.com",
                "password": "Demo-Password-123!",
                "role": "admin",  # ignored — members only
            },
        )
    assert created_resp.status_code == 201
    assert created_resp.json()["user"]["role"] == "member"
    assert mock_create.call_args.kwargs.get("role") is UserRole.member
    assert mock_create.call_args.kwargs.get("allow_admin_role") is False
