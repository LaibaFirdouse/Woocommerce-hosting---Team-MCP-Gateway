export type UserRole = "admin" | "member";

export interface User {
  id: number;
  email: string;
  role: UserRole;
}

export interface AuthUserResponse {
  user: User;
}

export interface ApiErrorBody {
  detail?: string;
}

export type ServerType = "mcp" | "streamable-http";

export interface RegisteredServer {
  id: number;
  name: string;
  server_type: string;
  endpoint_url: string;
  is_enabled: boolean;
  has_credentials: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ServersListResponse {
  servers: RegisteredServer[];
}

export interface ServerResponse {
  server: RegisteredServer;
}

export interface CreateServerInput {
  name: string;
  server_type: ServerType;
  endpoint_url: string;
  is_enabled?: boolean;
  authorization_header?: string;
}

export interface PatchServerInput {
  name?: string;
  server_type?: ServerType;
  endpoint_url?: string;
  is_enabled?: boolean;
  authorization_header?: string;
  clear_credentials?: boolean;
}

export interface ApiKeyMetadata {
  id: number;
  name: string;
  key_prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  revoked: boolean;
}

export interface AdminApiKeyMetadata extends ApiKeyMetadata {
  user_id: number;
  owner_email: string | null;
  owner_role: UserRole | string | null;
}

export interface ApiKeysListResponse {
  api_keys: ApiKeyMetadata[];
}

export interface AdminApiKeysListResponse {
  api_keys: AdminApiKeyMetadata[];
}

export interface CreateApiKeyResponse {
  api_key: ApiKeyMetadata;
  raw_key: string;
}

export interface RevokeApiKeyResponse {
  api_key: ApiKeyMetadata;
}

export interface AdminUser extends User {
  is_active: boolean;
  created_at: string | null;
}

export interface AdminUsersListResponse {
  users: AdminUser[];
}

export interface CreateMemberInput {
  email: string;
  password: string;
}

export interface CreateMemberResponse {
  user: AdminUser;
}

export type AuditOutcome = "success" | "error" | "timeout";

export interface AuditLogEntry {
  id: number;
  user_id: number | null;
  server_name: string;
  tool_name: string;
  timestamp: string | null;
  duration_ms: number | null;
  outcome: string;
  error_message: string | null;
}

export interface AuditLogsListResponse {
  audit_logs: AuditLogEntry[];
  limit: number;
  offset: number;
  total: number;
}

export interface AuditLogsQuery {
  limit?: number;
  offset?: number;
  outcome?: AuditOutcome | "";
  user_id?: number;
}

export type ChatMessageRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatMessageRole;
  content: string;
}

export interface ChatToolActivity {
  name?: string;
  outcome?: string;
  duration_ms?: number | null;
  result_preview?: string | null;
  error?: string | null;
}

export interface ChatResponse {
  message: ChatMessage;
  tool_activity?: ChatToolActivity[] | null;
  rounds?: number;
}
