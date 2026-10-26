"""
MCP transport authentication configuration.

MCP_AUTH_REQUIRED defaults to true so production deployments do not expose an
open /mcp endpoint. Set MCP_AUTH_REQUIRED=false only for local development when
you intentionally run the gateway without MCP credentials (not recommended).

When MCP auth is enabled, AUTH_JWT_SECRET and DATABASE_URL are required at
startup (JWT login and API-key verification both need them).
"""

from __future__ import annotations

import os

from pydantic import AnyHttpUrl

from auth.tokens import TokenConfigurationError, get_jwt_secret
from db.lifecycle import is_database_configured
from mcp.server.auth.settings import AuthSettings


def is_mcp_auth_required() -> bool:
    raw = os.getenv("MCP_AUTH_REQUIRED", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def gateway_public_base_url() -> str:
    base = os.getenv("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8001").strip()
    return base.rstrip("/")


def build_mcp_auth_settings() -> AuthSettings:
    base = gateway_public_base_url()
    return AuthSettings(
        issuer_url=AnyHttpUrl(f"{base}/auth"),
        resource_server_url=AnyHttpUrl(f"{base}/mcp"),
        # Our tokens are not OAuth resource-bound; GatewayTokenVerifier
        # validates JWT/API-key credentials itself.
        validate_token_resource=False,
        required_scopes=None,
    )


def validate_mcp_auth_startup() -> None:
    """
    Fail fast when MCP auth is enabled but required secrets or DB are missing.
    """
    if not is_mcp_auth_required():
        return

    try:
        get_jwt_secret()
    except TokenConfigurationError as exc:
        raise RuntimeError(
            "MCP authentication is enabled (MCP_AUTH_REQUIRED is not false) but "
            "AUTH_JWT_SECRET is not configured. Set AUTH_JWT_SECRET or disable "
            "MCP auth for local development with MCP_AUTH_REQUIRED=false."
        ) from exc

    if not is_database_configured():
        raise RuntimeError(
            "MCP authentication is enabled but DATABASE_URL is not set. "
            "Configure PostgreSQL or set MCP_AUTH_REQUIRED=false for local "
            "development without MCP credentials."
        )


def fastmcp_auth_kwargs() -> dict:
    """Keyword arguments for FastMCP when MCP auth is enabled."""
    if not is_mcp_auth_required():
        return {}

    from auth.mcp_verifier import GatewayTokenVerifier

    return {
        "auth": build_mcp_auth_settings(),
        "token_verifier": GatewayTokenVerifier(),
    }
