import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router-dom";

import * as apiKeysApi from "../api/apiKeys";
import { ApiError } from "../api/client";
import type { ApiKeyMetadata } from "../api/types";
import { useAuth } from "../auth/AuthContext";

type ListState =
  | { status: "loading" }
  | { status: "empty"; keys: [] }
  | { status: "ready"; keys: ApiKeyMetadata[] }
  | { status: "error"; message: string; denied?: boolean };

function formatWhen(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function ApiKeysPage() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [createBusy, setCreateBusy] = useState(false);
  const [createdSecret, setCreatedSecret] = useState<{
    rawKey: string;
    name: string;
  } | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [revokeBusyId, setRevokeBusyId] = useState<number | null>(null);

  const handleApiFailure = useCallback(
    async (err: unknown): Promise<string> => {
      if (err instanceof ApiError && err.status === 401) {
        await logout();
        navigate("/login", { replace: true });
        return "Session expired. Please sign in again.";
      }
      if (err instanceof ApiError && err.status === 403) {
        return "Access denied. You can only manage your own API keys.";
      }
      if (err instanceof ApiError) {
        return err.message;
      }
      return "Request failed. Check that the gateway is running.";
    },
    [logout, navigate],
  );

  const loadKeys = useCallback(async () => {
    setList({ status: "loading" });
    setActionError(null);
    try {
      const keys = await apiKeysApi.listApiKeys();
      if (keys.length === 0) {
        setList({ status: "empty", keys: [] });
      } else {
        setList({ status: "ready", keys });
      }
    } catch (err) {
      const message = await handleApiFailure(err);
      const denied = err instanceof ApiError && err.status === 403;
      setList({ status: "error", message, denied });
    }
  }, [handleApiFailure]);

  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  // Drop the one-time secret from memory when leaving this page.
  useEffect(() => {
    return () => {
      setCreatedSecret(null);
    };
  }, []);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (createBusy) return;
    setCreateError(null);
    setCopyStatus(null);
    const trimmed = name.trim();
    if (!trimmed) {
      setCreateError("API key name is required.");
      return;
    }
    setCreateBusy(true);
    try {
      const result = await apiKeysApi.createApiKey(trimmed);
      setName("");
      setCreatedSecret({
        rawKey: result.raw_key,
        name: result.api_key.name,
      });
      await loadKeys();
    } catch (err) {
      setCreateError(await handleApiFailure(err));
    } finally {
      setCreateBusy(false);
    }
  }

  async function onCopy() {
    if (!createdSecret) return;
    try {
      await navigator.clipboard.writeText(createdSecret.rawKey);
      setCopyStatus("Copied to clipboard.");
    } catch {
      setCopyStatus("Copy failed. Select the key and copy it manually.");
    }
  }

  function dismissSecret() {
    setCreatedSecret(null);
    setCopyStatus(null);
  }

  async function onRevoke(key: ApiKeyMetadata) {
    if (revokeBusyId !== null || key.revoked) return;
    const confirmed = window.confirm(
      `Revoke API key “${key.name}” (${key.key_prefix}…)? It will stop working immediately.`,
    );
    if (!confirmed) return;
    setActionError(null);
    setRevokeBusyId(key.id);
    try {
      await apiKeysApi.revokeApiKey(key.id);
      await loadKeys();
    } catch (err) {
      setActionError(await handleApiFailure(err));
    } finally {
      setRevokeBusyId(null);
    }
  }

  const keys = list.status === "ready" || list.status === "empty" ? list.keys : [];

  return (
    <section className="panel admin-panel">
      <h1>API keys</h1>
      <p className="lede">
        Personal keys for programmatic access to the gateway. The full secret is
        shown only once at creation — store it somewhere safe.
      </p>

      {createdSecret ? (
        <div className="secret-banner" role="status">
          <h2>Save your new key</h2>
          <p className="secret-warning">
            This is the only time “{createdSecret.name}” will be shown. After
            you leave this view, the secret cannot be retrieved again.
          </p>
          <code className="secret-value" data-testid="raw-api-key">
            {createdSecret.rawKey}
          </code>
          <div className="row-actions">
            <button type="button" className="btn" onClick={() => void onCopy()}>
              Copy key
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={dismissSecret}
            >
              I’ve saved it
            </button>
          </div>
          {copyStatus ? <p className="muted copy-status">{copyStatus}</p> : null}
        </div>
      ) : null}

      <div className="admin-section">
        <div className="admin-section-head">
          <h2>Your keys</h2>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void loadKeys()}
            disabled={list.status === "loading"}
          >
            Retry
          </button>
        </div>

        {actionError ? (
          <div className="alert alert-error" role="alert">
            {actionError}
          </div>
        ) : null}

        {list.status === "loading" ? (
          <p className="muted" role="status">
            Loading API keys…
          </p>
        ) : null}

        {list.status === "error" ? (
          <div className="alert alert-error" role="alert">
            {list.message}
          </div>
        ) : null}

        {list.status === "empty" ? (
          <p className="muted" role="status">
            You have no API keys yet. Create one below.
          </p>
        ) : null}

        {list.status === "ready" ? (
          <div className="server-table-wrap">
            <table className="server-table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Prefix</th>
                  <th scope="col">Created</th>
                  <th scope="col">Last used</th>
                  <th scope="col">Status</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id}>
                    <td>{key.name}</td>
                    <td>
                      <code>{key.key_prefix}</code>
                    </td>
                    <td>{formatWhen(key.created_at)}</td>
                    <td>{formatWhen(key.last_used_at)}</td>
                    <td>
                      <span className={key.revoked ? "status-off" : "status-on"}>
                        {key.revoked ? "Revoked" : "Active"}
                      </span>
                    </td>
                    <td>
                      {key.revoked ? (
                        <span className="muted">—</span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-danger btn-small"
                          disabled={revokeBusyId === key.id}
                          onClick={() => void onRevoke(key)}
                        >
                          {revokeBusyId === key.id ? "Revoking…" : "Revoke"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      <div className="admin-section">
        <h2>Create API key</h2>
        {createError ? (
          <div className="alert alert-error" role="alert">
            {createError}
          </div>
        ) : null}
        <form className="form-stack" onSubmit={(e) => void onCreate(e)} noValidate>
          <div className="field">
            <label htmlFor="api-key-name">Name</label>
            <input
              id="api-key-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder="CI pipeline"
              maxLength={120}
              autoComplete="off"
            />
          </div>
          <button className="btn" type="submit" disabled={createBusy}>
            {createBusy ? "Creating…" : "Create key"}
          </button>
        </form>
      </div>
    </section>
  );
}
