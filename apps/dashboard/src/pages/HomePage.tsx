import { useAuth } from "../auth/AuthContext";

export function HomePage() {
  const { user, isAdmin } = useAuth();

  return (
    <section className="panel">
      <h1>Welcome{user ? `, ${user.email}` : ""}</h1>
      <p className="lede">
        Use Chat for product and order questions, or manage API keys and audit
        logs from the nav. Admins can also register MCP servers.
      </p>
      <div className="home-grid">
        <div className="home-card">
          <h2>Session</h2>
          <p className="muted">
            Authenticated via gateway HttpOnly cookie. Role:{" "}
            <strong>{user?.role ?? "unknown"}</strong>
            {isAdmin ? " (admin navigation enabled)" : ""}.
          </p>
        </div>
      </div>
    </section>
  );
}
