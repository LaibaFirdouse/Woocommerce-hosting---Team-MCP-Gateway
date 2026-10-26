import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { User } from "../api/types";

const member: User = {
  id: 1,
  email: "member@example.com",
  role: "member",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function parseBody(init?: RequestInit): Record<string, unknown> {
  if (!init?.body || typeof init.body !== "string") return {};
  return JSON.parse(init.body) as Record<string, unknown>;
}

describe("dashboard chat", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lets a logged-in user open /chat from navigation", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(<App />);
    const primaryNav = await screen.findByRole("navigation", { name: "Primary" });
    await user.click(within(primaryNav).getByRole("link", { name: /^chat$/i }));
    expect(await screen.findByRole("heading", { name: /^chat$/i })).toBeInTheDocument();
    expect(
      screen.getByText(/no messages yet/i),
    ).toBeInTheDocument();
  });

  it("sends POST /chat with credentials and renders the assistant reply", async () => {
    const user = userEvent.setup();
    const chatCalls: RequestInit[] = [];

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/chat") && init?.method === "POST") {
        chatCalls.push(init);
        expect(init.credentials).toBe("include");
        const body = parseBody(init);
        expect(body.messages).toEqual([
          { role: "user", content: "What is in stock for ABC?" },
        ]);
        return jsonResponse({
          message: {
            role: "assistant",
            content: "SKU ABC has 3 units.",
          },
          tool_activity: [
            {
              name: "get_inventory",
              outcome: "success",
              duration_ms: 42,
              result_preview: '{"sku":"ABC","qty":3}',
            },
          ],
          rounds: 2,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    expect(await screen.findByRole("heading", { name: /^chat$/i })).toBeInTheDocument();
    await user.type(
      screen.getByLabelText(/^message$/i),
      "What is in stock for ABC?",
    );
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("SKU ABC has 3 units.")).toBeInTheDocument();
    expect(screen.getByText("What is in stock for ABC?")).toBeInTheDocument();
    expect(chatCalls).toHaveLength(1);

    const activity = screen.getByLabelText(/tool activity/i);
    expect(within(activity).getByText("get_inventory")).toBeInTheDocument();
    expect(within(activity).getByText("success")).toBeInTheDocument();
    expect(within(activity).getByText("42 ms")).toBeInTheDocument();
    expect(within(activity).getByText(/"qty":3/)).toBeInTheDocument();
  });

  it("renders failed tool activity distinctly", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/chat") && init?.method === "POST") {
        return jsonResponse({
          message: { role: "assistant", content: "I could not load that." },
          tool_activity: [
            {
              name: "get_order",
              outcome: "error",
              duration_ms: 12,
              error: "Upstream unavailable.",
            },
          ],
          rounds: 2,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    await screen.findByRole("heading", { name: /^chat$/i });
    await user.type(screen.getByLabelText(/^message$/i), "order 1");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("I could not load that.")).toBeInTheDocument();
    const activity = screen.getByLabelText(/tool activity/i);
    expect(within(activity).getByText("error")).toBeInTheDocument();
    expect(within(activity).getByText("Upstream unavailable.")).toBeInTheDocument();
  });

  it("shows loading and prevents duplicate submissions", async () => {
    const user = userEvent.setup();
    const deferred: { resolve: (value: Response) => void } = {
      resolve: () => {
        throw new Error("deferred not ready");
      },
    };
    const pending = new Promise<Response>((resolve) => {
      deferred.resolve = resolve;
    });
    let chatPosts = 0;

    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/chat") && init?.method === "POST") {
        chatPosts += 1;
        return pending;
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    await screen.findByRole("heading", { name: /^chat$/i });
    await user.type(screen.getByLabelText(/^message$/i), "hello");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText(/thinking/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sending/i })).toBeDisabled();
    expect(screen.getByLabelText(/^message$/i)).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /sending/i }));
    expect(chatPosts).toBe(1);

    deferred.resolve(
      jsonResponse({
        message: { role: "assistant", content: "Hi!" },
        tool_activity: [],
        rounds: 1,
      }),
    );
    expect(await screen.findByText("Hi!")).toBeInTheDocument();
  });

  it("preserves the draft and shows an error on HTTP failure", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/chat") && init?.method === "POST") {
        return jsonResponse({ detail: "Chat is not configured on this server." }, 503);
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    await screen.findByRole("heading", { name: /^chat$/i });
    const draft = "retry me please";
    await user.type(screen.getByLabelText(/^message$/i), draft);
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(
      await screen.findByText(/chat is not configured/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^message$/i)).toHaveValue(draft);
    // Conversation unchanged — draft was not committed as a user bubble.
    expect(screen.getByText(/no messages yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^you$/i })).not.toBeInTheDocument();
  });

  it("preserves the draft on network failure", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/chat") && init?.method === "POST") {
        throw new TypeError("Failed to fetch");
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    await screen.findByRole("heading", { name: /^chat$/i });
    await user.type(screen.getByLabelText(/^message$/i), "still here");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/failed to fetch/i);
    expect(screen.getByLabelText(/^message$/i)).toHaveValue("still here");
  });

  it("handles missing or malformed tool_activity safely", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/chat") && init?.method === "POST") {
        return jsonResponse({
          message: { role: "assistant", content: "Okay." },
          tool_activity: null,
          rounds: 1,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    await screen.findByRole("heading", { name: /^chat$/i });
    await user.type(screen.getByLabelText(/^message$/i), "hi");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Okay.")).toBeInTheDocument();
    expect(screen.queryByLabelText(/tool activity/i)).not.toBeInTheDocument();
  });

  it("handles sparse tool activity entries without crashing", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ user: member });
      }
      if (url.endsWith("/chat") && init?.method === "POST") {
        return jsonResponse({
          message: { role: "assistant", content: "Done." },
          tool_activity: [{}, { name: "get_inventory" }, "bad", null],
          rounds: 1,
        });
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    await screen.findByRole("heading", { name: /^chat$/i });
    await user.type(screen.getByLabelText(/^message$/i), "hi");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Done.")).toBeInTheDocument();
    const activity = screen.getByLabelText(/tool activity/i);
    expect(within(activity).getAllByText("unknown").length).toBeGreaterThan(0);
    expect(within(activity).getByText("get_inventory")).toBeInTheDocument();
  });

  it("redirects logged-out users away from /chat", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ detail: "Not authenticated." }, 401);
      }
      if (url.includes("/chat")) {
        throw new Error("logged-out users must not call chat");
      }
      return jsonResponse({ detail: "not found" }, 404);
    });

    window.history.pushState({}, "", "/chat");
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: /sign in/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^chat$/i })).not.toBeInTheDocument();
  });
});
