import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { User } from "../api/types";

const member: User = {
  id: 1,
  email: "member@example.com",
  role: "member",
};

const admin: User = {
  id: 2,
  email: "admin@example.com",
  role: "admin",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("dashboard auth foundation", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sends unauthenticated users from home to login", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Not authenticated." }, 401));

    render(<App />);

    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();

    // Nav and login form both expose a Register link; assert each in context.
    const primaryNav = screen.getByRole("navigation", { name: "Primary" });
    expect(
      within(primaryNav).getByRole("link", { name: /^register$/i }),
    ).toHaveAttribute("href", "/register");

    const accountPrompt = screen.getByText(/need an account/i).closest("p");
    expect(accountPrompt).not.toBeNull();
    expect(
      within(accountPrompt as HTMLElement).getByRole("link", { name: /^register$/i }),
    ).toHaveAttribute("href", "/register");
  });

  it("restores an authenticated session via /auth/me", async () => {
    const meInits: RequestInit[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        meInits.push(init ?? {});
        expect(init?.credentials).toBe("include");
        expect((init?.method ?? "GET").toUpperCase()).toBe("GET");
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/auth/logout")) {
        throw new Error("session restore must not call logout");
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: /welcome, member@example.com/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^admin$/i })).not.toBeInTheDocument();
    expect(meInits.length).toBeGreaterThanOrEqual(1);
  });

  it("uses 127.0.0.1 as the local gateway host fallback", async () => {
    const { gatewayBaseUrl } = await import("../api/client");
    // With Vitest, unset VITE_GATEWAY_API_URL falls through to the code default.
    // This guards against regressing the default back to a mismatched localhost host.
    expect(gatewayBaseUrl()).toMatch(/^http:\/\/127\.0\.0\.1:8001$/);
  });

  it("logs in successfully and shows home", async () => {
    const user = userEvent.setup();
    let session: User | null = null;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return session
          ? jsonResponse({ user: session })
          : jsonResponse({ detail: "Not authenticated." }, 401);
      }
      if (url.endsWith("/auth/login") && init?.method === "POST") {
        expect(init.credentials).toBe("include");
        session = member;
        return jsonResponse({ user: member });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);
    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();

    await user.type(screen.getByLabelText(/email/i), "member@example.com");
    await user.type(screen.getByLabelText(/password/i), "password123");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(
      await screen.findByRole("heading", { name: /welcome, member@example.com/i }),
    ).toBeInTheDocument();
  });

  it("logs out and returns to login", async () => {
    const user = userEvent.setup();
    let signedIn = true;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return signedIn
          ? jsonResponse({ user: member })
          : jsonResponse({ detail: "Not authenticated." }, 401);
      }
      if (url.endsWith("/auth/logout") && init?.method === "POST") {
        signedIn = false;
        return jsonResponse({ detail: "Logged out." });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: /welcome, member@example.com/i }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /log out/i }));

    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
  });

  it("shows admin nav for admins and blocks members from /admin", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: admin });
      }
      if (url.endsWith("/admin/servers")) {
        return jsonResponse({ servers: [] });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);
    expect(await screen.findByRole("link", { name: /^admin$/i })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /^admin$/i }));
    expect(await screen.findByRole("heading", { name: /^admin$/i })).toBeInTheDocument();
  });

  it("redirects members away from admin", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/admin");
    render(<App />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /welcome, member@example.com/i }),
      ).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: /^admin$/i })).not.toBeInTheDocument();
  });
});
