import { apiFetch } from "./client";
import type { AuditLogsListResponse, AuditLogsQuery } from "./types";

export async function listAuditLogs(
  query: AuditLogsQuery = {},
): Promise<AuditLogsListResponse> {
  const params = new URLSearchParams();
  if (query.limit !== undefined) {
    params.set("limit", String(query.limit));
  }
  if (query.offset !== undefined) {
    params.set("offset", String(query.offset));
  }
  if (query.outcome) {
    params.set("outcome", query.outcome);
  }
  // Only attach user_id when explicitly provided (admins). Members must never
  // send this parameter — the backend returns 403 if they do.
  if (query.user_id !== undefined) {
    params.set("user_id", String(query.user_id));
  }
  const qs = params.toString();
  const path = qs ? `/audit-logs?${qs}` : "/audit-logs";
  return apiFetch<AuditLogsListResponse>(path, { method: "GET" });
}
