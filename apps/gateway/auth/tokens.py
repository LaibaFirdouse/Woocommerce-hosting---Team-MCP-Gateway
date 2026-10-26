"""
Dashboard session tokens using signed JWTs (HS256).

Login sets an HttpOnly cookie for browser clients. The same JWT may be sent
as a Bearer token for non-browser API clients. Verify with decode_access_token().
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

JWT_ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32


class TokenConfigurationError(RuntimeError):
    """AUTH_JWT_SECRET is missing or too weak."""


def _jwt_expire_minutes() -> int:
    raw = os.getenv("AUTH_JWT_EXPIRE_MINUTES", "1440").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1440


def get_jwt_secret() -> str:
    secret = os.getenv("AUTH_JWT_SECRET", "").strip()
    if len(secret) < MIN_SECRET_LENGTH:
        raise TokenConfigurationError(
            "AUTH_JWT_SECRET must be set to a random string of at least "
            f"{MIN_SECRET_LENGTH} characters for HTTP authentication."
        )
    return secret


def create_access_token(*, user_id: int, email: str, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=_jwt_expire_minutes()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.PyJWTError:
        return None
