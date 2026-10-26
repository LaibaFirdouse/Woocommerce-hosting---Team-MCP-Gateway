"""FastMCP TokenVerifier for dashboard JWTs and gateway API keys."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from mcp.server.auth.provider import AccessToken
from sqlalchemy.orm import Session, sessionmaker

from auth.api_keys import API_KEY_PREFIX, verify_api_key
from auth.principal import AuthMethod
from auth.tokens import decode_access_token
from db.session import get_session_factory
from models.user import User, UserRole

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


def _role_value(role: UserRole | str) -> str:
    if isinstance(role, UserRole):
        return role.value
    return str(role)


def _user_to_access_token(
    user: User,
    *,
    auth_method: AuthMethod,
    expires_at: int | None,
) -> AccessToken:
    # Do not store the raw credential on AccessToken.token.
    return AccessToken(
        token=f"{auth_method.value}:{user.id}",
        client_id=str(user.id),
        scopes=[],
        expires_at=expires_at,
        subject=str(user.id),
        claims={
            "user_id": user.id,
            "email": user.email,
            "role": _role_value(user.role),
            "auth_method": auth_method.value,
        },
    )


def _verify_jwt_sync(token: str, session_factory: SessionFactory) -> AccessToken | None:
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

    exp = payload.get("exp")
    expires_at: int | None
    if isinstance(exp, (int, float)):
        expires_at = int(exp)
    else:
        expires_at = None

    session = session_factory()
    try:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return _user_to_access_token(
            user,
            auth_method=AuthMethod.jwt,
            expires_at=expires_at,
        )
    finally:
        session.close()


def _verify_api_key_sync(token: str, session_factory: SessionFactory) -> AccessToken | None:
    session = session_factory()
    try:
        user = verify_api_key(session, token)
        if user is None:
            return None
        return _user_to_access_token(
            user,
            auth_method=AuthMethod.api_key,
            expires_at=None,
        )
    finally:
        session.close()


class GatewayTokenVerifier:
    """Accepts HS256 session JWTs and uk_* API keys via Bearer authorization."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | Callable[[], Session] | None = None,
    ) -> None:
        # Production uses get_session_factory(); tests may inject an isolated factory.
        self._session_factory_provider = session_factory

    def _resolve_session_factory(self) -> SessionFactory:
        if self._session_factory_provider is not None:
            provider = self._session_factory_provider
            # sessionmaker and zero-arg callables both produce a Session.
            return provider  # type: ignore[return-value]
        return get_session_factory()

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not isinstance(token, str) or not token.strip():
            return None
        trimmed = token.strip()
        try:
            session_factory = self._resolve_session_factory()
            if trimmed.startswith(API_KEY_PREFIX):
                return await asyncio.to_thread(
                    _verify_api_key_sync, trimmed, session_factory
                )
            return await asyncio.to_thread(_verify_jwt_sync, trimmed, session_factory)
        except Exception:
            # Fail closed without logging the credential.
            logger.exception("MCP token verification failed")
            return None
