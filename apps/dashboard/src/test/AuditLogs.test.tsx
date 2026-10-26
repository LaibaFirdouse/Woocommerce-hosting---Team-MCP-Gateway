import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AuditLogEntry, User } from "../api/types";

const member: User = {
  id: 1,
  email: "member@example.com",
  role: "member",
};

const admin: User = {
  id: 9,
  email: "admin@example.com",
  role: "admin",
};

function entryFixture(overrides: Partial<AuditLogEntry> = {}): AuditLogEntry {
  return {
    id: 1,
    user_id: 1,
    server_name: "inventory",
    tool_name: "get_inventory",
    timestamp: "2026-01-05T12:00:00.000Z",
    duration_ms: 42,
    outcome: "success",
    error_message: null,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function parseUrl(input: RequestInfo | URL): URL {
  return new URL(String(input), "http://127.0.0.1:8001");
}

describe("audit log viewer", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows loading then empty state", async () => {
    const deferred: { resolve: (value: Response) => void } = {
      resolve: () => {
        throw new Error("deferred not ready");
      },
    };
    const pending = new Promise<Response>((resolve) => {
      deferred.resolve = resolve;
    });

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.includes("/audit-logs")) {
        return pending;
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/audit-logs");
    render(<App />);

    expect(await screen.findByText(/loading audit logs/i)).toBeInTheDocument();
    deferred.resolve(
      jsonResponse({ audit_logs: [], limit: 25, offset: 0, total: 0 }),
    );
    expect(
      await screen.findByText(/no audit records match these filters/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/0 matching records/i)).toBeInTheDocument();
  });

  it("lists a member's own audit records without sensitive fields", async () => {
    const seenUrls: string[] = [];

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.includes("/audit-logs")) {
        expect(init?.credentials).toBe("include");
        seenUrls.push(url);
        const parsed = parseUrl(input);
        expect(parsed.searchParams.has("user_id")).toBe(false);
        return jsonResponse({
          audit_logs: [
            {
              ...entryFixture(),
              // Extra fields must never surface in the UI.
              authorization: "Bearer secret-token",
              tool_arguments: { password: "x" },
              tool_result: { token: "y" },
              raw_key: "uk_x.secret",
            },
          ],
          limit: 25,
          offset: 0,
          total: 1,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/audit-logs");
    render(<App />);

    expect(await screen.findByText("get_inventory")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("inventory")).toBeInTheDocument();
    expect(within(table).getByText("success")).toBeInTheDocument();
    expect(within(table).getByText("42 ms")).toBeInTheDocument();
    expect(screen.queryByLabelText(/user id/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/secret-token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/uk_x\.secret/)).not.toBeInTheDocument();
    expect(screen.queryByText(/password/i)).not.toBeInTheDocument();
    expect(seenUrls[0]).toMatch(/limit=25/);
    expect(seenUrls[0]).toMatch(/offset=0/);
  });

  it("sends outcome filter and pagination parameters", async () => {
    const user = userEvent.setup();
    const urls: string[] = [];
    let total = 30;

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.includes("/audit-logs")) {
        urls.push(url);
        const parsed = parseUrl(input);
        const offset = Number(parsed.searchParams.get("offset") || "0");
        const outcome = parsed.searchParams.get("outcome");
        const rows =
          offset === 0
            ? [entryFixture({ id: 1, outcome: outcome || "success" })]
            : [entryFixture({ id: 2, tool_name: "page_two" })];
        return jsonResponse({
          audit_logs: rows,
          limit: 25,
          offset,
          total,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/audit-logs");
    render(<App />);

    expect(await screen.findByText("get_inventory")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/^outcome$/i), "error");
    await screen.findByText("get_inventory");

    const withOutcome = urls.find((u) => u.includes("outcome=error"));
    expect(withOutcome).toBeTruthy();
    expect(withOutcome).toMatch(/limit=25/);
    expect(withOutcome).toMatch(/offset=0/);
    expect(withOutcome).not.toMatch(/user_id=/);

    expect(screen.getByText(/showing 1–1 of 30/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^next$/i }));
    expect(await screen.findByText("page_two")).toBeInTheDocument();
    expect(urls.some((u) => u.includes("offset=25"))).toBe(true);
  });

  it("lets admins filter by user ID", async () => {
    const user = userEvent.setup();
    const urls: string[] = [];

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.includes("/audit-logs")) {
        urls.push(url);
        const parsed = parseUrl(input);
        const filterUser = parsed.searchParams.get("user_id");
        return jsonResponse({
          audit_logs: [
            entryFixture({
              id: 7,
              user_id: filterUser ? Number(filterUser) : null,
              tool_name: filterUser ? "filtered_tool" : "all_tools",
              error_message: filterUser ? "safe detail" : null,
            }),
          ],
          limit: 25,
          offset: 0,
          total: 1,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/audit-logs");
    render(<App />);

    expect(await screen.findByText("all_tools")).toBeInTheDocument();
    expect(screen.getByLabelText(/user id/i)).toBeInTheDocument();
    expect(
      within(screen.getByRole("table")).getByRole("columnheader", {
        name: /^user$/i,
      }),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText(/user id/i), "42");
    await user.click(screen.getByRole("button", { name: /apply filters/i }));

    expect(await screen.findByText("filtered_tool")).toBeInTheDocument();
    expect(urls.some((u) => u.includes("user_id=42"))).toBe(true);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("safe detail")).toBeInTheDocument();
  });

  it("renders API errors and retries", async () => {
    const user = userEvent.setup();
    let fail = true;

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.includes("/audit-logs")) {
        if (fail) {
          return jsonResponse({ detail: "Gateway unavailable." }, 503);
        }
        return jsonResponse({
          audit_logs: [entryFixture()],
          limit: 25,
          offset: 0,
          total: 1,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/audit-logs");
    render(<App />);

    expect(await screen.findByText(/gateway unavailable/i)).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: /^retry$/i }));
    expect(await screen.findByText("get_inventory")).toBeInTheDocument();
  });

  it("shows access denied on 403", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.includes("/audit-logs")) {
        return jsonResponse(
          { detail: "Only administrators may filter audit logs by user_id." },
          403,
        );
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/audit-logs");
    render(<App />);

    expect(
      await screen.findByText(/access denied\. you cannot view those audit records/i),
    ).toBeInTheDocument();
  });

  it("redirects unauthenticated users away from audit logs", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ detail: "Not authenticated." }, 401);
      }
      if (url.includes("/audit-logs")) {
        throw new Error("unauthenticated users must not call audit-logs");
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/audit-logs");
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: /sign in/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /^audit logs$/i }),
    ).not.toBeInTheDocument();
  });

  it("opens audit from primary navigation", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.includes("/audit-logs")) {
        return jsonResponse({
          audit_logs: [],
          limit: 25,
          offset: 0,
          total: 0,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);
    const primaryNav = await screen.findByRole("navigation", { name: "Primary" });
    await user.click(within(primaryNav).getByRole("link", { name: /^audit$/i }));
    expect(
      await screen.findByRole("heading", { name: /^audit logs$/i }),
    ).toBeInTheDocument();
  });
});
