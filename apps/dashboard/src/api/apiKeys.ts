import { apiFetch } from "./client";
import type {
  ApiKeyMetadata,
  ApiKeysListResponse,
  CreateApiKeyResponse,
  RevokeApiKeyResponse,
} from "./types";

export async function listApiKeys(): Promise<ApiKeyMetadata[]> {
  const data = await apiFetch<ApiKeysListResponse>("/auth/api-keys", {
    method: "GET",
  });
  return data.api_keys;
}

export async function createApiKey(
  name: string,
): Promise<CreateApiKeyResponse> {
  // Ownership is the JWT session subject — never send user_id.
  return apiFetch<CreateApiKeyResponse>("/auth/api-keys", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function revokeApiKey(
  keyId: number,
): Promise<ApiKeyMetadata> {
  const data = await apiFetch<RevokeApiKeyResponse>(
    `/auth/api-keys/${keyId}`,
    { method: "DELETE" },
  );
  return data.api_key;
}
