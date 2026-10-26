"""Tests for /admin/servers management and tool-registry effects."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from auth.exceptions import NotFoundError, ValidationError
from auth.tokens import create_access_token
from models.registered_server import RegisteredServer
from models.user import User, UserRole
from servers.credentials import decrypt_authorization_header, encrypt_authorization_header
from servers.service import (
    create_registered_server,
    delete_registered_server,
    update_registered_server,
)

JWT_SECRET = "test-jwt-secret-key-with-enough-length!!"


@pytest.fixture(scope="module")
def admin_client():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("UPSTREAM_SECRETS_KEY", "upstream-secrets-key-with-32chars!!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")

    async def noop_upstream() -> None:
        return

    sys.modules.pop("server", None)
    try:
        import server as server_mod

        async def noop_refresh() -> None:
            return

        with (
            patch.object(server_mod, "_register_upstream_proxies", noop_upstream),
            patch.object(server_mod, "startup_database"),
            patch.object(server_mod, "shutdown_database"),
            patch("admin.servers_http._servers_changed_hook", noop_refresh),
        ):
            with TestClient(server_mod.mcp.streamable_http_app()) as client:
                yield client, server_mod
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


def _sample_server(
    *,
    server_id: int = 1,
    name: str = "inventory",
    enabled: bool = True,
    ciphertext: str | None = None,
) -> RegisteredServer:
    now = datetime.now(timezone.utc)
    return RegisteredServer(
        id=server_id,
        name=name,
        server_type="mcp",
        endpoint_url="http://127.0.0.1:8002/mcp",
        is_enabled=enabled,
        auth_header_ciphertext=ciphertext,
        created_at=now,
        updated_at=now,
    )


def test_admin_create_requires_auth(admin_client) -> None:
    client, _ = admin_client
    response = client.post(
        "/admin/servers",
        json={
            "name": "custom",
            "server_type": "mcp",
            "endpoint_url": "http://127.0.0.1:9000/mcp",
        },
    )
    assert response.status_code == 401


def test_member_denied(admin_client) -> None:
    client, _ = admin_client
    member = _user(user_id=2, email="member@example.com", role=UserRole.member)
    with patch("admin.servers_http._authenticated_user", return_value=member):
        response = client.get("/admin/servers", headers=_headers(member))
    assert response.status_code == 403


def test_api_key_bearer_denied(admin_client) -> None:
    client, _ = admin_client
    response = client.get(
        "/admin/servers",
        headers={"Authorization": "Bearer uk_prefix.secret-value-goes-here"},
    )
    assert response.status_code == 401


def test_admin_crud_flow_hides_credentials(admin_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _server_mod = admin_client
    admin = _user(user_id=1, email="admin@example.com", role=UserRole.admin)
    secret = "Bearer upstream-super-secret-token"
    created = _sample_server(
        ciphertext=encrypt_authorization_header(secret),
    )

    with patch("admin.servers_http._authenticated_user", return_value=admin):
        with patch(
            "admin.servers_http.create_registered_server",
            return_value=created,
        ) as mock_create:
            with patch(
                "admin.servers_http._open_db_session",
                return_value=MagicMock(),
            ):
                create_resp = client.post(
                    "/admin/servers",
                    headers=_headers(admin),
                    json={
                        "name": "inventory",
                        "server_type": "mcp",
                        "endpoint_url": "http://127.0.0.1:8002/mcp",
                        "authorization_header": secret,
                        "user_id": 999,
                        "role": "member",
                    },
                )

    assert create_resp.status_code == 201
    body = create_resp.json()["server"]
    assert body["name"] == "inventory"
    assert body["has_credentials"] is True
    assert "authorization_header" not in create_resp.text
    assert secret not in create_resp.text
    assert "auth_header_ciphertext" not in create_resp.text
    assert mock_create.call_args.kwargs.get("authorization_header") == secret

    with patch("admin.servers_http._authenticated_user", return_value=admin):
        with patch(
            "admin.servers_http.list_registered_servers",
            return_value=[created],
        ):
            with patch(
                "admin.servers_http._open_db_session",
                return_value=MagicMock(),
            ):
                list_resp = client.get("/admin/servers", headers=_headers(admin))

    assert list_resp.status_code == 200
    assert list_resp.json()["servers"][0]["has_credentials"] is True
    assert secret not in list_resp.text

    updated = _sample_server(enabled=False, ciphertext=created.auth_header_ciphertext)
    with patch("admin.servers_http._authenticated_user", return_value=admin):
        with patch(
            "admin.servers_http.update_registered_server",
            return_value=updated,
        ) as mock_update:
            with patch(
                "admin.servers_http._open_db_session",
                return_value=MagicMock(),
            ):
                patch_resp = client.patch(
                    "/admin/servers/1",
                    headers=_headers(admin),
                    json={"is_enabled": False},
                )

    assert patch_resp.status_code == 200
    assert patch_resp.json()["server"]["is_enabled"] is False
    assert mock_update.call_args.kwargs.get("is_enabled") is False
    assert secret not in patch_resp.text

    with patch("admin.servers_http._authenticated_user", return_value=admin):
        with patch("admin.servers_http.delete_registered_server") as mock_delete:
            with patch(
                "admin.servers_http._open_db_session",
                return_value=MagicMock(),
            ):
                delete_resp = client.delete(
                    "/admin/servers/1",
                    headers=_headers(admin),
                )

    assert delete_resp.status_code == 200
    mock_delete.assert_called_once()
    assert secret not in caplog.text


def test_create_validation_error(admin_client) -> None:
    client, _ = admin_client
    admin = _user(user_id=1, email="admin@example.com", role=UserRole.admin)
    with patch("admin.servers_http._authenticated_user", return_value=admin):
        with patch(
            "admin.servers_http.create_registered_server",
            side_effect=ValidationError("endpoint_url must use http or https."),
        ):
            with patch(
                "admin.servers_http._open_db_session",
                return_value=MagicMock(),
            ):
                response = client.post(
                    "/admin/servers",
                    headers=_headers(admin),
                    json={
                        "name": "bad",
                        "server_type": "mcp",
                        "endpoint_url": "ftp://x",
                    },
                )
    assert response.status_code == 400


def test_duplicate_name_conflict(admin_client) -> None:
    client, _ = admin_client
    admin = _user(user_id=1, email="admin@example.com", role=UserRole.admin)
    with patch("admin.servers_http._authenticated_user", return_value=admin):
        with patch(
            "admin.servers_http.create_registered_server",
            side_effect=ValidationError(
                "A registered server with this name already exists."
            ),
        ):
            with patch(
                "admin.servers_http._open_db_session",
                return_value=MagicMock(),
            ):
                response = client.post(
                    "/admin/servers",
                    headers=_headers(admin),
                    json={
                        "name": "inventory",
                        "server_type": "mcp",
                        "endpoint_url": "http://127.0.0.1:8002/mcp",
                    },
                )
    assert response.status_code == 409


def test_patch_missing_server(admin_client) -> None:
    client, _ = admin_client
    admin = _user(user_id=1, email="admin@example.com", role=UserRole.admin)
    with patch("admin.servers_http._authenticated_user", return_value=admin):
        with patch(
            "admin.servers_http.update_registered_server",
            side_effect=NotFoundError("Registered server not found."),
        ):
            with patch(
                "admin.servers_http._open_db_session",
                return_value=MagicMock(),
            ):
                response = client.patch(
                    "/admin/servers/404",
                    headers=_headers(admin),
                    json={"is_enabled": True},
                )
    assert response.status_code == 404


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.delenv("UPSTREAM_SECRETS_KEY", raising=False)
    raw = "Bearer abc.def.ghi"
    cipher = encrypt_authorization_header(raw)
    assert raw not in cipher
    assert decrypt_authorization_header(cipher) == raw


def test_service_rejects_invalid_url() -> None:
    session = MagicMock()
    with pytest.raises(ValidationError, match="http or https"):
        create_registered_server(
            session,
            name="x",
            server_type="mcp",
            endpoint_url="not-a-url",
        )


def test_disabled_server_tools_not_callable() -> None:
    from servers import registry as reg

    # Isolate mutable registry maps from other tests.
    reg.TOOL_OWNER.clear()
    reg.TOOL_UPSTREAM_URL.clear()
    reg.TOOL_SERVER_NAME.clear()
    reg.UPSTREAM_AUTH_HEADERS.clear()

    mcp = MagicMock()
    mcp._tool_manager._tools = {"get_inventory": object()}
    reg.TOOL_OWNER["get_inventory"] = "inventory"
    reg.TOOL_UPSTREAM_URL["get_inventory"] = "http://127.0.0.1:8002/mcp"
    reg.TOOL_SERVER_NAME["get_inventory"] = "inventory"

    assert reg.is_tool_callable("get_inventory") is True
    removed = reg.unregister_tools_for_owner(mcp, "inventory")
    assert removed == 1
    assert reg.is_tool_callable("get_inventory") is False
    assert "get_inventory" not in mcp._tool_manager._tools


def test_refresh_isolates_upstream_failures() -> None:
    async def _run() -> None:
        from servers.registry import UpstreamConfig, refresh_upstream_tools

        mcp = MagicMock()
        mcp._tool_manager._tools = {}
        registered: list[str] = []

        async def discover(cfg: UpstreamConfig):
            if cfg.upstream_id == "broken":
                return {
                    "upstream_id": cfg.upstream_id,
                    "upstream_url": cfg.url,
                    "status": "unavailable",
                    "error": "down",
                    "tools": [],
                    "_raw_tools": [],
                }
            tool = MagicMock()
            tool.name = "healthy_tool"
            tool.description = ""
            tool.inputSchema = {"type": "object", "properties": {}}
            tool.title = None
            tool.annotations = None
            tool.icons = None
            tool.meta = None
            return {
                "upstream_id": cfg.upstream_id,
                "upstream_url": cfg.url,
                "status": "ok",
                "error": None,
                "tools": [{"name": "healthy_tool", "description": ""}],
                "_raw_tools": [tool],
            }

        def register(tool, url, owner=None):
            registered.append(owner or "")
            return True

        configs = [
            UpstreamConfig("broken", "http://127.0.0.1:1/mcp"),
            UpstreamConfig("healthy", "http://127.0.0.1:2/mcp"),
        ]
        with patch(
            "servers.registry.load_enabled_upstream_configs",
            return_value=configs,
        ):
            await refresh_upstream_tools(
                mcp,
                reserved_tool_names=frozenset(),
                discover_snapshot=discover,
                register_proxy_tool=register,
            )

        assert "healthy" in registered
        assert registered.count("broken") == 0

    asyncio.run(_run())


def test_update_enable_disable_service() -> None:
    session = MagicMock()
    record = _sample_server(enabled=True)
    session.get.return_value = record

    updated = update_registered_server(session, 1, is_enabled=False)
    assert updated.is_enabled is False
    session.commit.assert_called()

    session.get.return_value = None
    with pytest.raises(NotFoundError):
        delete_registered_server(session, 99)
