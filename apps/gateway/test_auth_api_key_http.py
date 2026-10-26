"""HTTP tests for /auth/api-keys management routes."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from auth.exceptions import ForbiddenError, NotFoundError, ValidationError
from auth.passwords import hash_password
from auth.tokens import create_access_token
from models.api_key import ApiKey
from models.user import User, UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


@pytest.fixture(scope="module")
def api_key_http_client():
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


def _member(user_id: int = 1, email: str = "member@example.com") -> User:
    return User(
        id=user_id,
        email=email,
        password_hash="hashed-not-plaintext",
        role=UserRole.member,
        is_active=True,
    )


def _auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


def _sample_key(
    *,
    key_id: int = 10,
    user_id: int = 1,
    name: str = "ci",
    prefix: str = "uk_abcdef",
    revoked: bool = False,
) -> ApiKey:
    now = datetime.now(timezone.utc)
    return ApiKey(
        id=key_id,
        user_id=user_id,
        name=name,
        key_prefix=prefix,
        key_hash=hash_password("uk_abcdef." + ("x" * 40)),
        created_at=now,
        last_used_at=None,
        revoked_at=now if revoked else None,
    )


def test_create_api_key_unauthenticated(api_key_http_client: TestClient) -> None:
    response = api_key_http_client.post("/auth/api-keys", json={"name": "ci"})
    assert response.status_code == 401


def test_list_api_keys_unauthenticated(api_key_http_client: TestClient) -> None:
    response = api_key_http_client.get("/auth/api-keys")
    assert response.status_code == 401


def test_revoke_api_key_unauthenticated(api_key_http_client: TestClient) -> None:
    response = api_key_http_client.delete("/auth/api-keys/1")
    assert response.status_code == 401


def test_create_list_revoke_flow(api_key_http_client: TestClient) -> None:
    user = _member()
    record = _sample_key()
    raw_key = "uk_abcdef.once-only-secret-value-not-stored"

    with patch("auth.http._session_from_token", return_value=user):
        with patch(
            "auth.http.create_api_key",
            return_value=(record, raw_key),
        ) as mock_create:
            with patch(
                "auth.http.get_session_factory",
                return_value=MagicMock(return_value=MagicMock()),
            ):
                create_resp = api_key_http_client.post(
                    "/auth/api-keys",
                    headers=_auth_headers(user),
                    json={"name": "ci", "user_id": 999},
                )

    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["raw_key"] == raw_key
    assert body["api_key"]["id"] == 10
    assert body["api_key"]["name"] == "ci"
    assert body["api_key"]["key_prefix"] == "uk_abcdef"
    assert body["api_key"]["revoked"] is False
    assert "key_hash" not in create_resp.text
    assert "password_hash" not in create_resp.text
    # Ownership comes from JWT, not body user_id.
    assert mock_create.call_args.args[1] is user

    listed = [_sample_key()]
    with patch("auth.http._session_from_token", return_value=user):
        with patch("auth.http.list_api_keys", return_value=listed):
            with patch(
                "auth.http.get_session_factory",
                return_value=MagicMock(return_value=MagicMock()),
            ):
                list_resp = api_key_http_client.get(
                    "/auth/api-keys",
                    headers=_auth_headers(user),
                )

    assert list_resp.status_code == 200
    keys = list_resp.json()["api_keys"]
    assert len(keys) == 1
    assert keys[0]["id"] == 10
    assert keys[0]["key_prefix"] == "uk_abcdef"
    assert "raw_key" not in list_resp.text
    assert "key_hash" not in list_resp.text
    assert raw_key not in list_resp.text

    revoked = _sample_key(revoked=True)
    with patch("auth.http._session_from_token", return_value=user):
        with patch("auth.http.revoke_api_key", return_value=revoked) as mock_revoke:
            with patch(
                "auth.http.get_session_factory",
                return_value=MagicMock(return_value=MagicMock()),
            ):
                revoke_resp = api_key_http_client.delete(
                    "/auth/api-keys/10",
                    headers=_auth_headers(user),
                )

    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["api_key"]["revoked"] is True
    assert revoke_resp.json()["api_key"]["revoked_at"] is not None
    assert mock_revoke.call_args.kwargs.get("as_admin") is False
    assert "key_hash" not in revoke_resp.text
    assert raw_key not in revoke_resp.text


def test_create_api_key_validation_error(api_key_http_client: TestClient) -> None:
    user = _member()
    with patch("auth.http._session_from_token", return_value=user):
        with patch(
            "auth.http.create_api_key",
            side_effect=ValidationError("API key name is required."),
        ):
            with patch(
                "auth.http.get_session_factory",
                return_value=MagicMock(return_value=MagicMock()),
            ):
                response = api_key_http_client.post(
                    "/auth/api-keys",
                    headers=_auth_headers(user),
                    json={"name": "   "},
                )

    assert response.status_code == 400
    assert "name" in response.json()["detail"].lower()


def test_revoke_missing_key(api_key_http_client: TestClient) -> None:
    user = _member()
    with patch("auth.http._session_from_token", return_value=user):
        with patch(
            "auth.http.revoke_api_key",
            side_effect=NotFoundError("API key not found."),
        ):
            with patch(
                "auth.http.get_session_factory",
                return_value=MagicMock(return_value=MagicMock()),
            ):
                response = api_key_http_client.delete(
                    "/auth/api-keys/404",
                    headers=_auth_headers(user),
                )

    assert response.status_code == 404


def test_revoke_cross_user_denied(api_key_http_client: TestClient) -> None:
    user = _member(user_id=1)
    with patch("auth.http._session_from_token", return_value=user):
        with patch(
            "auth.http.revoke_api_key",
            side_effect=ForbiddenError("You cannot revoke this API key."),
        ):
            with patch(
                "auth.http.get_session_factory",
                return_value=MagicMock(return_value=MagicMock()),
            ):
                response = api_key_http_client.delete(
                    "/auth/api-keys/99",
                    headers=_auth_headers(user),
                )

    assert response.status_code == 403
    assert "cannot revoke" in response.json()["detail"]


def test_revoke_invalid_key_id(api_key_http_client: TestClient) -> None:
    user = _member()
    with patch("auth.http._session_from_token", return_value=user):
        response = api_key_http_client.delete(
            "/auth/api-keys/not-an-int",
            headers=_auth_headers(user),
        )
    assert response.status_code == 400


def test_api_key_bearer_cannot_manage_keys(api_key_http_client: TestClient) -> None:
    response = api_key_http_client.post(
        "/auth/api-keys",
        headers={"Authorization": "Bearer uk_prefix.secret-value-here"},
        json={"name": "ci"},
    )
    assert response.status_code == 401


def test_revoked_key_rejected_by_verify() -> None:
    """Revoked keys must not authenticate (service-layer guarantee)."""
    from auth.api_keys import verify_api_key

    user = _member()
    raw = "uk_revoked." + ("s" * 40)
    api_key = ApiKey(
        id=1,
        user_id=1,
        name="old",
        key_prefix="uk_revoked",
        key_hash=hash_password(raw),
        revoked_at=datetime.now(timezone.utc),
        user=user,
    )
    session = MagicMock()
    session.scalar.return_value = api_key
    assert verify_api_key(session, raw) is None
    session.commit.assert_not_called()


def test_list_api_keys_service_filters_by_owner() -> None:
    from auth.api_keys import list_api_keys

    user = _member(user_id=5)
    session = MagicMock()
    own = [_sample_key(key_id=1, user_id=5)]
    session.scalars.return_value.all.return_value = own

    result = list_api_keys(session, user)
    assert result == own
    session.scalars.assert_called_once()
