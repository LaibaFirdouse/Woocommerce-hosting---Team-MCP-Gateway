import os

import pytest

from db.lifecycle import is_database_configured, startup_database
from db.session import get_database_url, reset_db


@pytest.fixture(autouse=True)
def _clean_db_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_db()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield
    reset_db()


def test_is_database_configured_false_when_unset() -> None:
    assert is_database_configured() is False


def test_is_database_configured_true_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    assert is_database_configured() is True


def test_startup_database_noop_without_url() -> None:
    startup_database()


def test_startup_database_raises_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://urumi:change_me@127.0.0.1:59999/urumi_gateway",
    )
    with pytest.raises(RuntimeError, match="Database startup failed"):
        startup_database()


def test_get_database_url_raises_when_missing() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        get_database_url()


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skip live PostgreSQL connectivity test",
)
def test_startup_database_succeeds_with_live_postgres() -> None:
    startup_database()
