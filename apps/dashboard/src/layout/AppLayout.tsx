import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

export function AppLayout() {
  const { user, logout, isAdmin, bootstrapping } = useAuth();

  return (
    <div className="app-shell">
      <header className="topnav">
        <NavLink to="/" className="brand" end>
          Urumi <span>AI</span>
        </NavLink>
        <nav className="nav-links" aria-label="Primary">
          {user ? (
            <>
              <NavLink to="/" end>
                Home
              </NavLink>
              <NavLink to="/chat">Chat</NavLink>
              <NavLink to="/api-keys">API keys</NavLink>
              <NavLink to="/audit-logs">Audit</NavLink>
              {isAdmin ? (
                <NavLink to="/admin">Admin</NavLink>
              ) : null}
            </>
          ) : (
            <>
              <NavLink to="/login">Sign in</NavLink>
              <NavLink to="/register">Register</NavLink>
            </>
          )}
        </nav>
        <div className="nav-meta">
          {bootstrapping ? (
            <span>Checking session…</span>
          ) : user ? (
            <>
              <span>
                {user.email}{" "}
                {isAdmin ? <span className="badge">Admin</span> : null}
              </span>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void logout()}
              >
                Log out
              </button>
            </>
          ) : null}
        </div>
      </header>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
