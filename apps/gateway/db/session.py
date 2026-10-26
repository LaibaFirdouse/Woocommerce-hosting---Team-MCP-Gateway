import os
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Export a PostgreSQL URL before initializing "
            "the database, for example: "
            "postgresql+psycopg://USER:PASSWORD@localhost:5432/urumi_gateway"
        )
    return url


def init_db() -> None:
    """Create the SQLAlchemy engine and session factory."""
    global _engine, _session_factory

    if _engine is not None and _session_factory is not None:
        return

    _engine = create_engine(
        get_database_url(),
        pool_pre_ping=True,
        future=True,
    )
    _session_factory = sessionmaker(
        bind=_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


def get_engine() -> Engine:
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        init_db()
    assert _session_factory is not None
    return _session_factory


def get_session() -> Generator[Session, Any, None]:
    """Yield a database session and close it when done."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_db() -> None:
    """Dispose engine and session factory (for tests and graceful shutdown)."""
    global _engine, _session_factory

    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
