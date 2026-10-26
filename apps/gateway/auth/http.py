import json
import os
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from auth.api_keys import create_api_key, list_api_keys, revoke_api_key
from auth.exceptions import (
    DuplicateEmailError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from auth.tokens import (
    TokenConfigurationError,
    create_access_token,
    decode_access_token,
    get_jwt_secret,
)
from auth.users import authenticate_user, create_user
from db.lifecycle import is_database_configured
from db.session import get_session_factory
from models.api_key import ApiKey
from models.user import User, UserRole

COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "urumi_session")


def _cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _user_response(user: User) -> dict[str, Any]:
    role = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    return {"id": user.id, "email": user.email, "role": role}


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _api_key_metadata(api_key: ApiKey) -> dict[str, Any]:
    """Safe metadata only — never include raw key or key_hash."""
    return {
        "id": api_key.id,
        "name": api_key.name,
        "key_prefix": api_key.key_prefix,
        "created_at": _isoformat(api_key.created_at),
        "last_used_at": _isoformat(api_key.last_used_at),
        "revoked_at": _isoformat(api_key.revoked_at),
        "revoked": api_key.revoked_at is not None,
    }


async def _read_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.body()
        if not body:
            return {}
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _database_unavailable_response() -> JSONResponse:
    return JSONResponse(
        {"detail": "Database is not configured for authentication."},
        status_code=503,
    )


def _auth_not_configured_response() -> JSONResponse:
    return JSONResponse(
        {"detail": "HTTP authentication is not configured on this server."},
        status_code=503,
    )


def _open_db_session():
    return get_session_factory()()


def _session_from_token(token: str) -> User | None:
    payload = decode_access_token(token)
    if not payload:
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None

    session = _open_db_session()
    try:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return user
    finally:
        session.close()


def _token_from_request(request: Request) -> str | None:
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        return cookie_token

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return None


def _require_service_ready() -> JSONResponse | None:
    if not is_database_configured():
        return _database_unavailable_response()
    try:
        get_jwt_secret()
    except TokenConfigurationError:
        return _auth_not_configured_response()
    return None


def _authenticated_user(request: Request) -> User | JSONResponse:
    """
    Resolve the caller from JWT cookie or Bearer JWT only.

    Management routes intentionally do not treat API keys as the authority for
    creating/listing/revoking keys (JWT/session ownership).
    """
    not_ready = _require_service_ready()
    if not_ready is not None:
        return not_ready

    token = _token_from_request(request)
    if not token:
        return JSONResponse({"detail": "Not authenticated."}, status_code=401)

    # Reject API-key shaped credentials for management endpoints: ownership
    # must come from a dashboard JWT/session, not a bearer API key.
    from auth.api_keys import API_KEY_PREFIX

    if token.startswith(API_KEY_PREFIX):
        return JSONResponse({"detail": "Not authenticated."}, status_code=401)

    user = _session_from_token(token)
    if user is None:
        return JSONResponse({"detail": "Not authenticated."}, status_code=401)
    return user


def _attach_session_cookie(response: JSONResponse, token: str) -> None:
    max_age = int(os.getenv("AUTH_JWT_EXPIRE_MINUTES", "1440")) * 60
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def register_auth_routes(mcp) -> None:
    @mcp.custom_route("/auth/register", methods=["POST"])
    async def auth_register(request: Request) -> Response:
        if not is_database_configured():
            return _database_unavailable_response()
        try:
            get_jwt_secret()
        except TokenConfigurationError:
            return _auth_not_configured_response()

        data = await _read_json_body(request)
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

        return JSONResponse(
            {"user": _user_response(user)},
            status_code=201,
        )

    @mcp.custom_route("/auth/login", methods=["POST"])
    async def auth_login(request: Request) -> Response:
        if not is_database_configured():
            return _database_unavailable_response()
        try:
            get_jwt_secret()
        except TokenConfigurationError:
            return _auth_not_configured_response()

        data = await _read_json_body(request)
        email = data.get("email", "")
        password = data.get("password", "")

        session = _open_db_session()
        try:
            user = authenticate_user(session, str(email), str(password))
        finally:
            session.close()

        if user is None:
            return JSONResponse(
                {"detail": "Invalid email or password."},
                status_code=401,
            )

        role = user.role.value if isinstance(user.role, UserRole) else str(user.role)
        token = create_access_token(user_id=user.id, email=user.email, role=role)
        response = JSONResponse({"user": _user_response(user)})
        _attach_session_cookie(response, token)
        return response

    @mcp.custom_route("/auth/me", methods=["GET"])
    async def auth_me(request: Request) -> Response:
        if not is_database_configured():
            return _database_unavailable_response()
        try:
            get_jwt_secret()
        except TokenConfigurationError:
            return _auth_not_configured_response()

        token = _token_from_request(request)
        if not token:
            return JSONResponse({"detail": "Not authenticated."}, status_code=401)

        user = _session_from_token(token)
        if user is None:
            return JSONResponse({"detail": "Not authenticated."}, status_code=401)

        return JSONResponse({"user": _user_response(user)})

    @mcp.custom_route("/auth/logout", methods=["POST"])
    async def auth_logout(_request: Request) -> Response:
        """Clear the HttpOnly session cookie. Safe to call when already logged out."""
        response = JSONResponse({"detail": "Logged out."})
        response.delete_cookie(
            key=COOKIE_NAME,
            path="/",
            secure=_cookie_secure(),
            httponly=True,
            samesite="lax",
        )
        return response

    @mcp.custom_route("/auth/api-keys", methods=["POST"])
    async def auth_create_api_key(request: Request) -> Response:
        auth = _authenticated_user(request)
        if isinstance(auth, JSONResponse):
            return auth

        data = await _read_json_body(request)
        # Ignore any client-supplied user_id; ownership is the JWT subject only.
        name = data.get("name", "")

        session = _open_db_session()
        try:
            try:
                record, raw_key = create_api_key(session, auth, str(name))
            except ValidationError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
        finally:
            session.close()

        return JSONResponse(
            {
                "api_key": _api_key_metadata(record),
                "raw_key": raw_key,
            },
            status_code=201,
        )

    @mcp.custom_route("/auth/api-keys", methods=["GET"])
    async def auth_list_api_keys(request: Request) -> Response:
        auth = _authenticated_user(request)
        if isinstance(auth, JSONResponse):
            return auth

        session = _open_db_session()
        try:
            keys = list_api_keys(session, auth)
            payload = [_api_key_metadata(key) for key in keys]
        finally:
            session.close()

        return JSONResponse({"api_keys": payload})

    @mcp.custom_route("/auth/api-keys/{key_id}", methods=["DELETE"])
    async def auth_revoke_api_key(request: Request) -> Response:
        auth = _authenticated_user(request)
        if isinstance(auth, JSONResponse):
            return auth

        raw_id = request.path_params.get("key_id")
        try:
            key_id = int(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"detail": "Invalid API key id."}, status_code=400)

        session = _open_db_session()
        try:
            try:
                # Owners revoke their own keys; admins may revoke any key.
                from auth.permissions import user_is_admin

                as_admin = user_is_admin(auth)
                record = revoke_api_key(session, key_id, auth, as_admin=as_admin)
            except NotFoundError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=404)
            except ForbiddenError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=403)
            except ValidationError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
        finally:
            session.close()

        return JSONResponse({"api_key": _api_key_metadata(record)})
