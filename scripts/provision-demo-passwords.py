#!/usr/bin/env python3
"""Set dashboard login passwords for known demo users (IDs preserved).

Reads plaintext passwords from environment (never prints them):
  URUMI_DEMO_ADMIN_PASSWORD   → user.a.admin@urumi.local (and optional user 6)
  URUMI_DEMO_MEMBER_PASSWORD  → user.b.member@urumi.local

Intended to run inside the gateway pod (has argon2 + DATABASE_URL).
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import select

from auth.passwords import hash_password, validate_password_strength
from db.lifecycle import is_database_configured, startup_database
from db.session import get_session_factory
from models.user import User


ADMIN_EMAIL = "user.a.admin@urumi.local"
MEMBER_EMAIL = "user.b.member@urumi.local"
# Pre-existing bootstrap admin from earlier demos — keep usable for the same password.
LEGACY_ADMIN_EMAIL = "demo.admin.88dfe2@example.com"


def _require_password(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        print(f"missing_env={env_name}", file=sys.stderr)
        sys.exit(2)
    try:
        validate_password_strength(value)
    except ValueError as exc:
        print(f"invalid_password_env={env_name} reason={exc}", file=sys.stderr)
        sys.exit(2)
    return value


def _set_password(session, email: str, password: str) -> str:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        return "missing"
    user.password_hash = hash_password(password)
    user.is_active = True
    return f"updated id={user.id} role={user.role.value if hasattr(user.role, 'value') else user.role}"


def main() -> int:
    if not is_database_configured():
        print("database_not_configured", file=sys.stderr)
        return 1

    admin_pw = _require_password("URUMI_DEMO_ADMIN_PASSWORD")
    member_pw = _require_password("URUMI_DEMO_MEMBER_PASSWORD")

    startup_database()
    session = get_session_factory()()
    try:
        results = [
            f"admin:{_set_password(session, ADMIN_EMAIL, admin_pw)}",
            f"member:{_set_password(session, MEMBER_EMAIL, member_pw)}",
            f"legacy_admin:{_set_password(session, LEGACY_ADMIN_EMAIL, admin_pw)}",
        ]
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    for line in results:
        print(line)
    print("passwords_provisioned_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
