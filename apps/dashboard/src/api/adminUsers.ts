import { apiFetch } from "./client";
import type {
  AdminApiKeyMetadata,
  AdminApiKeysListResponse,
  AdminUser,
  AdminUsersListResponse,
  CreateApiKeyResponse,
  CreateMemberInput,
  CreateMemberResponse,
  RevokeApiKeyResponse,
} from "./types";

export async function listUsers(): Promise<AdminUser[]> {
  const data = await apiFetch<AdminUsersListResponse>("/admin/users", {
    method: "GET",
  });
  return data.users;
}

export async function createMember(
  input: CreateMemberInput,
): Promise<AdminUser> {
  const data = await apiFetch<CreateMemberResponse>("/admin/users", {
    method: "POST",
    body: JSON.stringify({
      email: input.email,
      password: input.password,
    }),
  });
  return data.user;
}

export async function listTeamApiKeys(): Promise<AdminApiKeyMetadata[]> {
  const data = await apiFetch<AdminApiKeysListResponse>("/admin/api-keys", {
    method: "GET",
  });
  return data.api_keys;
}

export async function issueApiKeyForUser(
  userId: number,
  name: string,
): Promise<CreateApiKeyResponse & { api_key: AdminApiKeyMetadata }> {
  return apiFetch(`/admin/users/${userId}/api-keys`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function revokeTeamApiKey(
  keyId: number,
): Promise<RevokeApiKeyResponse> {
  return apiFetch<RevokeApiKeyResponse>(`/admin/api-keys/${keyId}`, {
    method: "DELETE",
  });
}
