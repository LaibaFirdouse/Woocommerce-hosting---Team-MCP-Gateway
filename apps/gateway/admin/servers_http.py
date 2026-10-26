"""Admin-only HTTP routes for registered MCP servers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from auth.exceptions import ForbiddenError, NotFoundError, ValidationError
from auth.http import (
    _authenticated_user,
    _open_db_session,
    _read_json_body,
)
from auth.permissions import user_is_admin
from models.registered_server import RegisteredServer
from models.user import User
from servers.service import (
    create_registered_server,
    delete_registered_server,
    list_registered_servers,
    update_registered_server,
)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _server_response(record: RegisteredServer) -> dict[str, Any]:
    """Safe server metadata — never includes ciphertext or plaintext credentials."""
    return {
        "id": record.id,
        "name": record.name,
        "server_type": record.server_type,
        "endpoint_url": record.endpoint_url,
        "is_enabled": record.is_enabled,
        "has_credentials": bool(record.auth_header_ciphertext),
        "created_at": _isoformat(record.created_at),
        "updated_at": _isoformat(record.updated_at),
    }


def _require_admin(request: Request) -> User | JSONResponse:
    """
    JWT/session only. Role is taken from the current database user record,
    never from the request body or JWT claims alone without DB reload
    (``_authenticated_user`` already reloads from DB).
    """
    auth = _authenticated_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not user_is_admin(auth):
        return JSONResponse({"detail": "Admin privileges required."}, status_code=403)
    return auth


# Mutable hook so tests can replace the refresh callback without re-importing.
_servers_changed_hook = None


async def notify_servers_changed() -> None:
    hook = _servers_changed_hook
    if hook is not None:
        await hook()


def register_admin_server_routes(mcp, *, on_servers_changed) -> None:
    """
    Register CRUD routes. ``on_servers_changed`` is an async callable invoked
    after successful mutations so the tool registry can refresh.
    """
    global _servers_changed_hook
    _servers_changed_hook = on_servers_changed

    @mcp.custom_route("/admin/servers", methods=["POST"])
    async def admin_create_server(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        data = await _read_json_body(request)
        # Ignore role/user_id from body — authority is the authenticated admin only.
        session = _open_db_session()
        try:
            try:
                record = create_registered_server(
                    session,
                    name=str(data.get("name", "")),
                    server_type=str(data.get("server_type", "mcp")),
                    endpoint_url=str(data.get("endpoint_url", "")),
                    is_enabled=bool(data.get("is_enabled", True)),
                    authorization_header=(
                        str(data["authorization_header"])
                        if data.get("authorization_header") is not None
                        else None
                    ),
                )
            except ValidationError as exc:
                detail = str(exc)
                status = 409 if "already exists" in detail.lower() else 400
                return JSONResponse({"detail": detail}, status_code=status)
        finally:
            session.close()

        await notify_servers_changed()
        return JSONResponse({"server": _server_response(record)}, status_code=201)

    @mcp.custom_route("/admin/servers", methods=["GET"])
    async def admin_list_servers(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        session = _open_db_session()
        try:
            records = list_registered_servers(session)
            payload = [_server_response(row) for row in records]
        finally:
            session.close()
        return JSONResponse({"servers": payload})

    @mcp.custom_route("/admin/servers/{server_id}", methods=["PATCH"])
    async def admin_patch_server(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        raw_id = request.path_params.get("server_id")
        try:
            server_id = int(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"detail": "Invalid server id."}, status_code=400)

        data = await _read_json_body(request)
        kwargs: dict[str, Any] = {}
        if "name" in data:
            kwargs["name"] = str(data.get("name", ""))
        if "server_type" in data:
            kwargs["server_type"] = str(data.get("server_type", ""))
        if "endpoint_url" in data:
            kwargs["endpoint_url"] = str(data.get("endpoint_url", ""))
        if "is_enabled" in data:
            kwargs["is_enabled"] = bool(data.get("is_enabled"))
        if data.get("clear_credentials") is True:
            kwargs["clear_credentials"] = True
        elif "authorization_header" in data:
            value = data.get("authorization_header")
            kwargs["authorization_header"] = None if value is None else str(value)

        session = _open_db_session()
        try:
            try:
                record = update_registered_server(session, server_id, **kwargs)
            except NotFoundError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=404)
            except ValidationError as exc:
                detail = str(exc)
                status = 409 if "already exists" in detail.lower() else 400
                return JSONResponse({"detail": detail}, status_code=status)
            except ForbiddenError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=403)
        finally:
            session.close()

        await notify_servers_changed()
        return JSONResponse({"server": _server_response(record)})

    @mcp.custom_route("/admin/servers/{server_id}", methods=["DELETE"])
    async def admin_delete_server(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        raw_id = request.path_params.get("server_id")
        try:
            server_id = int(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"detail": "Invalid server id."}, status_code=400)

        session = _open_db_session()
        try:
            try:
                delete_registered_server(session, server_id)
            except NotFoundError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=404)
            except ValidationError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
        finally:
            session.close()

        await notify_servers_changed()
        return JSONResponse({"detail": "Registered server deleted."}, status_code=200)
