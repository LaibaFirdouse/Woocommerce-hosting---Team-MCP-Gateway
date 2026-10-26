import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { useNavigate } from "react-router-dom";

import * as chatApi from "../api/chat";
import { ApiError } from "../api/client";
import type { ChatMessage, ChatToolActivity } from "../api/types";
import { useAuth } from "../auth/AuthContext";

type DisplayTurn =
  | { id: string; kind: "user"; content: string }
  | {
      id: string;
      kind: "assistant";
      content: string;
      toolActivity: ChatToolActivity[];
    };

function nextId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeToolActivity(raw: unknown): ChatToolActivity[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item) => item && typeof item === "object") as ChatToolActivity[];
}

function ToolActivityList({ items }: { items: ChatToolActivity[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="tool-activity-list" aria-label="Tool activity">
      {items.map((item, index) => {
        const name = typeof item.name === "string" && item.name ? item.name : "tool";
        const outcome =
          typeof item.outcome === "string" && item.outcome
            ? item.outcome
            : "unknown";
        const ok = outcome === "success";
        const duration =
          typeof item.duration_ms === "number" ? `${item.duration_ms} ms` : null;
        const detail = ok
          ? typeof item.result_preview === "string"
            ? item.result_preview
            : null
          : typeof item.error === "string"
            ? item.error
            : null;
        return (
          <li
            key={`${name}-${index}`}
            className={ok ? "tool-activity-item ok" : "tool-activity-item fail"}
          >
            <div className="tool-activity-head">
              <code>{name}</code>
              <span className={ok ? "status-on" : "status-off"}>{outcome}</span>
              {duration ? <span className="muted">{duration}</span> : null}
            </div>
            {detail ? <p className="tool-activity-detail">{detail}</p> : null}
          </li>
        );
      })}
    </ul>
  );
}

export function ChatPage() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const formId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const [turns, setTurns] = useState<DisplayTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("Ready.");

  useEffect(() => {
    const el = listRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [turns, busy]);

  function conversationPayload(): ChatMessage[] {
    return turns.map((turn) => ({
      role: turn.kind === "user" ? "user" : "assistant",
      content: turn.content,
    }));
  }

  async function sendMessage() {
    const text = draft.trim();
    if (!text || busy) return;

    setError(null);
    setBusy(true);
    setStatus("Sending message…");

    const outbound: ChatMessage[] = [
      ...conversationPayload(),
      { role: "user", content: text },
    ];

    try {
      const result = await chatApi.sendChat(outbound);
      const assistantContent =
        result?.message && typeof result.message.content === "string"
          ? result.message.content
          : "";
      const activity = normalizeToolActivity(result?.tool_activity);

      setTurns((prev) => [
        ...prev,
        { id: nextId("user"), kind: "user", content: text },
        {
          id: nextId("assistant"),
          kind: "assistant",
          content: assistantContent || "(No response text.)",
          toolActivity: activity,
        },
      ]);
      setDraft("");
      setStatus("Response received.");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        await logout();
        navigate("/login", { replace: true });
        setError("Session expired. Please sign in again.");
        setStatus("Session expired.");
        return;
      }
      if (err instanceof ApiError) {
        setError(err.message);
        setStatus("Request failed.");
      } else {
        setError(
          err instanceof Error
            ? err.message
            : "Network error. Check that the gateway is running.",
        );
        setStatus("Network error.");
      }
      // Draft preserved for retry.
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void sendMessage();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  return (
    <section className="panel chat-panel">
      <h1>Chat</h1>
      <p className="lede">
        Ask about products, orders, inventory, or shipping. The gateway runs
        read-only tools on your behalf.
      </p>

      <div
        className="chat-thread"
        ref={listRef}
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="Conversation"
      >
        {turns.length === 0 && !busy ? (
          <p className="muted chat-empty" role="status">
            No messages yet. Ask a question below.
          </p>
        ) : null}

        {turns.map((turn) =>
          turn.kind === "user" ? (
            <article key={turn.id} className="chat-bubble user">
              <h2 className="chat-role">You</h2>
              <p className="chat-content">{turn.content}</p>
            </article>
          ) : (
            <article key={turn.id} className="chat-bubble assistant">
              <h2 className="chat-role">Assistant</h2>
              <p className="chat-content">{turn.content}</p>
              <ToolActivityList items={turn.toolActivity} />
            </article>
          ),
        )}

        {busy ? (
          <p className="muted chat-loading" role="status">
            Thinking…
          </p>
        ) : null}
      </div>

      <p className="visually-hidden" role="status" aria-live="polite">
        {status}
      </p>

      {error ? (
        <div className="alert alert-error" role="alert">
          {error}
        </div>
      ) : null}

      <form id={formId} className="chat-composer" onSubmit={onSubmit}>
        <label htmlFor="chat-draft" className="visually-hidden">
          Message
        </label>
        <textarea
          id="chat-draft"
          name="message"
          rows={3}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={busy}
          placeholder="Ask about stock, an order, or shipping…"
          autoComplete="off"
        />
        <div className="chat-composer-actions">
          <p className="muted chat-hint">
            Enter to send · Shift+Enter for a new line
          </p>
          <button
            className="btn"
            type="submit"
            disabled={busy || !draft.trim()}
          >
            {busy ? "Sending…" : "Send"}
          </button>
        </div>
      </form>
    </section>
  );
}
