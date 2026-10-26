import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as authApi from "../api/auth";
import { ApiError } from "../api/client";
import type { User } from "../api/types";

interface AuthContextValue {
  user: User | null;
  bootstrapping: boolean;
  authError: string | null;
  clearAuthError: () => void;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  isAdmin: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function restore() {
      try {
        const current = await authApi.fetchCurrentUser();
        if (!cancelled) {
          setUser(current);
        }
      } catch (err) {
        if (!cancelled) {
          setUser(null);
          // Expired/missing session is expected on first visit — not a hard error.
          if (!(err instanceof ApiError && err.status === 401)) {
            setAuthError(
              err instanceof Error ? err.message : "Unable to restore session.",
            );
          }
        }
      } finally {
        if (!cancelled) {
          setBootstrapping(false);
        }
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const clearAuthError = useCallback(() => setAuthError(null), []);

  const login = useCallback(async (email: string, password: string) => {
    setAuthError(null);
    await authApi.login(email, password);
    // Re-read /auth/me from the cookie so the UI never shows ADMIN from the
    // login JSON while the browser still holds a different session cookie.
    const next = await authApi.fetchCurrentUser();
    setUser(next);
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    setAuthError(null);
    await authApi.register(email, password);
    // Registration does not set a cookie — follow with login for a session.
    await authApi.login(email, password);
    const next = await authApi.fetchCurrentUser();
    setUser(next);
  }, []);

  const logout = useCallback(async () => {
    setAuthError(null);
    try {
      await authApi.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      bootstrapping,
      authError,
      clearAuthError,
      login,
      register,
      logout,
      isAdmin: user?.role === "admin",
    }),
    [
      user,
      bootstrapping,
      authError,
      clearAuthError,
      login,
      register,
      logout,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
