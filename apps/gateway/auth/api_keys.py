"""API key issuance, verification, and revocation."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from auth.exceptions import ForbiddenError, NotFoundError, ValidationError
from auth.passwords import hash_password, verify_password
from models.api_key import ApiKey
from models.user import User

API_KEY_PREFIX = "uk_"
_PUBLIC_ID_BYTES = 8
_SECRET_BYTES = 32
_MAX_CREATE_ATTEMPTS = 5

# uk_<public_id>.<secret> — public_id is indexed by key_prefix (uk_<public_id>).
_KEY_PATTERN = re.compile(
    rf"^{re.escape(API_KEY_PREFIX)}([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)$"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_raw_key(raw_key: str) -> tuple[str, str] | None:
    if not raw_key or not isinstance(raw_key, str):
        return None
    trimmed = raw_key.strip()
    match = _KEY_PATTERN.match(trimmed)
    if not match:
        return None
    public_id, secret = match.group(1), match.group(2)
    if not public_id or not secret:
        return None
    return f"{API_KEY_PREFIX}{public_id}", trimmed


def _generate_raw_key() -> tuple[str, str]:
    public_id = secrets.token_urlsafe(_PUBLIC_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    key_prefix = f"{API_KEY_PREFIX}{public_id}"
    raw_key = f"{key_prefix}.{secret}"
    return key_prefix, raw_key


def _load_user(session: Session, user: User | int) -> User:
    if isinstance(user, User):
        if user.id is None:
            raise ValidationError("User must be persisted before issuing API keys.")
        return user
    loaded = session.get(User, user)
    if loaded is None:
        raise ValidationError("User not found.")
    return loaded


def create_api_key(
    session: Session,
    user: User | int,
    name: str,
) -> tuple[ApiKey, str]:
    """
    Create an API key for an active user.

    Returns the persisted ApiKey and the raw key (shown only once).
    """
    resolved_user = _load_user(session, user)
    if not resolved_user.is_active:
        raise ValidationError("API keys cannot be issued to inactive users.")

    trimmed_name = (name or "").strip()
    if not trimmed_name:
        raise ValidationError("API key name is required.")

    last_error: IntegrityError | None = None
    for _ in range(_MAX_CREATE_ATTEMPTS):
        key_prefix, raw_key = _generate_raw_key()
        record = ApiKey(
            user_id=resolved_user.id,
            name=trimmed_name,
            key_prefix=key_prefix,
            key_hash=hash_password(raw_key),
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            last_error = exc
            continue
        session.refresh(record)
        return record, raw_key

    raise ValidationError(
        "Could not generate a unique API key; try again."
    ) from last_error


def verify_api_key(session: Session, raw_key: str) -> User | None:
    """
    Validate a raw API key and return the associated active user.

    Returns None for malformed, unknown, revoked, or inactive-user keys without
    distinguishing the failure reason.
    """
    parsed = _parse_raw_key(raw_key)
    if parsed is None:
        return None
    key_prefix, normalized_key = parsed

    api_key = session.scalar(
        select(ApiKey)
        .where(ApiKey.key_prefix == key_prefix)
        .options(joinedload(ApiKey.user))
    )
    if api_key is None:
        return None
    if api_key.revoked_at is not None:
        return None
    if not verify_password(normalized_key, api_key.key_hash):
        return None

    user = api_key.user
    if user is None or not user.is_active:
        return None

    api_key.last_used_at = _utcnow()
    try:
        session.commit()
    except Exception:
        session.rollback()
        return None

    session.refresh(user)
    return user


def list_api_keys(session: Session, user: User | int) -> list[ApiKey]:
    """Return API keys owned by ``user``, newest first. Never includes raw secrets."""
    resolved_user = _load_user(session, user)
    return list(
        session.scalars(
            select(ApiKey)
            .where(ApiKey.user_id == resolved_user.id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        ).all()
    )


def list_all_api_keys(session: Session) -> list[ApiKey]:
    """Return all API keys with owners loaded. Never includes raw secrets."""
    return list(
        session.scalars(
            select(ApiKey)
            .options(joinedload(ApiKey.user))
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        ).unique()
        .all()
    )


def revoke_api_key(
    session: Session,
    api_key_id: int,
    actor: User,
    *,
    as_admin: bool = False,
) -> ApiKey:
    """
    Revoke an API key by id.

    Users may revoke their own keys. Revoking another user's key requires
    as_admin=True and an admin actor (for future service-layer authorization).
    HTTP routes must pass as_admin=False unless an explicit admin policy is wired.
    """
    if actor.id is None:
        raise ValidationError("Actor must be a persisted user.")

    api_key = session.get(ApiKey, api_key_id)
    if api_key is None:
        raise NotFoundError("API key not found.")

    if api_key.user_id != actor.id:
        from auth.permissions import user_is_admin

        allowed = as_admin and user_is_admin(actor)
        if not allowed:
            raise ForbiddenError("You cannot revoke this API key.")

    if api_key.revoked_at is None:
        api_key.revoked_at = _utcnow()
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
        session.refresh(api_key)

    return api_key
