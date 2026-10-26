"""Tool-invocation audit logging for the MCP gateway."""

from audit.service import (
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    AuditPersistenceError,
    list_audit_logs,
    record_tool_invocation,
    run_audited_tool,
)

__all__ = [
    "OUTCOME_ERROR",
    "OUTCOME_SUCCESS",
    "OUTCOME_TIMEOUT",
    "AuditPersistenceError",
    "list_audit_logs",
    "record_tool_invocation",
    "run_audited_tool",
]
