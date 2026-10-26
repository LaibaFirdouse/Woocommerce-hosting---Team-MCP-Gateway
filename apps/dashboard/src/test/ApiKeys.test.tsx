import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { ApiKeyMetadata, User } from "../api/types";

const member: User = {
  id: 1,
  email: "member@example.com",
  role: "member",
};

const otherMember: User = {
  id: 99,
  email: "other@example.com",
  role: "member",
};

function keyFixture(overrides: Partial<ApiKeyMetadata> = {}): ApiKeyMetadata {
  return {
    id: 1,
    name: "CI pipeline",
    key_prefix: "uk_abc123",
    created_at: "2026-01-02T10:00:00.000Z",
    last_used_at: "2026-01-03T12:00:00.000Z",
    revoked_at: null,
    revoked: false,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function parseBody(init?: RequestInit): Record<string, unknown> {
  if (!init?.body || typeof init.body !== "string") {
    return {};
  }
  return JSON.parse(init.body) as Record<string, unknown>;
}

function storageSnapshot(): { local: string; session: string } {
  return {
    local: JSON.stringify({ ...localStorage }),
    session: JSON.stringify({ ...sessionStorage }),
  };
}

describe("personal API key management", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    localStorage.clear();
    sessionStorage.clear();
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lists keys with safe metadata only", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys")) {
        return jsonResponse({
          api_keys: [
            keyFixture(),
            keyFixture({
              id: 2,
              name: "Laptop",
              key_prefix: "uk_xyz789",
              last_used_at: null,
              revoked_at: "2026-01-04T00:00:00.000Z",
              revoked: true,
            }),
          ],
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(await screen.findByText("CI pipeline")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("uk_abc123")).toBeInTheDocument();
    expect(within(table).getByText("Active")).toBeInTheDocument();
    expect(within(table).getByText("Laptop")).toBeInTheDocument();
    expect(within(table).getByText("Revoked")).toBeInTheDocument();
    expect(screen.queryByText(/uk_abc123\./)).not.toBeInTheDocument();
    expect(screen.queryByTestId("raw-api-key")).not.toBeInTheDocument();
  });

  it("creates a key, shows the secret once, and copies it", async () => {
    const user = userEvent.setup();
    const keys: ApiKeyMetadata[] = [];
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ api_keys: [...keys] });
      }
      if (url.endsWith("/auth/api-keys") && init?.method === "POST") {
        expect(init.credentials).toBe("include");
        const body = parseBody(init);
        expect(body).toEqual({ name: "Deploy bot" });
        expect(body).not.toHaveProperty("user_id");
        const created = keyFixture({
          id: 7,
          name: "Deploy bot",
          key_prefix: "uk_deploy1",
          last_used_at: null,
        });
        keys.push(created);
        return jsonResponse(
          {
            api_key: created,
            raw_key: "uk_deploy1.secret-once-only",
          },
          201,
        );
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(
      await screen.findByText(/you have no api keys yet/i),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^name$/i), "Deploy bot");
    await user.click(screen.getByRole("button", { name: /create key/i }));

    const secret = await screen.findByTestId("raw-api-key");
    expect(secret).toHaveTextContent("uk_deploy1.secret-once-only");
    expect(
      screen.getByText(/only time .* will be shown/i),
    ).toBeInTheDocument();
    expect(await screen.findByText("Deploy bot")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /copy key/i }));
    expect(writeText).toHaveBeenCalledWith("uk_deploy1.secret-once-only");
    expect(await screen.findByText(/copied to clipboard/i)).toBeInTheDocument();

    const afterCreate = storageSnapshot();
    expect(afterCreate.local).not.toMatch(/secret-once-only/);
    expect(afterCreate.session).not.toMatch(/secret-once-only/);
  });

  it("clears the secret after dismiss and does not restore it on reload", async () => {
    const user = userEvent.setup();
    let keys: ApiKeyMetadata[] = [];

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ api_keys: [...keys] });
      }
      if (url.endsWith("/auth/api-keys") && init?.method === "POST") {
        const created = keyFixture({
          id: 3,
          name: "Temp",
          key_prefix: "uk_temp99",
        });
        keys = [created];
        return jsonResponse(
          { api_key: created, raw_key: "uk_temp99.do-not-persist" },
          201,
        );
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    const { unmount } = render(<App />);

    await screen.findByText(/you have no api keys yet/i);
    await user.type(screen.getByLabelText(/^name$/i), "Temp");
    await user.click(screen.getByRole("button", { name: /create key/i }));
    expect(await screen.findByTestId("raw-api-key")).toHaveTextContent(
      "uk_temp99.do-not-persist",
    );

    await user.click(screen.getByRole("button", { name: /i’ve saved it/i }));
    expect(screen.queryByTestId("raw-api-key")).not.toBeInTheDocument();

    // Navigate away then remount as a page reload would.
    await user.click(screen.getByRole("link", { name: /^home$/i }));
    expect(
      await screen.findByRole("heading", { name: /welcome, member@example.com/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/do-not-persist/)).not.toBeInTheDocument();

    unmount();
    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(await screen.findByText("Temp")).toBeInTheDocument();
    expect(screen.queryByTestId("raw-api-key")).not.toBeInTheDocument();
    expect(screen.queryByText(/do-not-persist/)).not.toBeInTheDocument();
    expect(storageSnapshot().local).not.toMatch(/do-not-persist/);
    expect(storageSnapshot().session).not.toMatch(/do-not-persist/);
  });

  it("revokes a key after confirmation and refreshes the list", async () => {
    const user = userEvent.setup();
    let current = keyFixture({ revoked: false, revoked_at: null });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ api_keys: [current] });
      }
      if (url.endsWith("/auth/api-keys/1") && init?.method === "DELETE") {
        expect(init.credentials).toBe("include");
        current = {
          ...current,
          revoked: true,
          revoked_at: "2026-01-05T00:00:00.000Z",
        };
        return jsonResponse({ api_key: current });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(await screen.findByText("CI pipeline")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^revoke$/i }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(await screen.findByText("Revoked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^revoke$/i })).not.toBeInTheDocument();
  });

  it("handles list and create API failures", async () => {
    const user = userEvent.setup();
    let failList = true;

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys") && (!init?.method || init.method === "GET")) {
        if (failList) {
          return jsonResponse({ detail: "Gateway unavailable." }, 503);
        }
        return jsonResponse({ api_keys: [] });
      }
      if (url.endsWith("/auth/api-keys") && init?.method === "POST") {
        return jsonResponse({ detail: "API key name is required." }, 400);
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(await screen.findByText(/gateway unavailable/i)).toBeInTheDocument();
    failList = false;
    await user.click(screen.getByRole("button", { name: /^retry$/i }));
    expect(
      await screen.findByText(/you have no api keys yet/i),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /create key/i }));
    expect(
      await screen.findByText(/api key name is required/i),
    ).toBeInTheDocument();
  });

  it("shows access denied on 403", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys")) {
        return jsonResponse({ detail: "Forbidden." }, 403);
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(
      await screen.findByText(/access denied\. you can only manage your own api keys/i),
    ).toBeInTheDocument();
  });

  it("redirects unauthenticated users away from the page", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ detail: "Not authenticated." }, 401);
      }
      if (url.includes("/auth/api-keys")) {
        throw new Error("unauthenticated users must not call api-keys");
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^api keys$/i })).not.toBeInTheDocument();
  });

  it("does not surface another user's keys to the current member", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys")) {
        // Backend scopes by JWT subject; mock returns only the caller's keys.
        return jsonResponse({
          api_keys: [keyFixture({ id: 1, name: "mine-only", key_prefix: "uk_mine" })],
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(await screen.findByText("mine-only")).toBeInTheDocument();
    expect(screen.queryByText(otherMember.email)).not.toBeInTheDocument();
    expect(screen.queryByText(/other-user/i)).not.toBeInTheDocument();
  });

  it("surfaces revoke failures for keys the user does not own", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ api_keys: [keyFixture({ id: 50, name: "foreign" })] });
      }
      if (url.endsWith("/auth/api-keys/50") && init?.method === "DELETE") {
        return jsonResponse(
          { detail: "You may only revoke your own API keys." },
          403,
        );
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    expect(await screen.findByText("foreign")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^revoke$/i }));
    expect(
      await screen.findByText(/access denied\. you can only manage your own api keys/i),
    ).toBeInTheDocument();
  });

  it("does not log raw keys to the console", async () => {
    const user = userEvent.setup();
    const consoleSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ api_keys: [] });
      }
      if (url.endsWith("/auth/api-keys") && init?.method === "POST") {
        return jsonResponse(
          {
            api_key: keyFixture({ name: "Quiet" }),
            raw_key: "uk_quiet.never-console-log",
          },
          201,
        );
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/api-keys");
    render(<App />);

    await screen.findByText(/you have no api keys yet/i);
    await user.type(screen.getByLabelText(/^name$/i), "Quiet");
    await user.click(screen.getByRole("button", { name: /create key/i }));
    expect(await screen.findByTestId("raw-api-key")).toBeInTheDocument();

    const logged = [...consoleSpy.mock.calls, ...errorSpy.mock.calls]
      .flat()
      .map(String)
      .join(" ");
    expect(logged).not.toMatch(/never-console-log/);
  });
});

describe("API keys nav", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("opens the page from primary navigation", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/api-keys")) {
        return jsonResponse({ api_keys: [] });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);
    const primaryNav = await screen.findByRole("navigation", { name: "Primary" });
    await user.click(within(primaryNav).getByRole("link", { name: /api keys/i }));
    expect(
      await screen.findByRole("heading", { name: /^api keys$/i }),
    ).toBeInTheDocument();
  });
});
