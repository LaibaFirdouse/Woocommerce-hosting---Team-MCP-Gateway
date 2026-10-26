import { apiFetch } from "./client";
import type { AuthUserResponse, User } from "./types";

export async function register(
  email: string,
  password: string,
): Promise<User> {
  const data = await apiFetch<AuthUserResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  return data.user;
}

export async function login(email: string, password: string): Promise<User> {
  const data = await apiFetch<AuthUserResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  return data.user;
}

export async function fetchCurrentUser(): Promise<User> {
  const data = await apiFetch<AuthUserResponse>("/auth/me", {
    method: "GET",
  });
  return data.user;
}

export async function logout(): Promise<void> {
  await apiFetch<{ detail: string }>("/auth/logout", {
    method: "POST",
  });
}
