import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.types import Tool as UpstreamTool
from starlette.testclient import TestClient

from auth.api_keys import API_KEY_PREFIX
from auth.mcp_verifier import GatewayTokenVerifier
from auth.passwords import hash_password
from auth.principal import AuthMethod, GatewayPrincipal, get_gateway_principal
from auth.tokens import create_access_token
from models.api_key import ApiKey
from models.user import User, UserRole

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"

GATEWAY_TOOL_NAMES = (
    "gateway_health",
    "list_upstream_tools",
    "list_all_upstream_tools",
)

PROXY_TOOL_NAMES = (
    "list_products",
    "create_product",
    "list_orders",
    "get_order",
    "update_order_status",
    "get_inventory",
    "get_shipping_status",
)

EXPECTED_TOOL_NAMES = frozenset(GATEWAY_TOOL_NAMES + PROXY_TOOL_NAMES)


def _mcp_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _jsonrpc(method: str, request_id: int = 1, params: dict | None = None) -> dict:
    payload: dict = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return payload


def _initialize_params() -> dict:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    }


def _active_user(user_id: int = 1, email: str = "member@example.com") -> User:
    return User(
        id=user_id,
        email=email,
        password_hash=hash_password("password123"),
        role=UserRole.member,
        is_active=True,
    )


def _random_secret() -> str:
    import secrets

    return secrets.token_urlsafe(32)


async def _register_test_proxy_tools(server_mod) -> None:
    for name in PROXY_TOOL_NAMES:
        upstream = UpstreamTool(
            name=name,
            inputSchema={"type": "object", "properties": {}},
        )
        server_mod._register_proxy_tool(upstream, "http://127.0.0.1:9999/mcp")


@pytest.fixture(scope="module")
def mcp_auth_client():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8001")

    # Import after env is set so FastMCP receives auth kwargs.
    for mod_name in ("server",):
        sys.modules.pop(mod_name, None)

    import server as server_mod

    async def register_proxies() -> None:
        await _register_test_proxy_tools(server_mod)

    try:
        with (
            patch.object(server_mod, "_register_upstream_proxies", register_proxies),
            patch.object(server_mod, "startup_database"),
            patch.object(server_mod, "shutdown_database"),
        ):
            # Force one-shot startup path on first authenticated MCP request.
            server_mod._gateway_runtime_ready = False
            with TestClient(server_mod.mcp.streamable_http_app()) as client:
                yield client
    finally:
        monkeypatch.undo()
        sys.modules.pop("server", None)


def test_mcp_initialize_unauthenticated_returns_401(mcp_auth_client: TestClient) -> None:
    response = mcp_auth_client.post(
        "/mcp",
        headers=_mcp_headers(),
        json=_jsonrpc("initialize", params=_initialize_params()),
    )
    assert response.status_code == 401


def test_mcp_tools_list_unauthenticated_returns_401(mcp_auth_client: TestClient) -> None:
    response = mcp_auth_client.post(
        "/mcp",
        headers=_mcp_headers(),
        json=_jsonrpc("tools/list", params={}),
    )
    assert response.status_code == 401


def test_mcp_rejects_invalid_jwt(mcp_auth_client: TestClient) -> None:
    response = mcp_auth_client.post(
        "/mcp",
        headers=_mcp_headers("not-a-valid-jwt"),
        json=_jsonrpc("initialize", params=_initialize_params()),
    )
    assert response.status_code == 401


def test_mcp_accepts_valid_jwt(mcp_auth_client: TestClient) -> None:
    user = _active_user()
    token = create_access_token(user_id=user.id, email=user.email, role=user.role.value)
    session = MagicMock()
    session.get.return_value = user

    with patch(
        "auth.mcp_verifier.get_session_factory",
        return_value=MagicMock(return_value=session),
    ):
        response = mcp_auth_client.post(
            "/mcp",
            headers=_mcp_headers(token),
            json=_jsonrpc("initialize", params=_initialize_params()),
        )

    assert response.status_code == 200
    body = response.json()
    assert body.get("result", {}).get("serverInfo", {}).get("name") == "Urumi Gateway"


def test_mcp_rejects_inactive_user_jwt(mcp_auth_client: TestClient) -> None:
    user = _active_user()
    user.is_active = False
    token = create_access_token(user_id=user.id, email=user.email, role=user.role.value)
    session = MagicMock()
    session.get.return_value = user

    with patch(
        "auth.mcp_verifier.get_session_factory",
        return_value=MagicMock(return_value=session),
    ):
        response = mcp_auth_client.post(
            "/mcp",
            headers=_mcp_headers(token),
            json=_jsonrpc("tools/list", params={}),
        )

    assert response.status_code == 401


def test_mcp_rejects_invalid_api_key(mcp_auth_client: TestClient) -> None:
    session = MagicMock()
    session.scalar.return_value = None

    with patch(
        "auth.mcp_verifier.get_session_factory",
        return_value=MagicMock(return_value=session),
    ):
        response = mcp_auth_client.post(
            "/mcp",
            headers=_mcp_headers(f"{API_KEY_PREFIX}missing.{_random_secret()}"),
            json=_jsonrpc("initialize", params=_initialize_params()),
        )

    assert response.status_code == 401


def test_mcp_accepts_valid_api_key(mcp_auth_client: TestClient) -> None:
    user = _active_user()
    raw_key = f"{API_KEY_PREFIX}testpub.{_random_secret()}"
    api_key = ApiKey(
        id=1,
        user_id=user.id,
        name="test",
        key_prefix=f"{API_KEY_PREFIX}testpub",
        key_hash=hash_password(raw_key),
        user=user,
    )
    session = MagicMock()
    session.scalar.return_value = api_key

    with patch(
        "auth.mcp_verifier.get_session_factory",
        return_value=MagicMock(return_value=session),
    ):
        response = mcp_auth_client.post(
            "/mcp",
            headers=_mcp_headers(raw_key),
            json=_jsonrpc("initialize", params=_initialize_params()),
        )

    assert response.status_code == 200


def test_mcp_rejects_revoked_api_key(mcp_auth_client: TestClient) -> None:
    from datetime import datetime, timezone

    user = _active_user()
    raw_key = f"{API_KEY_PREFIX}revoked.{_random_secret()}"
    api_key = ApiKey(
        id=1,
        user_id=user.id,
        name="test",
        key_prefix=f"{API_KEY_PREFIX}revoked",
        key_hash=hash_password(raw_key),
        revoked_at=datetime.now(timezone.utc),
        user=user,
    )
    session = MagicMock()
    session.scalar.return_value = api_key

    with patch(
        "auth.mcp_verifier.get_session_factory",
        return_value=MagicMock(return_value=session),
    ):
        response = mcp_auth_client.post(
            "/mcp",
            headers=_mcp_headers(raw_key),
            json=_jsonrpc("tools/list", params={}),
        )

    assert response.status_code == 401


def test_auth_register_and_login_remain_public(mcp_auth_client: TestClient) -> None:
    user = _active_user()
    session = MagicMock()

    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch("auth.http.create_user", return_value=user):
            register = mcp_auth_client.post(
                "/auth/register",
                json={"email": "member@example.com", "password": "password123"},
            )
    assert register.status_code == 201

    with patch("auth.http.get_session_factory", return_value=MagicMock(return_value=session)):
        with patch("auth.http.authenticate_user", return_value=user):
            login = mcp_auth_client.post(
                "/auth/login",
                json={"email": "member@example.com", "password": "password123"},
            )
    assert login.status_code == 200
    assert login.cookies.get("urumi_session")


def test_authenticated_tools_list_exposes_ten_tools(mcp_auth_client: TestClient) -> None:
    user = _active_user()
    token = create_access_token(user_id=user.id, email=user.email, role=user.role.value)
    session = MagicMock()
    session.get.return_value = user

    with patch(
        "auth.mcp_verifier.get_session_factory",
        return_value=MagicMock(return_value=session),
    ):
        response = mcp_auth_client.post(
            "/mcp",
            headers=_mcp_headers(token),
            json=_jsonrpc("tools/list", params={}),
        )

    assert response.status_code == 200
    body = response.json()
    result = body.get("result") or {}
    tools = result.get("tools") or []
    names = {tool["name"] for tool in tools}
    assert names == EXPECTED_TOOL_NAMES


def test_concurrent_requests_do_not_leak_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Concurrent token verification + contextvar isolation.

    Uses one injected session factory (no overlapping module patches) so
    asyncio.to_thread workers cannot race onto the real database.
    """
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)

    async def _run() -> None:
        user_a = _active_user(1, "a@example.com")
        user_b = _active_user(2, "b@example.com")
        users_by_id = {1: user_a, 2: user_b}

        def session_factory() -> MagicMock:
            session = MagicMock()

            def get_user(_model: object, user_id: int) -> User | None:
                return users_by_id.get(user_id)

            session.get.side_effect = get_user
            return session

        verifier = GatewayTokenVerifier(session_factory=session_factory)
        token_a = create_access_token(user_id=1, email="a@example.com", role="member")
        token_b = create_access_token(user_id=2, email="b@example.com", role="member")

        async def run_for_token(token: str, expected_user_id: int) -> int:
            access = await verifier.verify_token(token)
            assert access is not None
            assert access.claims is not None
            assert access.claims["user_id"] == expected_user_id
            assert "a@example.com" not in access.token
            assert "b@example.com" not in access.token

            auth_user = AuthenticatedUser(access)
            reset = auth_context_var.set(auth_user)
            try:
                await asyncio.sleep(0.02)
                principal = get_gateway_principal()
                assert principal is not None
                assert principal.user_id == expected_user_id
                return principal.user_id
            finally:
                auth_context_var.reset(reset)

        user_id_a, user_id_b = await asyncio.gather(
            run_for_token(token_a, 1),
            run_for_token(token_b, 2),
        )
        assert user_id_a == 1
        assert user_id_b == 2

    asyncio.run(_run())


def test_gateway_principal_from_access_claims() -> None:
    principal = GatewayPrincipal.from_access_claims(
        {
            "user_id": 3,
            "email": "x@example.com",
            "role": "admin",
            "auth_method": "jwt",
        }
    )
    assert principal == GatewayPrincipal(
        user_id=3,
        email="x@example.com",
        role=UserRole.admin,
        auth_method=AuthMethod.jwt,
    )


def test_verifier_returns_none_on_database_error() -> None:
    async def _run() -> None:
        verifier = GatewayTokenVerifier()
        token = create_access_token(user_id=1, email="a@example.com", role="member")
        with patch(
            "auth.mcp_verifier.get_session_factory",
            side_effect=RuntimeError("db down"),
        ):
            assert await verifier.verify_token(token) is None

    asyncio.run(_run())


def test_mcp_auth_settings_require_secrets() -> None:
    from auth.mcp_settings import validate_mcp_auth_startup

    mp = pytest.MonkeyPatch()
    mp.setenv("MCP_AUTH_REQUIRED", "true")
    mp.delenv("AUTH_JWT_SECRET", raising=False)
    mp.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    try:
        with pytest.raises(RuntimeError, match="AUTH_JWT_SECRET"):
            validate_mcp_auth_startup()
    finally:
        mp.undo()
