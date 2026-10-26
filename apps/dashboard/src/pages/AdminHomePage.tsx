import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import * as serversApi from "../api/servers";
import type { RegisteredServer, ServerType } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { AdminTeamSection } from "./AdminTeamSection";

const SERVER_TYPES: ServerType[] = ["mcp", "streamable-http"];

type ListState =
  | { status: "loading" }
  | { status: "empty"; servers: [] }
  | { status: "ready"; servers: RegisteredServer[] }
  | { status: "error"; message: string; denied?: boolean };

function emptyCreateForm() {
  return {
    name: "",
    server_type: "mcp" as ServerType,
    endpoint_url: "",
    authorization_header: "",
    is_enabled: true,
  };
}

export function AdminHomePage() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [createForm, setCreateForm] = useState(emptyCreateForm);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createBusy, setCreateBusy] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState({
    name: "",
    server_type: "mcp" as ServerType,
    endpoint_url: "",
    authorization_header: "",
    clear_credentials: false,
  });
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [rowBusyId, setRowBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleApiFailure = useCallback(
    async (err: unknown): Promise<string> => {
      if (err instanceof ApiError && err.status === 401) {
        await logout();
        navigate("/login", { replace: true });
        return "Session expired. Please sign in again.";
      }
      if (err instanceof ApiError && err.status === 403) {
        return "Access denied. Admin privileges are required.";
      }
      if (err instanceof ApiError) {
        return err.message;
      }
      return "Request failed. Check that the gateway is running.";
    },
    [logout, navigate],
  );

  const loadServers = useCallback(async () => {
    setList({ status: "loading" });
    setActionError(null);
    try {
      const servers = await serversApi.listServers();
      if (servers.length === 0) {
        setList({ status: "empty", servers: [] });
      } else {
        setList({ status: "ready", servers });
      }
    } catch (err) {
      const message = await handleApiFailure(err);
      const denied = err instanceof ApiError && err.status === 403;
      setList({ status: "error", message, denied });
    }
  }, [handleApiFailure]);

  useEffect(() => {
    void loadServers();
  }, [loadServers]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (createBusy) return;
    setCreateError(null);
    if (!createForm.name.trim() || !createForm.endpoint_url.trim()) {
      setCreateError("Name and endpoint URL are required.");
      return;
    }
    setCreateBusy(true);
    try {
      await serversApi.createServer({
        name: createForm.name.trim(),
        server_type: createForm.server_type,
        endpoint_url: createForm.endpoint_url.trim(),
        is_enabled: createForm.is_enabled,
        authorization_header: createForm.authorization_header.trim() || undefined,
      });
      setCreateForm(emptyCreateForm());
      await loadServers();
    } catch (err) {
      setCreateError(await handleApiFailure(err));
    } finally {
      setCreateBusy(false);
    }
  }

  function startEdit(server: RegisteredServer) {
    setEditingId(server.id);
    setEditError(null);
    setEditForm({
      name: server.name,
      server_type: (SERVER_TYPES.includes(server.server_type as ServerType)
        ? server.server_type
        : "mcp") as ServerType,
      endpoint_url: server.endpoint_url,
      authorization_header: "",
      clear_credentials: false,
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setEditError(null);
    setEditForm({
      name: "",
      server_type: "mcp",
      endpoint_url: "",
      authorization_header: "",
      clear_credentials: false,
    });
  }

  async function onSaveEdit(event: FormEvent) {
    event.preventDefault();
    if (editBusy || editingId === null) return;
    setEditError(null);
    if (!editForm.name.trim() || !editForm.endpoint_url.trim()) {
      setEditError("Name and endpoint URL are required.");
      return;
    }
    setEditBusy(true);
    try {
      await serversApi.patchServer(editingId, {
        name: editForm.name.trim(),
        server_type: editForm.server_type,
        endpoint_url: editForm.endpoint_url.trim(),
        clear_credentials: editForm.clear_credentials || undefined,
        authorization_header: editForm.clear_credentials
          ? undefined
          : editForm.authorization_header.trim() || undefined,
      });
      cancelEdit();
      await loadServers();
    } catch (err) {
      setEditError(await handleApiFailure(err));
    } finally {
      setEditBusy(false);
    }
  }

  async function toggleEnabled(server: RegisteredServer) {
    if (rowBusyId !== null) return;
    setActionError(null);
    setRowBusyId(server.id);
    try {
      await serversApi.patchServer(server.id, {
        is_enabled: !server.is_enabled,
      });
      await loadServers();
    } catch (err) {
      setActionError(await handleApiFailure(err));
    } finally {
      setRowBusyId(null);
    }
  }

  async function onDelete(server: RegisteredServer) {
    if (rowBusyId !== null) return;
    const confirmed = window.confirm(
      `Delete MCP server “${server.name}”? Its tools will be removed from the gateway.`,
    );
    if (!confirmed) return;
    setActionError(null);
    setRowBusyId(server.id);
    try {
      await serversApi.deleteServer(server.id);
      if (editingId === server.id) {
        cancelEdit();
      }
      await loadServers();
    } catch (err) {
      setActionError(await handleApiFailure(err));
    } finally {
      setRowBusyId(null);
    }
  }

  const servers =
    list.status === "ready" || list.status === "empty" ? list.servers : [];

  return (
    <section className="panel admin-panel">
      <h1>Admin</h1>
      <p className="lede">
        Manage team members, API keys, and upstream MCP servers. Authorization
        headers are write-only — stored secrets are never shown in this UI.
      </p>

      {list.status === "error" && list.denied ? (
        <div className="alert alert-error" role="alert">
          {list.message}
        </div>
      ) : null}

      <AdminTeamSection onApiFailure={handleApiFailure} />

      <div className="admin-section">
        <div className="admin-section-head">
          <h2>Registered servers</h2>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void loadServers()}
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
            Loading servers…
          </p>
        ) : null}

        {list.status === "error" && !list.denied ? (
          <div className="alert alert-error" role="alert">
            {list.message}
          </div>
        ) : null}

        {list.status === "empty" ? (
          <p className="muted" role="status">
            No MCP servers registered yet. Add one below.
          </p>
        ) : null}

        {list.status === "ready" ? (
          <div className="server-table-wrap">
            <table className="server-table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Type</th>
                  <th scope="col">Endpoint</th>
                  <th scope="col">Status</th>
                  <th scope="col">Credentials</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {servers.map((server) => (
                  <tr key={server.id}>
                    <td>{server.name}</td>
                    <td>{server.server_type}</td>
                    <td className="endpoint-cell">{server.endpoint_url}</td>
                    <td>
                      <span
                        className={
                          server.is_enabled ? "status-on" : "status-off"
                        }
                      >
                        {server.is_enabled ? "Enabled" : "Disabled"}
                      </span>
                    </td>
                    <td>{server.has_credentials ? "Configured" : "None"}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-small"
                          disabled={rowBusyId === server.id || editBusy}
                          onClick={() => startEdit(server)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-small"
                          disabled={rowBusyId === server.id}
                          onClick={() => void toggleEnabled(server)}
                        >
                          {server.is_enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger btn-small"
                          disabled={rowBusyId === server.id}
                          onClick={() => void onDelete(server)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      {editingId !== null ? (
        <div className="admin-section">
          <h2>Edit server</h2>
          {editError ? (
            <div className="alert alert-error" role="alert">
              {editError}
            </div>
          ) : null}
          <form
            className="form-stack"
            onSubmit={(e) => void onSaveEdit(e)}
            noValidate
          >
            <div className="field">
              <label htmlFor="edit-name">Name</label>
              <input
                id="edit-name"
                value={editForm.name}
                onChange={(e) =>
                  setEditForm((f) => ({ ...f, name: e.target.value }))
                }
                required
              />
            </div>
            <div className="field">
              <label htmlFor="edit-type">Server type</label>
              <select
                id="edit-type"
                value={editForm.server_type}
                onChange={(e) =>
                  setEditForm((f) => ({
                    ...f,
                    server_type: e.target.value as ServerType,
                  }))
                }
              >
                {SERVER_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="edit-url">Endpoint URL</label>
              <input
                id="edit-url"
                value={editForm.endpoint_url}
                onChange={(e) =>
                  setEditForm((f) => ({ ...f, endpoint_url: e.target.value }))
                }
                required
              />
            </div>
            <div className="field">
              <label htmlFor="edit-auth">
                New authorization header (optional)
              </label>
              <input
                id="edit-auth"
                type="password"
                autoComplete="off"
                value={editForm.authorization_header}
                disabled={editForm.clear_credentials}
                onChange={(e) =>
                  setEditForm((f) => ({
                    ...f,
                    authorization_header: e.target.value,
                  }))
                }
                placeholder="Leave blank to keep existing credentials"
              />
            </div>
            <label className="check-row">
              <input
                type="checkbox"
                checked={editForm.clear_credentials}
                onChange={(e) =>
                  setEditForm((f) => ({
                    ...f,
                    clear_credentials: e.target.checked,
                    authorization_header: e.target.checked
                      ? ""
                      : f.authorization_header,
                  }))
                }
              />
              Clear stored credentials
            </label>
            <div className="row-actions">
              <button className="btn" type="submit" disabled={editBusy}>
                {editBusy ? "Saving…" : "Save changes"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={editBusy}
                onClick={cancelEdit}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}

      <div className="admin-section">
        <h2>Register server</h2>
        {createError ? (
          <div className="alert alert-error" role="alert">
            {createError}
          </div>
        ) : null}
        <form
          className="form-stack"
          onSubmit={(e) => void onCreate(e)}
          noValidate
        >
          <div className="field">
            <label htmlFor="create-name">Name</label>
            <input
              id="create-name"
              value={createForm.name}
              onChange={(e) =>
                setCreateForm((f) => ({ ...f, name: e.target.value }))
              }
              required
              placeholder="inventory"
            />
          </div>
          <div className="field">
            <label htmlFor="create-type">Server type</label>
            <select
              id="create-type"
              value={createForm.server_type}
              onChange={(e) =>
                setCreateForm((f) => ({
                  ...f,
                  server_type: e.target.value as ServerType,
                }))
              }
            >
              {SERVER_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="create-url">Endpoint URL</label>
            <input
              id="create-url"
              value={createForm.endpoint_url}
              onChange={(e) =>
                setCreateForm((f) => ({ ...f, endpoint_url: e.target.value }))
              }
              required
              placeholder="http://127.0.0.1:8002/mcp"
            />
          </div>
          <div className="field">
            <label htmlFor="create-auth">
              Authorization header (optional)
            </label>
            <input
              id="create-auth"
              type="password"
              autoComplete="off"
              value={createForm.authorization_header}
              onChange={(e) =>
                setCreateForm((f) => ({
                  ...f,
                  authorization_header: e.target.value,
                }))
              }
              placeholder="Bearer … or Basic …"
            />
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={createForm.is_enabled}
              onChange={(e) =>
                setCreateForm((f) => ({ ...f, is_enabled: e.target.checked }))
              }
            />
            Enable immediately
          </label>
          <button className="btn" type="submit" disabled={createBusy}>
            {createBusy ? "Registering…" : "Register server"}
          </button>
        </form>
      </div>
    </section>
  );
}
