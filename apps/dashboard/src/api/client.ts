import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Gateway HTTP origin for credentialed cookie sessions.
 *
 * - Vite dev: unset → http://127.0.0.1:8001
 * - Kind dashboard image: empty string → same-origin (nginx proxies /auth,/admin,…)
 * Mixing localhost and 127.0.0.1 (or dashboard vs gateway hosts) breaks cookies.
 */
export function gatewayBaseUrl(): string {
  const raw = import.meta.env.VITE_GATEWAY_API_URL;
  if (raw === undefined || raw === null) {
    return "http://127.0.0.1:8001";
  }
  return String(raw).trim().replace(/\/$/, "");
}

/**
 * Cookie-authenticated fetch against the gateway.
 * Never stores JWTs in localStorage/sessionStorage — the HttpOnly cookie is the session.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${gatewayBaseUrl()}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      data = { detail: text };
    }
  }

  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as ApiErrorBody).detail ?? "Request failed.")
        : "Request failed.";
    throw new ApiError(response.status, detail);
  }

  return data as T;
}
