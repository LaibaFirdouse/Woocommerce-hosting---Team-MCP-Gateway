"""
Persist audit rows for authenticated MCP tool invocations.

Failure-handling policy
-----------------------
``record_tool_invocation`` returns ``True`` only after a successful DB commit.
If the database is unavailable, the write raises, or commit fails, the function
rolls back, logs a warning (never including credentials), and returns ``False``.

``run_audited_tool`` always attempts to persist an audit row in a ``finally``
block. Audit persistence failure does **not** change the tool's success/error
result: the original return value or exception is preserved. Callers must treat
a ``False`` return from ``record_tool_invocation`` as "not persisted" and must
not claim that an audit record exists when it does not.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timezone
from typing import Any, TypeVar

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth.exceptions import ForbiddenError, ValidationError
from auth.principal import get_gateway_principal
from db.session import get_session_factory
from models.audit_log import AuditLog
from models.user import User, UserRole

logger = logging.getLogger(__name__)

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"
OUTCOME_TIMEOUT = "timeout"

ALLOWED_OUTCOMES = frozenset({OUTCOME_SUCCESS, OUTCOME_ERROR, OUTCOME_TIMEOUT})

GATEWAY_SERVER_NAME = "gateway"

_MAX_ERROR_MESSAGE_LENGTH = 500
_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 100

# Redact credentials that might appear in exception strings.
_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"uk_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+"),
)

T = TypeVar("T")


class AuditPersistenceError(RuntimeError):
    """Raised only when a caller opts into fail-fast audit writes."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_error_message(message: str | None) -> str | None:
    if not message:
        return None
    cleaned = str(message).strip()
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    if len(cleaned) > _MAX_ERROR_MESSAGE_LENGTH:
        cleaned = cleaned[:_MAX_ERROR_MESSAGE_LENGTH]
    return cleaned or None


def classify_outcome(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return OUTCOME_TIMEOUT
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return OUTCOME_TIMEOUT
    return OUTCOME_ERROR


def record_tool_invocation(
    *,
    user_id: int | None,
    server_name: str,
    tool_name: str,
    duration_ms: int,
    outcome: str,
    error_message: str | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> bool:
    """
    Persist one AuditLog row.

    Returns True only if the row was committed. Returns False (and logs) on
    any persistence failure. Never logs credentials or raw tokens.
    """
    factory = session_factory or get_session_factory()
    session = None
    try:
        session = factory()
        entry = AuditLog(
            user_id=user_id,
            server_name=server_name,
            tool_name=tool_name,
            timestamp=_utcnow(),
            duration_ms=max(0, int(duration_ms)),
            outcome=outcome,
            error_message=sanitize_error_message(error_message),
        )
        session.add(entry)
        session.commit()
        return True
    except Exception:
        if session is not None:
            try:
                session.rollback()
            except Exception:
                logger.exception("Audit rollback failed for tool=%s", tool_name)
        logger.warning(
            "Audit record was NOT persisted for tool=%s server=%s user_id=%s outcome=%s",
            tool_name,
            server_name,
            user_id,
            outcome,
            exc_info=True,
        )
        return False
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                logger.exception("Audit session close failed for tool=%s", tool_name)


def list_audit_logs(
    session: Session,
    *,
    actor: User,
    limit: int = _DEFAULT_LIST_LIMIT,
    offset: int = 0,
    outcome: str | None = None,
    filter_user_id: int | None = None,
) -> tuple[list[AuditLog], int]:
    """
    List audit rows with authorization applied in the SQL query.

    Members always see only their own non-null ``user_id`` rows.
    Admins see all rows and may optionally filter by ``user_id``
    (including filtering to ``NULL`` only when explicitly requested —
    callers pass an int; null-user rows appear only in unfiltered admin lists).
    """
    if actor.role is not UserRole.admin and filter_user_id is not None:
        raise ForbiddenError("Only administrators may filter audit logs by user_id.")

    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= _MAX_LIST_LIMIT):
        raise ValidationError("limit must be an integer between 1 and 100.")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValidationError("offset must be a non-negative integer.")

    normalized_outcome: str | None = None
    if outcome is not None and str(outcome).strip() != "":
        normalized_outcome = str(outcome).strip().lower()
        if normalized_outcome not in ALLOWED_OUTCOMES:
            raise ValidationError(
                "outcome must be one of: success, error, timeout."
            )

    filters = []
    if actor.role is UserRole.admin:
        if filter_user_id is not None:
            filters.append(AuditLog.user_id == filter_user_id)
    else:
        # Members never see null-user rows or anyone else's activity.
        filters.append(AuditLog.user_id == actor.id)

    if normalized_outcome is not None:
        filters.append(AuditLog.outcome == normalized_outcome)

    count_stmt = select(func.count()).select_from(AuditLog)
    list_stmt = select(AuditLog)
    for clause in filters:
        count_stmt = count_stmt.where(clause)
        list_stmt = list_stmt.where(clause)

    total = int(session.scalar(count_stmt) or 0)
    rows = list(
        session.scalars(
            list_stmt.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return rows, total


async def run_audited_tool(
    *,
    server_name: str,
    tool_name: str,
    operation: Callable[[], Awaitable[T]] | Callable[[], Coroutine[Any, Any, T]],
    session_factory: Callable[[], Any] | None = None,
) -> T:
    """
    Execute ``operation``, then persist an audit row for the authenticated principal.

    ``user_id`` is taken only from ``get_gateway_principal()`` (request-scoped
    contextvar). Tool arguments are never consulted for identity.
    """
    principal = get_gateway_principal()
    user_id = principal.user_id if principal is not None else None
    started = time.perf_counter()
    outcome = OUTCOME_SUCCESS
    error_message: str | None = None
    try:
        return await operation()
    except Exception as exc:
        outcome = classify_outcome(exc)
        error_message = str(exc)
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        persisted = record_tool_invocation(
            user_id=user_id,
            server_name=server_name,
            tool_name=tool_name,
            duration_ms=duration_ms,
            outcome=outcome,
            error_message=error_message,
            session_factory=session_factory,
        )
        if not persisted:
            logger.warning(
                "Tool %s completed with outcome=%s but audit persistence failed",
                tool_name,
                outcome,
            )
