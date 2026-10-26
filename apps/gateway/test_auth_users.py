import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from sqlalchemy import delete

from auth.exceptions import DuplicateEmailError, ValidationError
from auth.passwords import hash_password
from auth.users import authenticate_user, create_user, normalize_email
from db.session import get_session_factory, init_db, reset_db
from models.user import User, UserRole


def test_normalize_email() -> None:
    assert normalize_email("  User@Example.COM ") == "user@example.com"


def test_create_user_success_with_mock_session() -> None:
    session = MagicMock()
    session.scalar.return_value = None

    user = create_user(session, "  New@Example.com ", "password123")

    assert user.email == "new@example.com"
    assert user.role is UserRole.member
    assert user.is_active is True
    assert user.password_hash != "password123"
    session.add.assert_called_once()
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(user)


def test_create_user_duplicate_email_precheck() -> None:
    session = MagicMock()
    session.scalar.return_value = User(email="taken@example.com")

    with pytest.raises(DuplicateEmailError, match="already exists"):
        create_user(session, "taken@example.com", "password123")

    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_create_user_duplicate_email_integrity_error() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    session.commit.side_effect = IntegrityError("insert", {}, Exception("dup"))

    with pytest.raises(DuplicateEmailError, match="already exists"):
        create_user(session, "race@example.com", "password123")

    session.rollback.assert_called_once()


def test_create_user_rejects_empty_password() -> None:
    session = MagicMock()
    with pytest.raises(ValidationError, match="empty"):
        create_user(session, "user@example.com", "   ")


def test_create_user_rejects_short_password() -> None:
    session = MagicMock()
    with pytest.raises(ValidationError, match="at least"):
        create_user(session, "user@example.com", "short")


def test_create_user_rejects_invalid_role() -> None:
    session = MagicMock()
    with pytest.raises(ValidationError, match="Invalid role"):
        create_user(session, "user@example.com", "password123", role="superuser")


def test_create_user_blocks_admin_without_flag() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    with pytest.raises(ValidationError, match="Admin accounts"):
        create_user(session, "admin@example.com", "password123", role="admin")


def test_create_user_allows_admin_with_flag() -> None:
    session = MagicMock()
    session.scalar.return_value = None

    user = create_user(
        session,
        "admin@example.com",
        "password123",
        role=UserRole.admin,
        allow_admin_role=True,
    )
    assert user.role is UserRole.admin


def test_authenticate_user_success() -> None:
    session = MagicMock()
    password = "password123"
    stored = User(
        email="user@example.com",
        password_hash=hash_password(password),
        role=UserRole.member,
        is_active=True,
    )
    session.scalar.return_value = stored

    result = authenticate_user(session, " User@Example.com ", password)
    assert result is stored


def test_authenticate_user_wrong_password() -> None:
    session = MagicMock()
    stored = User(
        email="user@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.member,
        is_active=True,
    )
    session.scalar.return_value = stored

    assert authenticate_user(session, "user@example.com", "wrong-password") is None


def test_authenticate_user_inactive() -> None:
    session = MagicMock()
    stored = User(
        email="user@example.com",
        password_hash=hash_password("password123"),
        role=UserRole.member,
        is_active=False,
    )
    session.scalar.return_value = stored

    assert authenticate_user(session, "user@example.com", "password123") is None


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skip PostgreSQL integration tests",
)
class TestAuthUsersPostgresIntegration:
    @pytest.fixture(autouse=True)
    def _setup_db(self) -> None:
        reset_db()
        init_db()
        yield
        session = get_session_factory()()
        try:
            session.execute(
                delete(User).where(
                    User.email.in_(
                        [
                            "integration-member@example.com",
                            "integration-auth@example.com",
                        ]
                    )
                )
            )
            session.commit()
        finally:
            session.close()
            reset_db()

    def test_create_and_authenticate_user_postgres(self) -> None:
        session = get_session_factory()()
        try:
            created = create_user(
                session,
                "integration-member@example.com",
                "integration-pass-123",
            )
            assert created.id is not None

            authed = authenticate_user(
                session,
                "integration-member@example.com",
                "integration-pass-123",
            )
            assert authed is not None
            assert authed.id == created.id
        finally:
            session.close()
