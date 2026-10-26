import logging
import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select
from auth.api_keys import API_KEY_PREFIX, create_api_key, revoke_api_key, verify_api_key
from auth.exceptions import ForbiddenError, ValidationError
from auth.passwords import verify_password
from auth.users import create_user
from db.session import get_session_factory, init_db, reset_db
from models.api_key import ApiKey
from models.user import User, UserRole


def test_create_api_key_format_and_prefix() -> None:
    session = MagicMock()
    session.get.return_value = User(
        id=1,
        email="user@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )

    record, raw_key = create_api_key(session, 1, "ci key")

    assert raw_key.startswith(API_KEY_PREFIX)
    assert "." in raw_key
    assert record.key_prefix == raw_key.split(".", 1)[0]
    assert record.name == "ci key"
    assert record.user_id == 1
    assert record.key_hash != raw_key
    session.commit.assert_called_once()


def test_create_api_key_raw_secret_not_stored_in_db() -> None:
    session = MagicMock()
    session.get.return_value = User(
        id=1,
        email="user@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )

    record, raw_key = create_api_key(session, 1, "deploy")

    assert raw_key not in record.key_hash
    assert verify_password(raw_key, record.key_hash) is True


def test_create_api_key_rejects_inactive_user() -> None:
    session = MagicMock()
    session.get.return_value = User(
        id=1,
        email="inactive@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=False,
    )

    with pytest.raises(ValidationError, match="inactive"):
        create_api_key(session, 1, "blocked")

    session.add.assert_not_called()


def test_create_api_key_rejects_empty_name() -> None:
    session = MagicMock()
    session.get.return_value = User(
        id=1,
        email="user@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )

    with pytest.raises(ValidationError, match="name"):
        create_api_key(session, 1, "   ")


def _random_secret() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def test_verify_api_key_success() -> None:
    from auth.passwords import hash_password

    session = MagicMock()
    user = User(
        id=7,
        email="user@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )
    raw_key = f"{API_KEY_PREFIX}pubid.{_random_secret()}"

    api_key = ApiKey(
        id=10,
        user_id=7,
        name="n",
        key_prefix=f"{API_KEY_PREFIX}pubid",
        key_hash=hash_password(raw_key),
        user=user,
    )
    session.scalar.return_value = api_key

    result = verify_api_key(session, raw_key)
    assert result is user
    assert api_key.last_used_at is not None
    session.commit.assert_called_once()


def test_verify_api_key_malformed_and_invalid() -> None:
    session = MagicMock()
    session.scalar.return_value = None

    assert verify_api_key(session, "") is None
    assert verify_api_key(session, "not-a-key") is None
    assert verify_api_key(session, "uk_nodot") is None
    assert verify_api_key(session, "xx_pubid.secret") is None


def test_verify_api_key_wrong_secret() -> None:
    from auth.passwords import hash_password

    session = MagicMock()
    user = User(
        id=1,
        email="u@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )
    raw_key = f"{API_KEY_PREFIX}abc123.{_random_secret()}"
    api_key = ApiKey(
        id=1,
        user_id=1,
        name="n",
        key_prefix=f"{API_KEY_PREFIX}abc123",
        key_hash=hash_password(raw_key),
        user=user,
    )
    session.scalar.return_value = api_key

    assert verify_api_key(session, f"{API_KEY_PREFIX}abc123.wrong-secret") is None
    session.commit.assert_not_called()


def test_verify_api_key_revoked() -> None:
    from auth.passwords import hash_password
    from datetime import datetime, timezone

    session = MagicMock()
    user = User(
        id=1,
        email="u@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )
    raw_key = f"{API_KEY_PREFIX}revoked.{_random_secret()}"
    api_key = ApiKey(
        id=1,
        user_id=1,
        name="n",
        key_prefix=f"{API_KEY_PREFIX}revoked",
        key_hash=hash_password(raw_key),
        revoked_at=datetime.now(timezone.utc),
        user=user,
    )
    session.scalar.return_value = api_key

    assert verify_api_key(session, raw_key) is None
    session.commit.assert_not_called()


def test_verify_api_key_inactive_user() -> None:
    from auth.passwords import hash_password

    session = MagicMock()
    user = User(
        id=1,
        email="u@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=False,
    )
    raw_key = f"{API_KEY_PREFIX}inactive.{_random_secret()}"
    api_key = ApiKey(
        id=1,
        user_id=1,
        name="n",
        key_prefix=f"{API_KEY_PREFIX}inactive",
        key_hash=hash_password(raw_key),
        user=user,
    )
    session.scalar.return_value = api_key

    assert verify_api_key(session, raw_key) is None
    session.commit.assert_not_called()


def test_verify_api_key_unknown_prefix() -> None:
    session = MagicMock()
    session.scalar.return_value = None

    assert (
        verify_api_key(session, f"{API_KEY_PREFIX}missing.{_random_secret()}")
        is None
    )


def test_revoke_api_key_owner() -> None:
    session = MagicMock()
    actor = User(
        id=2,
        email="owner@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )
    api_key = ApiKey(
        id=5,
        user_id=2,
        name="k",
        key_prefix=f"{API_KEY_PREFIX}own",
        key_hash="hash",
        revoked_at=None,
    )
    session.get.return_value = api_key

    revoked = revoke_api_key(session, 5, actor)
    assert revoked.revoked_at is not None
    session.commit.assert_called_once()


def test_revoke_api_key_denied_for_other_user() -> None:
    session = MagicMock()
    actor = User(
        id=1,
        email="other@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )
    api_key = ApiKey(
        id=5,
        user_id=99,
        name="k",
        key_prefix=f"{API_KEY_PREFIX}x",
        key_hash="hash",
    )
    session.get.return_value = api_key

    with pytest.raises(ForbiddenError, match="cannot revoke"):
        revoke_api_key(session, 5, actor)

    session.commit.assert_not_called()


def test_revoke_api_key_admin_with_flag() -> None:
    session = MagicMock()
    actor = User(
        id=1,
        email="admin@example.com",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    api_key = ApiKey(
        id=5,
        user_id=99,
        name="k",
        key_prefix=f"{API_KEY_PREFIX}adm",
        key_hash="hash",
    )
    session.get.return_value = api_key

    revoked = revoke_api_key(session, 5, actor, as_admin=True)
    assert revoked.revoked_at is not None


def test_api_key_repr_does_not_leak_secrets() -> None:
    session = MagicMock()
    session.get.return_value = User(
        id=1,
        email="user@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )

    record, raw_key = create_api_key(session, 1, "repr test")
    text = repr(record)
    assert raw_key not in text
    assert record.key_hash not in text


def test_create_and_verify_no_secret_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    from auth.passwords import hash_password

    session = MagicMock()
    owner = User(
        id=1,
        email="user@example.com",
        password_hash="x",
        role=UserRole.member,
        is_active=True,
    )
    session.get.return_value = owner

    with caplog.at_level(logging.DEBUG):
        record, raw_key = create_api_key(session, 1, "logging")
        lookup = ApiKey(
            id=record.id,
            user_id=record.user_id,
            name=record.name,
            key_prefix=record.key_prefix,
            key_hash=hash_password(raw_key),
            user=owner,
        )
        session.scalar.return_value = lookup
        verify_api_key(session, raw_key)

    combined = caplog.text
    assert raw_key not in combined
    assert lookup.key_hash not in combined


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skip PostgreSQL integration tests",
)
class TestApiKeysPostgresIntegration:
    _EMAIL = "integration-apikeys@example.com"

    @pytest.fixture(autouse=True)
    def _setup_db(self) -> None:
        reset_db()
        init_db()
        yield
        session = get_session_factory()()
        try:
            user = session.scalar(select(User).where(User.email == self._EMAIL))
            if user is not None:
                session.execute(delete(ApiKey).where(ApiKey.user_id == user.id))
                session.execute(delete(User).where(User.id == user.id))
            session.commit()
        finally:
            session.close()
            reset_db()

    def test_create_verify_revoke_flow(self) -> None:
        session = get_session_factory()()
        try:
            user = create_user(session, self._EMAIL, "integration-pass-123")
            record, raw_key = create_api_key(session, user, "integration key")

            stored = session.scalar(
                select(ApiKey).where(ApiKey.id == record.id)
            )
            assert stored is not None
            assert stored.key_prefix == raw_key.split(".", 1)[0]
            assert stored.key_hash != raw_key

            authed = verify_api_key(session, raw_key)
            assert authed is not None
            assert authed.id == user.id

            session.refresh(stored)
            assert stored.last_used_at is not None

            revoke_api_key(session, record.id, user)
            assert verify_api_key(session, raw_key) is None
        finally:
            session.close()
