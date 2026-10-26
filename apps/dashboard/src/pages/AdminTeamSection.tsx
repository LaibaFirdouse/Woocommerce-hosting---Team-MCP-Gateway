import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";

import * as adminUsersApi from "../api/adminUsers";
import type { AdminApiKeyMetadata, AdminUser } from "../api/types";

function formatWhen(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

type Props = {
  onApiFailure: (err: unknown) => Promise<string>;
};

export function AdminTeamSection({ onApiFailure }: Props) {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [keys, setKeys] = useState<AdminApiKeyMetadata[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [memberEmail, setMemberEmail] = useState("");
  const [memberPassword, setMemberPassword] = useState("");
  const [createMemberError, setCreateMemberError] = useState<string | null>(
    null,
  );
  const [createMemberBusy, setCreateMemberBusy] = useState(false);

  const [issueUserId, setIssueUserId] = useState<number | "">("");
  const [issueName, setIssueName] = useState("");
  const [issueError, setIssueError] = useState<string | null>(null);
  const [issueBusy, setIssueBusy] = useState(false);
  const [createdSecret, setCreatedSecret] = useState<{
    rawKey: string;
    name: string;
    ownerEmail: string;
  } | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);

  const [revokeBusyId, setRevokeBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setActionError(null);
    try {
      const [nextUsers, nextKeys] = await Promise.all([
        adminUsersApi.listUsers(),
        adminUsersApi.listTeamApiKeys(),
      ]);
      setUsers(nextUsers);
      setKeys(nextKeys);
      setIssueUserId((current) => {
        if (current !== "") return current;
        if (nextUsers.length === 0) return "";
        const member = nextUsers.find((u) => u.role === "member");
        return member?.id ?? nextUsers[0].id;
      });
    } catch (err) {
      setLoadError(await onApiFailure(err));
      setUsers(null);
      setKeys(null);
    } finally {
      setLoading(false);
    }
  }, [onApiFailure]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    return () => {
      setCreatedSecret(null);
    };
  }, []);

  async function onCreateMember(event: FormEvent) {
    event.preventDefault();
    if (createMemberBusy) return;
    setCreateMemberError(null);
    if (!memberEmail.trim() || !memberPassword) {
      setCreateMemberError("Email and password are required.");
      return;
    }
    setCreateMemberBusy(true);
    try {
      await adminUsersApi.createMember({
        email: memberEmail.trim(),
        password: memberPassword,
      });
      setMemberEmail("");
      setMemberPassword("");
      await reload();
    } catch (err) {
      setCreateMemberError(await onApiFailure(err));
    } finally {
      setCreateMemberBusy(false);
    }
  }

  async function onIssueKey(event: FormEvent) {
    event.preventDefault();
    if (issueBusy || issueUserId === "") return;
    setIssueError(null);
    setCopyStatus(null);
    setCreatedSecret(null);
    const trimmed = issueName.trim();
    if (!trimmed) {
      setIssueError("API key name is required.");
      return;
    }
    setIssueBusy(true);
    try {
      const result = await adminUsersApi.issueApiKeyForUser(
        issueUserId,
        trimmed,
      );
      const owner =
        users?.find((u) => u.id === issueUserId)?.email ??
        result.api_key.owner_email ??
        `user #${issueUserId}`;
      setCreatedSecret({
        rawKey: result.raw_key,
        name: result.api_key.name,
        ownerEmail: owner,
      });
      setIssueName("");
      await reload();
    } catch (err) {
      setIssueError(await onApiFailure(err));
    } finally {
      setIssueBusy(false);
    }
  }

  async function onCopySecret() {
    if (!createdSecret) return;
    try {
      await navigator.clipboard.writeText(createdSecret.rawKey);
      setCopyStatus("Copied. Store it securely — it will not be shown again.");
    } catch {
      setCopyStatus("Copy failed. Select the key and copy manually.");
    }
  }

  async function onRevoke(keyId: number) {
    if (revokeBusyId !== null) return;
    setActionError(null);
    setRevokeBusyId(keyId);
    try {
      await adminUsersApi.revokeTeamApiKey(keyId);
      await reload();
    } catch (err) {
      setActionError(await onApiFailure(err));
    } finally {
      setRevokeBusyId(null);
    }
  }

  return (
    <div className="admin-section">
      <div className="admin-section-head">
        <h2>Team &amp; API keys</h2>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => void reload()}
          disabled={loading}
        >
          Refresh
        </button>
      </div>
      <p className="muted">
        Provision members and issue or revoke their MCP API keys. Raw secrets
        are shown once to administrators and are never stored in the browser.
      </p>

      {loadError ? (
        <div className="alert alert-error" role="alert">
          {loadError}
        </div>
      ) : null}
      {actionError ? (
        <div className="alert alert-error" role="alert">
          {actionError}
        </div>
      ) : null}

      {loading && !users ? (
        <p className="muted" role="status">
          Loading team…
        </p>
      ) : null}

      {users ? (
        <div className="server-table-wrap">
          <table className="server-table">
            <thead>
              <tr>
                <th scope="col">ID</th>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Active</th>
                <th scope="col">Created</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>{user.id}</td>
                  <td>{user.email}</td>
                  <td>{user.role}</td>
                  <td>{user.is_active ? "Yes" : "No"}</td>
                  <td>{formatWhen(user.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <form className="form-stack admin-form" onSubmit={(e) => void onCreateMember(e)}>
        <h3>Create member</h3>
        <p className="muted">
          New accounts are always members. There is no public admin registration.
        </p>
        {createMemberError ? (
          <div className="alert alert-error" role="alert">
            {createMemberError}
          </div>
        ) : null}
        <div className="field">
          <label htmlFor="team-member-email">Email</label>
          <input
            id="team-member-email"
            type="email"
            autoComplete="off"
            value={memberEmail}
            onChange={(e) => setMemberEmail(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="team-member-password">Temporary password</label>
          <input
            id="team-member-password"
            type="password"
            autoComplete="new-password"
            value={memberPassword}
            onChange={(e) => setMemberPassword(e.target.value)}
            required
            minLength={8}
          />
        </div>
        <button className="btn" type="submit" disabled={createMemberBusy}>
          {createMemberBusy ? "Creating…" : "Create member"}
        </button>
      </form>

      <form className="form-stack admin-form" onSubmit={(e) => void onIssueKey(e)}>
        <h3>Issue API key for member</h3>
        {issueError ? (
          <div className="alert alert-error" role="alert">
            {issueError}
          </div>
        ) : null}
        <div className="field">
          <label htmlFor="team-issue-user">Owner</label>
          <select
            id="team-issue-user"
            value={issueUserId === "" ? "" : String(issueUserId)}
            onChange={(e) =>
              setIssueUserId(e.target.value ? Number(e.target.value) : "")
            }
            required
          >
            <option value="" disabled>
              Select user…
            </option>
            {(users ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.email} (#{user.id}, {user.role})
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="team-issue-name">Key name</label>
          <input
            id="team-issue-name"
            type="text"
            value={issueName}
            onChange={(e) => setIssueName(e.target.value)}
            required
          />
        </div>
        <button className="btn" type="submit" disabled={issueBusy}>
          {issueBusy ? "Issuing…" : "Issue key"}
        </button>
      </form>

      {createdSecret ? (
        <div className="alert alert-success" role="status">
          <p>
            <strong>Copy this key now</strong> for {createdSecret.ownerEmail} (
            {createdSecret.name}). It will not be shown again.
          </p>
          <code className="raw-key">{createdSecret.rawKey}</code>
          <div className="row-actions">
            <button type="button" className="btn" onClick={() => void onCopySecret()}>
              Copy
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setCreatedSecret(null)}
            >
              Dismiss
            </button>
          </div>
          {copyStatus ? <p className="muted">{copyStatus}</p> : null}
        </div>
      ) : null}

      {keys ? (
        <div className="server-table-wrap">
          <h3>Team API keys</h3>
          {keys.length === 0 ? (
            <p className="muted">No API keys issued yet.</p>
          ) : (
            <table className="server-table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Prefix</th>
                  <th scope="col">Created</th>
                  <th scope="col">Status</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id}>
                    <td>{key.name}</td>
                    <td>
                      {key.owner_email ?? `user #${key.user_id}`}
                      {key.owner_role ? ` (${key.owner_role})` : ""}
                    </td>
                    <td>
                      <code>{key.key_prefix}</code>
                    </td>
                    <td>{formatWhen(key.created_at)}</td>
                    <td>{key.revoked ? "Revoked" : "Active"}</td>
                    <td>
                      {key.revoked ? (
                        "—"
                      ) : (
                        <button
                          type="button"
                          className="btn btn-ghost btn-small"
                          disabled={revokeBusyId === key.id}
                          onClick={() => void onRevoke(key.id)}
                        >
                          {revokeBusyId === key.id ? "Revoking…" : "Revoke"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  );
}
