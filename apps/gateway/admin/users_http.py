"""Admin-only HTTP routes for team member and API-key provisioning."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from admin.servers_http import _require_admin
from audit.service import GATEWAY_SERVER_NAME, OUTCOME_SUCCESS, record_tool_invocation
from auth.api_keys import create_api_key, list_all_api_keys, revoke_api_key
from auth.exceptions import DuplicateEmailError, ForbiddenError, NotFoundError, ValidationError
from auth.http import (
    _api_key_metadata,
    _open_db_session,
    _read_json_body,
    _user_response,
)
from auth.users import create_user
from models.api_key import ApiKey
from models.user import User, UserRole


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _user_admin_row(user: User) -> dict[str, Any]:
    return {
        **_user_response(user),
        "is_active": bool(user.is_active),
        "created_at": _isoformat(getattr(user, "created_at", None)),
    }


def _api_key_admin_row(api_key: ApiKey) -> dict[str, Any]:
    owner = api_key.user
    owner_email = owner.email if owner is not None else None
    owner_role = None
    if owner is not None:
        owner_role = (
            owner.role.value if isinstance(owner.role, UserRole) else str(owner.role)
        )
    return {
        **_api_key_metadata(api_key),
        "user_id": api_key.user_id,
        "owner_email": owner_email,
        "owner_role": owner_role,
    }


def _audit_admin_action(
    *,
    actor: User,
    tool_name: str,
    detail: str,
) -> None:
    """Best-effort audit row: actor in user_id, target metadata in error_message."""
    record_tool_invocation(
        user_id=actor.id,
        server_name=GATEWAY_SERVER_NAME,
        tool_name=tool_name,
        duration_ms=0,
        outcome=OUTCOME_SUCCESS,
        error_message=detail,
    )


def register_admin_user_routes(mcp) -> None:
    @mcp.custom_route("/admin/users", methods=["GET"])
    async def admin_list_users(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        session = _open_db_session()
        try:
            users = list(
                session.scalars(select(User).order_by(User.id.asc())).all()
            )
            payload = [_user_admin_row(u) for u in users]
        finally:
            session.close()
        return JSONResponse({"users": payload})

    @mcp.custom_route("/admin/users", methods=["POST"])
    async def admin_create_member(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        data = await _read_json_body(request)
        # Ignore client-supplied role elevation: admins create members only.
        # Creating additional admins requires allow_admin_role via bootstrap.
        email = data.get("email", "")
        password = data.get("password", "")

        session = _open_db_session()
        try:
            try:
                user = create_user(
                    session,
                    str(email),
                    str(password),
                    role=UserRole.member,
                    allow_admin_role=False,
                )
            except DuplicateEmailError:
                return JSONResponse(
                    {"detail": "An account with this email already exists."},
                    status_code=409,
                )
            except ValidationError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
        finally:
            session.close()

        _audit_admin_action(
            actor=admin,
            tool_name="admin_create_member",
            detail=f"target_user_id={user.id};email={user.email};role=member",
        )
        return JSONResponse({"user": _user_admin_row(user)}, status_code=201)

    @mcp.custom_route("/admin/api-keys", methods=["GET"])
    async def admin_list_api_keys(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        session = _open_db_session()
        try:
            keys = list_all_api_keys(session)
            payload = [_api_key_admin_row(k) for k in keys]
        finally:
            session.close()
        return JSONResponse({"api_keys": payload})

    @mcp.custom_route("/admin/users/{user_id}/api-keys", methods=["POST"])
    async def admin_issue_api_key_for_user(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        raw_id = request.path_params.get("user_id")
        try:
            target_user_id = int(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"detail": "Invalid user id."}, status_code=400)

        data = await _read_json_body(request)
        # Ignore any client-supplied owner/user_id — path param + admin session win.
        name = data.get("name", "")

        session = _open_db_session()
        try:
            target = session.get(User, target_user_id)
            if target is None:
                return JSONResponse({"detail": "User not found."}, status_code=404)
            try:
                record, raw_key = create_api_key(session, target, str(name))
                # Reload owner relationship for response metadata.
                record = session.scalar(
                    select(ApiKey)
                    .where(ApiKey.id == record.id)
                    .options(joinedload(ApiKey.user))
                )
            except ValidationError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
        finally:
            session.close()

        assert record is not None
        _audit_admin_action(
            actor=admin,
            tool_name="admin_issue_api_key",
            detail=(
                f"target_user_id={target_user_id};"
                f"key_id={record.id};key_prefix={record.key_prefix}"
            ),
        )
        return JSONResponse(
            {
                "api_key": _api_key_admin_row(record),
                "raw_key": raw_key,
            },
            status_code=201,
        )

    @mcp.custom_route("/admin/api-keys/{key_id}", methods=["DELETE"])
    async def admin_revoke_api_key(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin

        raw_id = request.path_params.get("key_id")
        try:
            key_id = int(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"detail": "Invalid API key id."}, status_code=400)

        session = _open_db_session()
        try:
            try:
                record = revoke_api_key(session, key_id, admin, as_admin=True)
                record = session.scalar(
                    select(ApiKey)
                    .where(ApiKey.id == record.id)
                    .options(joinedload(ApiKey.user))
                )
            except NotFoundError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=404)
            except ForbiddenError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=403)
            except ValidationError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
        finally:
            session.close()

        assert record is not None
        _audit_admin_action(
            actor=admin,
            tool_name="admin_revoke_api_key",
            detail=(
                f"target_user_id={record.user_id};"
                f"key_id={record.id};key_prefix={record.key_prefix}"
            ),
        )
        return JSONResponse({"api_key": _api_key_admin_row(record)})
