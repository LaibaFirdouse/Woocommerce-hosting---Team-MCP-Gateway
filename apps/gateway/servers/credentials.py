"""
Encrypt optional upstream Authorization headers at rest.

Credential-storage decision
---------------------------
Plaintext Authorization header values (e.g. ``Bearer …`` / ``Basic …``) are
accepted only on write (create/PATCH). They are Fernet-encrypted into
``RegisteredServer.auth_header_ciphertext`` and never returned in API JSON or
written to logs. Decryption happens only in-process for upstream MCP calls.

Key material: ``UPSTREAM_SECRETS_KEY`` (preferred) or a key derived from
``AUTH_JWT_SECRET``. Missing key material refuses to store new secrets.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from auth.exceptions import ValidationError
from auth.tokens import TokenConfigurationError, get_jwt_secret

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    explicit = os.getenv("UPSTREAM_SECRETS_KEY", "").strip()
    if explicit:
        # Accept raw Fernet keys or any secret (≥32 chars) hashed into a key.
        try:
            if len(explicit) == 44 and explicit.endswith("="):
                return Fernet(explicit.encode("ascii"))
        except (ValueError, InvalidToken):
            pass
        digest = hashlib.sha256(explicit.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    try:
        secret = get_jwt_secret()
    except TokenConfigurationError as exc:
        raise ValidationError(
            "Cannot store upstream credentials: configure UPSTREAM_SECRETS_KEY "
            "or AUTH_JWT_SECRET."
        ) from exc
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_authorization_header(header_value: str) -> str:
    cleaned = (header_value or "").strip()
    if not cleaned:
        raise ValidationError("authorization_header cannot be empty.")
    if "\n" in cleaned or "\r" in cleaned:
        raise ValidationError("authorization_header contains invalid characters.")
    return _fernet().encrypt(cleaned.encode("utf-8")).decode("ascii")


def decrypt_authorization_header(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("Failed to decrypt upstream authorization header ciphertext")
        return None
