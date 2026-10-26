import { apiFetch } from "./client";
import type {
  CreateServerInput,
  PatchServerInput,
  RegisteredServer,
  ServerResponse,
  ServersListResponse,
} from "./types";

export async function listServers(): Promise<RegisteredServer[]> {
  const data = await apiFetch<ServersListResponse>("/admin/servers", {
    method: "GET",
  });
  return data.servers;
}

export async function createServer(
  input: CreateServerInput,
): Promise<RegisteredServer> {
  const body: Record<string, unknown> = {
    name: input.name,
    server_type: input.server_type,
    endpoint_url: input.endpoint_url,
    is_enabled: input.is_enabled ?? true,
  };
  if (input.authorization_header?.trim()) {
    body.authorization_header = input.authorization_header.trim();
  }
  const data = await apiFetch<ServerResponse>("/admin/servers", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data.server;
}

export async function patchServer(
  serverId: number,
  input: PatchServerInput,
): Promise<RegisteredServer> {
  const body: Record<string, unknown> = {};
  if (input.name !== undefined) body.name = input.name;
  if (input.server_type !== undefined) body.server_type = input.server_type;
  if (input.endpoint_url !== undefined) body.endpoint_url = input.endpoint_url;
  if (input.is_enabled !== undefined) body.is_enabled = input.is_enabled;
  if (input.clear_credentials === true) {
    body.clear_credentials = true;
  } else if (input.authorization_header !== undefined) {
    const trimmed = input.authorization_header.trim();
    if (trimmed) {
      body.authorization_header = trimmed;
    }
  }
  const data = await apiFetch<ServerResponse>(`/admin/servers/${serverId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  return data.server;
}

export async function deleteServer(serverId: number): Promise<void> {
  await apiFetch<{ detail: string }>(`/admin/servers/${serverId}`, {
    method: "DELETE",
  });
}
