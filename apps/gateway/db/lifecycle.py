import logging
import os

from db.health import check_database_health

logger = logging.getLogger(__name__)


def is_database_configured() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def startup_database() -> None:
    """
    Initialize persistence when DATABASE_URL is set.

    Verifies connectivity with SELECT 1. Does not create or migrate tables.
    No-op when DATABASE_URL is unset so the MCP gateway can run without Postgres.
    """
    if not is_database_configured():
        logger.info(
            "DATABASE_URL is not set; starting gateway without database persistence."
        )
        return

    result = check_database_health()
    if result.get("status") == "ok":
        logger.info("Database connectivity verified at startup.")
        return

    message = result.get("message") or result.get("database", "unknown error")
    raise RuntimeError(
        "Database startup failed: "
        f"{message} "
        "Check DATABASE_URL, ensure PostgreSQL is running, and apply migrations "
        "with `alembic upgrade head` before starting the gateway."
    )


def shutdown_database() -> None:
    """Release the SQLAlchemy engine on gateway shutdown."""
    from db.session import reset_db

    reset_db()
