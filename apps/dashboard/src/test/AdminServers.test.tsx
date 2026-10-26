import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { RegisteredServer, User } from "../api/types";

const admin: User = {
  id: 2,
  email: "admin@example.com",
  role: "admin",
};

const member: User = {
  id: 1,
  email: "member@example.com",
  role: "member",
};

function serverFixture(
  overrides: Partial<RegisteredServer> = {},
): RegisteredServer {
  return {
    id: 1,
    name: "inventory",
    server_type: "mcp",
    endpoint_url: "http://127.0.0.1:8002/mcp",
    is_enabled: true,
    has_credentials: true,
    created_at: "2026-01-01T00:00:00",
    updated_at: "2026-01-01T00:00:00",
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

/** Team panel on /admin always loads these; keep tests focused on servers. */
function teamApiStub(url: string): Response | null {
  if (url.endsWith("/admin/users") && !url.includes("api-keys")) {
    return jsonResponse({
      users: [
        {
          id: admin.id,
          email: admin.email,
          role: admin.role,
          is_active: true,
          created_at: "2026-01-01T00:00:00",
        },
      ],
    });
  }
  if (url.endsWith("/admin/api-keys")) {
    return jsonResponse({ api_keys: [] });
  }
  return null;
}

describe("admin MCP server management", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows loading then empty state for admins with no servers", async () => {
    const deferred: {
      resolve: (value: Response) => void;
    } = {
      resolve: () => {
        throw new Error("list deferred not ready");
      },
    };
    const listPromise = new Promise<Response>((resolve) => {
      deferred.resolve = resolve;
    });

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers")) {
        return listPromise;
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    expect(await screen.findByText(/loading servers/i)).toBeInTheDocument();
    deferred.resolve(jsonResponse({ servers: [] }));
    expect(
      await screen.findByText(/no mcp servers registered yet/i),
    ).toBeInTheDocument();
  });

  it("lists servers without exposing credential secrets", async () => {
    const listed = serverFixture({
      has_credentials: true,
    });

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers")) {
        return jsonResponse({
          servers: [
            {
              ...listed,
              // Backend must never return these; ensure UI ignores them if present.
              authorization_header: "Bearer super-secret",
              auth_header_ciphertext: "cipher",
            },
          ],
        });
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    expect(await screen.findByText("inventory")).toBeInTheDocument();
    const serversHeading = screen.getByRole("heading", {
      name: /registered servers/i,
    });
    const serversSection = serversHeading.closest(
      ".admin-section",
    ) as HTMLElement;
    const table = within(serversSection).getByRole("table");
    expect(within(table).getByText("mcp")).toBeInTheDocument();
    expect(
      within(table).getByText("http://127.0.0.1:8002/mcp"),
    ).toBeInTheDocument();
    expect(within(table).getByText("Enabled")).toBeInTheDocument();
    expect(within(table).getByText("Configured")).toBeInTheDocument();
    expect(screen.queryByText(/super-secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/cipher/i)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(/super-secret/i)).not.toBeInTheDocument();
  });

  it("registers a server, clears credential input, and refreshes the list", async () => {
    const user = userEvent.setup();
    const servers: RegisteredServer[] = [];

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ servers: [...servers] });
      }
      if (url.endsWith("/admin/servers") && init?.method === "POST") {
        expect(init.credentials).toBe("include");
        const body = parseBody(init);
        expect(body.authorization_header).toBe("Bearer temp-token");
        expect(JSON.stringify(body)).not.toMatch(/password|secret_key/i);
        const created = serverFixture({
          id: 10,
          name: String(body.name),
          endpoint_url: String(body.endpoint_url),
          server_type: String(body.server_type),
          has_credentials: true,
        });
        servers.push(created);
        return jsonResponse({ server: created }, 201);
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    expect(
      await screen.findByText(/no mcp servers registered yet/i),
    ).toBeInTheDocument();

    const registerSection = screen
      .getByRole("heading", { name: /register server/i })
      .closest(".admin-section") as HTMLElement;
    await user.type(
      within(registerSection).getByLabelText(/^name$/i),
      "catalog",
    );
    await user.clear(within(registerSection).getByLabelText(/endpoint url/i));
    await user.type(
      within(registerSection).getByLabelText(/endpoint url/i),
      "http://127.0.0.1:9000/mcp",
    );
    await user.type(
      within(registerSection).getByLabelText(/authorization header \(optional\)/i),
      "Bearer temp-token",
    );
    await user.click(
      within(registerSection).getByRole("button", { name: /register server/i }),
    );

    expect(await screen.findByText("catalog")).toBeInTheDocument();
    expect(
      within(registerSection).getByLabelText(/authorization header \(optional\)/i),
    ).toHaveValue("");
  });

  it("edits, enables/disables, and deletes with confirmation", async () => {
    const user = userEvent.setup();
    let current = serverFixture({ is_enabled: true, has_credentials: true });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ servers: current ? [current] : [] });
      }
      if (url.includes("/admin/servers/") && init?.method === "PATCH") {
        const body = parseBody(init);
        expect(body).not.toHaveProperty("auth_header_ciphertext");
        if (typeof body.is_enabled === "boolean") {
          current = { ...current, is_enabled: body.is_enabled };
        }
        if (typeof body.name === "string") {
          current = {
            ...current,
            name: body.name,
            endpoint_url: String(body.endpoint_url ?? current.endpoint_url),
            server_type: String(body.server_type ?? current.server_type),
            has_credentials: body.clear_credentials
              ? false
              : body.authorization_header
                ? true
                : current.has_credentials,
          };
        }
        return jsonResponse({ server: current });
      }
      if (url.includes("/admin/servers/") && init?.method === "DELETE") {
        current = null as unknown as RegisteredServer;
        return jsonResponse({ detail: "Registered server deleted." });
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    expect(await screen.findByText("inventory")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^disable$/i }));
    expect(await screen.findByText("Disabled")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^edit$/i }));
    const editSection = screen
      .getByRole("heading", { name: /edit server/i })
      .closest(".admin-section");
    expect(editSection).toBeTruthy();
    const editName = within(editSection as HTMLElement).getByLabelText(/^name$/i);
    await user.clear(editName);
    await user.type(editName, "inventory-v2");
    await user.click(
      within(editSection as HTMLElement).getByLabelText(/clear stored credentials/i),
    );
    await user.click(
      within(editSection as HTMLElement).getByRole("button", {
        name: /save changes/i,
      }),
    );

    expect(await screen.findByText("inventory-v2")).toBeInTheDocument();
    expect(screen.getByText("None")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(
      await screen.findByText(/no mcp servers registered yet/i),
    ).toBeInTheDocument();
  });

  it("surfaces API errors and retries the list", async () => {
    const user = userEvent.setup();
    let failList = true;

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers")) {
        if (failList) {
          return jsonResponse({ detail: "Gateway unavailable." }, 503);
        }
        return jsonResponse({ servers: [serverFixture()] });
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    expect(await screen.findByText(/gateway unavailable/i)).toBeInTheDocument();
    failList = false;
    await user.click(screen.getByRole("button", { name: /^retry$/i }));
    expect(await screen.findByText("inventory")).toBeInTheDocument();
  });

  it("shows access denied when the API returns 403", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers")) {
        return jsonResponse({ detail: "Admin privileges required." }, 403);
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    expect(
      await screen.findByText(/access denied\. admin privileges are required/i),
    ).toBeInTheDocument();
  });

  it("blocks members from the admin page", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.includes("/admin/servers")) {
        throw new Error("member must not call admin servers API from UI route");
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /welcome, member@example.com/i }),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/registered servers/i)).not.toBeInTheDocument();
  });

  it("shows registration errors without logging secrets", async () => {
    const user = userEvent.setup();
    const consoleSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ servers: [] });
      }
      if (url.endsWith("/admin/servers") && init?.method === "POST") {
        return jsonResponse(
          { detail: "A registered server with this name already exists." },
          409,
        );
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    await screen.findByText(/no mcp servers registered yet/i);
    const registerSection = screen
      .getByRole("heading", { name: /register server/i })
      .closest(".admin-section") as HTMLElement;
    await user.type(within(registerSection).getByLabelText(/^name$/i), "dup");
    await user.type(
      within(registerSection).getByLabelText(/endpoint url/i),
      "http://127.0.0.1:1/mcp",
    );
    await user.type(
      within(registerSection).getByLabelText(/authorization header \(optional\)/i),
      "Bearer never-log-me",
    );
    await user.click(
      within(registerSection).getByRole("button", { name: /register server/i }),
    );

    expect(
      await screen.findByText(/already exists/i),
    ).toBeInTheDocument();

    const logged = [...consoleSpy.mock.calls, ...errorSpy.mock.calls]
      .flat()
      .map(String)
      .join(" ");
    expect(logged).not.toMatch(/never-log-me/);
  });
});

describe("admin nav still works with server list", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("opens admin from nav for admins", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers")) {
        return jsonResponse({ servers: [] });
      }
      const team = teamApiStub(url);
      if (team) return team;
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);
    const primaryNav = await screen.findByRole("navigation", { name: "Primary" });
    await user.click(within(primaryNav).getByRole("link", { name: /^admin$/i }));
    expect(await screen.findByRole("heading", { name: /^admin$/i })).toBeInTheDocument();
    expect(
      await screen.findByText(/no mcp servers registered yet/i),
    ).toBeInTheDocument();
  });
});
