from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db.session import get_database_url, get_engine, init_db


def check_database_health() -> dict[str, Any]:
    """
    Verify PostgreSQL connectivity.

    Initializes the engine on first use. Requires DATABASE_URL.
    """
    try:
        get_database_url()
        init_db()
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except RuntimeError as exc:
        return {
            "status": "error",
            "database": "not_configured",
            "message": str(exc),
        }
    except SQLAlchemyError:
        return {
            "status": "error",
            "database": "unreachable",
            "message": "Could not connect to PostgreSQL using DATABASE_URL.",
        }
