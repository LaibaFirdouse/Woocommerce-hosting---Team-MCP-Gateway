from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.exceptions import DuplicateEmailError, ValidationError
from auth.passwords import hash_password, validate_password_strength, verify_password
from models.user import User, UserRole


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def _coerce_role(role: UserRole | str) -> UserRole:
    if isinstance(role, UserRole):
        return role
    if not isinstance(role, str):
        raise ValidationError("Invalid role.")
    normalized = role.strip().lower()
    try:
        return UserRole(normalized)
    except ValueError as exc:
        raise ValidationError(
            "Invalid role. Allowed values: admin, member."
        ) from exc


def create_user(
    session: Session,
    email: str,
    password: str,
    role: UserRole | str = UserRole.member,
    *,
    allow_admin_role: bool = False,
) -> User:
    """
    Create a user with a hashed password.

    Admin accounts require allow_admin_role=True (not for public registration).
    """
    normalized_email = normalize_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise ValidationError("A valid email address is required.")

    try:
        validate_password_strength(password)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    resolved_role = _coerce_role(role)
    if resolved_role is UserRole.admin and not allow_admin_role:
        raise ValidationError(
            "Admin accounts cannot be created through this registration path."
        )

    existing = session.scalar(select(User).where(User.email == normalized_email))
    if existing is not None:
        raise DuplicateEmailError("An account with this email already exists.")

    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        role=resolved_role,
        is_active=True,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateEmailError("An account with this email already exists.") from exc

    session.refresh(user)
    return user


def authenticate_user(session: Session, email: str, password: str) -> User | None:
    """Return the active user when credentials match; otherwise None."""
    normalized_email = normalize_email(email)
    if not normalized_email:
        return None

    user = session.scalar(select(User).where(User.email == normalized_email))
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
