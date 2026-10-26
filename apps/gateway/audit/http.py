"""Read-only HTTP route for audit log listing."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from audit.service import list_audit_logs, sanitize_error_message
from auth.exceptions import ForbiddenError, ValidationError
from auth.http import _authenticated_user, _open_db_session
from models.audit_log import AuditLog
from models.user import UserRole

logger = logging.getLogger(__name__)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _audit_log_response(row: AuditLog) -> dict[str, Any]:
    """Safe audit fields only — never tool args, results, or credentials."""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "server_name": row.server_name,
        "tool_name": row.tool_name,
        "timestamp": _isoformat(row.timestamp),
        "duration_ms": row.duration_ms,
        "outcome": row.outcome,
        "error_message": sanitize_error_message(row.error_message),
    }


def _parse_int_param(raw: str | None, *, name: str) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an integer.") from exc


def register_audit_routes(mcp) -> None:
    @mcp.custom_route("/audit-logs", methods=["GET"])
    async def get_audit_logs(request: Request) -> Response:
        auth = _authenticated_user(request)
        if isinstance(auth, JSONResponse):
            return auth

        params = request.query_params
        try:
            # Presence of user_id (even empty / own id) is forbidden for members.
            if "user_id" in params and auth.role is not UserRole.admin:
                raise ForbiddenError(
                    "Only administrators may filter audit logs by user_id."
                )

            limit = _parse_int_param(params.get("limit"), name="limit")
            if limit is None:
                limit = 50
            offset = _parse_int_param(params.get("offset"), name="offset")
            if offset is None:
                offset = 0
            filter_user_id = None
            if "user_id" in params:
                filter_user_id = _parse_int_param(
                    params.get("user_id"), name="user_id"
                )
                if filter_user_id is None:
                    raise ValidationError("user_id must be an integer.")

            outcome = params.get("outcome")

            session = _open_db_session()
            try:
                rows, total = list_audit_logs(
                    session,
                    actor=auth,
                    limit=limit,
                    offset=offset,
                    outcome=outcome,
                    filter_user_id=filter_user_id,
                )
                payload = [_audit_log_response(row) for row in rows]
            finally:
                session.close()
        except ForbiddenError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        except ValidationError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except Exception:
            logger.exception("Audit log listing failed")
            return JSONResponse(
                {"detail": "Unable to load audit logs."},
                status_code=503,
            )

        return JSONResponse(
            {
                "audit_logs": payload,
                "limit": limit,
                "offset": offset,
                "total": total,
            }
        )
