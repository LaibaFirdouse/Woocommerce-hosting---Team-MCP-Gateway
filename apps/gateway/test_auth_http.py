import sys
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from models.user import User, UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


@pytest.fixture(scope="module")
def auth_client():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    # HTTP auth route tests are independent of MCP transport protection.
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


def _member_user(user_id: int = 1, email: str = "member@example.com") -> User:
    return User(
        id=user_id,
        email=email,
        password_hash="hashed-not-plaintext",
        role=UserRole.member,
        is_active=True,
    )


def test_register_success(auth_client: TestClient) -> None:
    user = _member_user()
    session = MagicMock()

    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch("auth.http.create_user", return_value=user) as mock_create:
            response = auth_client.post(
                "/auth/register",
                json={"email": "member@example.com", "password": "password123"},
            )

    assert response.status_code == 201
    assert response.json()["user"] == {
        "id": 1,
        "email": "member@example.com",
        "role": "member",
    }
    assert "password_hash" not in response.text
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs.get("allow_admin_role") is False
    assert mock_create.call_args.kwargs.get("role") is UserRole.member


def test_register_ignores_admin_role_in_body(auth_client: TestClient) -> None:
    user = _member_user()
    session = MagicMock()

    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch("auth.http.create_user", return_value=user) as mock_create:
            response = auth_client.post(
                "/auth/register",
                json={
                    "email": "member@example.com",
                    "password": "password123",
                    "role": "admin",
                },
            )

    assert response.status_code == 201
    assert mock_create.call_args.kwargs["role"] is UserRole.member


def test_register_duplicate_email(auth_client: TestClient) -> None:
    from auth.exceptions import DuplicateEmailError

    session = MagicMock()
    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch("auth.http.create_user", side_effect=DuplicateEmailError("dup")):
            response = auth_client.post(
                "/auth/register",
                json={"email": "dup@example.com", "password": "password123"},
            )

    assert response.status_code == 409


def test_register_validation_error(auth_client: TestClient) -> None:
    from auth.exceptions import ValidationError

    session = MagicMock()
    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch(
            "auth.http.create_user",
            side_effect=ValidationError("Password cannot be empty."),
        ):
            response = auth_client.post(
                "/auth/register",
                json={"email": "user@example.com", "password": ""},
            )

    assert response.status_code == 400


def test_login_success_sets_cookie(auth_client: TestClient) -> None:
    user = _member_user()
    session = MagicMock()

    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch("auth.http.authenticate_user", return_value=user):
            response = auth_client.post(
                "/auth/login",
                json={"email": "member@example.com", "password": "password123"},
            )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "member@example.com"
    assert "urumi_session" in response.cookies
    assert "password123" not in response.text
    assert "hashed-not-plaintext" not in response.text


def test_login_invalid_credentials(auth_client: TestClient) -> None:
    session = MagicMock()
    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch("auth.http.authenticate_user", return_value=None):
            response = auth_client.post(
                "/auth/login",
                json={"email": "member@example.com", "password": "wrong"},
            )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_me_requires_authentication(auth_client: TestClient) -> None:
    auth_client.cookies.clear()
    response = auth_client.get("/auth/me")
    assert response.status_code == 401


def test_me_returns_user(auth_client: TestClient) -> None:
    from auth.tokens import create_access_token

    user = _member_user()
    token = create_access_token(user_id=user.id, email=user.email, role="member")

    with patch("auth.http._session_from_token", return_value=user):
        response = auth_client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["user"] == {
        "id": 1,
        "email": "member@example.com",
        "role": "member",
    }


def test_login_cookie_authenticates_me_without_authorization_header(
    auth_client: TestClient,
) -> None:
    """Session cookie from login must be enough for GET /auth/me (no Bearer)."""
    user = _member_user()
    db_session = MagicMock()
    db_session.get.return_value = user

    with patch(
        "auth.http.get_session_factory",
        return_value=MagicMock(return_value=db_session),
    ):
        with patch("auth.http.authenticate_user", return_value=user):
            login = auth_client.post(
                "/auth/login",
                json={"email": "member@example.com", "password": "password123"},
            )

        assert login.status_code == 200
        assert "urumi_session" in auth_client.cookies

        # Cookie jar retained by TestClient; do not send Authorization.
        me = auth_client.get("/auth/me")

    assert me.status_code == 200
    assert me.json()["user"] == {
        "id": 1,
        "email": "member@example.com",
        "role": "member",
    }
    assert "Authorization" not in me.request.headers


def test_logout_clears_session_cookie(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/logout")
    assert response.status_code == 200
    assert response.json()["detail"] == "Logged out."
    # Starlette TestClient exposes Set-Cookie clearing via headers.
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "urumi_session" in set_cookie


def test_auth_endpoints_require_jwt_secret() -> None:
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    mp.delenv("AUTH_JWT_SECRET", raising=False)

    async def noop_upstream() -> None:
        return

    with patch("server._register_upstream_proxies", noop_upstream):
        with patch("server.startup_database"):
            with patch("server.shutdown_database"):
                from mcp.server.fastmcp import FastMCP

                isolated = FastMCP("auth-test-isolated")
                from auth.http import register_auth_routes

                register_auth_routes(isolated)
                with TestClient(isolated.streamable_http_app()) as client:
                    response = client.post(
                        "/auth/register",
                        json={"email": "a@example.com", "password": "password123"},
                    )
                    assert response.status_code == 503
    mp.undo()
