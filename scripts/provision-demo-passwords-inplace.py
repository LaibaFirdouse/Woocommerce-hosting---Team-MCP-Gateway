#!/usr/bin/env python3
"""Generate demo passwords, update DB hashes, write /tmp/urumi-demo-passwords.env.

Run inside the gateway container. Does not print plaintext passwords.
"""

from __future__ import annotations

import os
import secrets
import string
import sys

# Ensure /app imports resolve when invoked as a script path.
sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from auth.passwords import hash_password, validate_password_strength  # noqa: E402
from db.lifecycle import is_database_configured, startup_database  # noqa: E402
from db.session import get_session_factory  # noqa: E402
from models.user import User  # noqa: E402

ADMIN_EMAIL = "user.a.admin@urumi.local"
MEMBER_EMAIL = "user.b.member@urumi.local"
LEGACY_ADMIN_EMAIL = "demo.admin.88dfe2@example.com"
OUT_PATH = "/tmp/urumi-demo-passwords.env"


def _gen() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(20)) + "Aa1!"


def _set(session, email: str, password: str) -> str:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        return "missing"
    user.password_hash = hash_password(password)
    user.is_active = True
    role = user.role.value if hasattr(user.role, "value") else user.role
    return f"updated id={user.id} role={role}"


def main() -> int:
    if not is_database_configured():
        print("database_not_configured", file=sys.stderr)
        return 1

    admin_pw = _gen()
    member_pw = _gen()
    validate_password_strength(admin_pw)
    validate_password_strength(member_pw)

    startup_database()
    session = get_session_factory()()
    try:
        lines = [
            f"admin:{_set(session, ADMIN_EMAIL, admin_pw)}",
            f"member:{_set(session, MEMBER_EMAIL, member_pw)}",
            f"legacy_admin:{_set(session, LEGACY_ADMIN_EMAIL, admin_pw)}",
        ]
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("# Urumi dashboard demo credentials — do not commit\n")
        fh.write(f"URUMI_DEMO_ADMIN_EMAIL={ADMIN_EMAIL}\n")
        fh.write(f"URUMI_DEMO_ADMIN_PASSWORD={admin_pw}\n")
        fh.write(f"URUMI_DEMO_MEMBER_EMAIL={MEMBER_EMAIL}\n")
        fh.write(f"URUMI_DEMO_MEMBER_PASSWORD={member_pw}\n")
        fh.write(f"URUMI_DEMO_LEGACY_ADMIN_EMAIL={LEGACY_ADMIN_EMAIL}\n")
    os.chmod(OUT_PATH, 0o600)

    for line in lines:
        print(line)
    print(f"passwords_file={OUT_PATH}")
    print("passwords_provisioned_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
