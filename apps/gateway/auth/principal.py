"""Authenticated gateway identity for MCP and future authorization."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator

from mcp.server.auth.middleware.auth_context import get_access_token

from models.user import UserRole


class AuthMethod(str, Enum):
    jwt = "jwt"
    api_key = "api_key"


@dataclass(frozen=True, slots=True)
class GatewayPrincipal:
    user_id: int
    email: str
    role: UserRole
    auth_method: AuthMethod

    @classmethod
    def from_user(cls, user: Any, *, auth_method: AuthMethod) -> GatewayPrincipal:
        role = user.role
        if isinstance(role, UserRole):
            resolved_role = role
        else:
            resolved_role = UserRole(str(role))
        return cls(
            user_id=int(user.id),
            email=str(user.email),
            role=resolved_role,
            auth_method=auth_method,
        )

    @classmethod
    def from_access_claims(cls, claims: dict[str, Any]) -> GatewayPrincipal | None:
        try:
            user_id = int(claims["user_id"])
            email = str(claims["email"])
            role = UserRole(str(claims["role"]))
            auth_method = AuthMethod(str(claims["auth_method"]))
        except (KeyError, TypeError, ValueError):
            return None
        return cls(
            user_id=user_id,
            email=email,
            role=role,
            auth_method=auth_method,
        )


# Optional override for non-MCP request paths (e.g. future HTTP chat) so audit
# attribution still uses request-scoped identity without inventing a user_id.
_principal_override: ContextVar[GatewayPrincipal | None] = ContextVar(
    "gateway_principal_override",
    default=None,
)


@contextmanager
def use_gateway_principal(principal: GatewayPrincipal) -> Iterator[GatewayPrincipal]:
    """Bind ``principal`` for the current task/request (chat HTTP, tests)."""
    token = _principal_override.set(principal)
    try:
        yield principal
    finally:
        _principal_override.reset(token)


def get_gateway_principal() -> GatewayPrincipal | None:
    """
    Return the authenticated principal for the current request.

    Prefers an explicit override (HTTP chat), then the MCP SDK request-scoped
    auth context (contextvars). Never reads identity from tool arguments.
    """
    override = _principal_override.get()
    if override is not None:
        return override

    access = get_access_token()
    if access is None or not access.claims:
        return None
    return GatewayPrincipal.from_access_claims(access.claims)
