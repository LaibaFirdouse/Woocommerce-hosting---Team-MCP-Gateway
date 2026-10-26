import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import * as auditApi from "../api/audit";
import { ApiError } from "../api/client";
import type { AuditLogEntry, AuditOutcome } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const PAGE_SIZE = 25;

type ListState =
  | { status: "loading" }
  | { status: "empty"; entries: []; total: number }
  | { status: "ready"; entries: AuditLogEntry[]; total: number }
  | { status: "error"; message: string; denied?: boolean };

function formatWhen(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return "—";
  return `${ms} ms`;
}

function outcomeClass(outcome: string): string {
  if (outcome === "success") return "status-on";
  if (outcome === "timeout") return "status-warn";
  return "status-off";
}

export function AuditLogsPage() {
  const { logout, isAdmin } = useAuth();
  const navigate = useNavigate();
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [outcome, setOutcome] = useState<AuditOutcome | "">("");
  const [userIdInput, setUserIdInput] = useState("");
  const [appliedUserId, setAppliedUserId] = useState<number | undefined>(
    undefined,
  );
  const [offset, setOffset] = useState(0);

  const handleApiFailure = useCallback(
    async (err: unknown): Promise<string> => {
      if (err instanceof ApiError && err.status === 401) {
        await logout();
        navigate("/login", { replace: true });
        return "Session expired. Please sign in again.";
      }
      if (err instanceof ApiError && err.status === 403) {
        return "Access denied. You cannot view those audit records.";
      }
      if (err instanceof ApiError) {
        return err.message;
      }
      return "Request failed. Check that the gateway is running.";
    },
    [logout, navigate],
  );

  const loadLogs = useCallback(async () => {
    setList({ status: "loading" });
    try {
      const query: Parameters<typeof auditApi.listAuditLogs>[0] = {
        limit: PAGE_SIZE,
        offset,
      };
      if (outcome) {
        query.outcome = outcome;
      }
      // Never send user_id for members — backend is the authority.
      if (isAdmin && appliedUserId !== undefined) {
        query.user_id = appliedUserId;
      }
      const data = await auditApi.listAuditLogs(query);
      if (data.audit_logs.length === 0) {
        setList({ status: "empty", entries: [], total: data.total });
      } else {
        setList({
          status: "ready",
          entries: data.audit_logs,
          total: data.total,
        });
      }
    } catch (err) {
      const message = await handleApiFailure(err);
      const denied = err instanceof ApiError && err.status === 403;
      setList({ status: "error", message, denied });
    }
  }, [appliedUserId, handleApiFailure, isAdmin, offset, outcome]);

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  function onApplyFilters(event: FormEvent) {
    event.preventDefault();
    if (isAdmin) {
      const trimmed = userIdInput.trim();
      if (!trimmed) {
        setAppliedUserId(undefined);
      } else {
        const parsed = Number.parseInt(trimmed, 10);
        if (Number.isNaN(parsed) || parsed < 1) {
          setList({
            status: "error",
            message: "User ID must be a positive integer.",
          });
          return;
        }
        setAppliedUserId(parsed);
      }
    }
    setOffset(0);
  }

  function onOutcomeChange(value: AuditOutcome | "") {
    setOutcome(value);
    setOffset(0);
  }

  const total =
    list.status === "ready" || list.status === "empty" ? list.total : 0;
  const entries =
    list.status === "ready" || list.status === "empty" ? list.entries : [];
  const pageStart = total === 0 || entries.length === 0 ? 0 : offset + 1;
  const pageEnd = offset + entries.length;
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  return (
    <section className="panel admin-panel">
      <h1>Audit logs</h1>
      <p className="lede">
        {isAdmin
          ? "Platform tool-invocation history. Filter by outcome or user when needed."
          : "Your tool-invocation history. Only your own activity is shown."}
      </p>

      <form className="audit-filters" onSubmit={onApplyFilters}>
        <div className="field">
          <label htmlFor="audit-outcome">Outcome</label>
          <select
            id="audit-outcome"
            value={outcome}
            onChange={(e) =>
              onOutcomeChange(e.target.value as AuditOutcome | "")
            }
          >
            <option value="">All</option>
            <option value="success">success</option>
            <option value="error">error</option>
            <option value="timeout">timeout</option>
          </select>
        </div>
        {isAdmin ? (
          <div className="field">
            <label htmlFor="audit-user-id">User ID (optional)</label>
            <input
              id="audit-user-id"
              inputMode="numeric"
              autoComplete="off"
              value={userIdInput}
              onChange={(e) => setUserIdInput(e.target.value)}
              placeholder="e.g. 42"
            />
          </div>
        ) : null}
        <div className="audit-filter-actions">
          {isAdmin ? (
            <button className="btn" type="submit">
              Apply filters
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void loadLogs()}
            disabled={list.status === "loading"}
          >
            Retry
          </button>
        </div>
      </form>

      <div className="admin-section">
        <div className="admin-section-head">
          <h2>Records</h2>
          {list.status === "ready" || list.status === "empty" ? (
            <p className="muted audit-total" role="status">
              {total === 0
                ? "0 matching records"
                : `Showing ${pageStart}–${pageEnd} of ${total}`}
            </p>
          ) : null}
        </div>

        {list.status === "loading" ? (
          <p className="muted" role="status">
            Loading audit logs…
          </p>
        ) : null}

        {list.status === "error" ? (
          <div className="alert alert-error" role="alert">
            {list.message}
          </div>
        ) : null}

        {list.status === "empty" ? (
          <p className="muted" role="status">
            No audit records match these filters.
          </p>
        ) : null}

        {list.status === "ready" ? (
          <div className="server-table-wrap">
            <table className="server-table audit-table">
              <thead>
                <tr>
                  <th scope="col">Timestamp</th>
                  {isAdmin ? <th scope="col">User</th> : null}
                  <th scope="col">Server</th>
                  <th scope="col">Tool</th>
                  <th scope="col">Duration</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Error</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.id}>
                    <td>{formatWhen(entry.timestamp)}</td>
                    {isAdmin ? (
                      <td>{entry.user_id ?? "—"}</td>
                    ) : null}
                    <td>{entry.server_name}</td>
                    <td>
                      <code>{entry.tool_name}</code>
                    </td>
                    <td>{formatDuration(entry.duration_ms)}</td>
                    <td>
                      <span className={outcomeClass(entry.outcome)}>
                        {entry.outcome}
                      </span>
                    </td>
                    <td className="error-cell">
                      {entry.error_message ? entry.error_message : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {(list.status === "ready" || list.status === "empty") && total > 0 ? (
          <div className="pagination-row">
            <button
              type="button"
              className="btn btn-ghost btn-small"
              disabled={!canPrev}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              disabled={!canNext}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
            >
              Next
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
