# Urumi Gateway MCP

MCP gateway server (Streamable HTTP) that proxies tools from upstream MCP servers.
HTTP session auth (`/auth/*`), API-key management (`/auth/api-keys`), admin MCP
server CRUD (`/admin/servers`), and MCP transport auth (`/mcp`) are supported.
Tool invocations are persisted to `audit_logs`; authenticated users can read
them via `GET /audit-logs` (members: own rows only; admins: all rows, optional
`user_id` filter).

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt` (MCP Python SDK 1.x / FastMCP, httpx)

## Installation

```bash
cd apps/gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

| Variable                   | Default                        | Description                         |
|----------------------------|--------------------------------|-------------------------------------|
| `GATEWAY_HOST`             | `0.0.0.0`                      | HTTP bind address                   |
| `GATEWAY_PORT`             | `8001`                         | HTTP port                           |
| `WOOCOMMERCE_MCP_URL`      | `http://127.0.0.1:8000/mcp`    | WooCommerce upstream MCP URL        |
| `INVENTORY_MCP_URL`        | `http://127.0.0.1:8002/mcp`    | Inventory upstream MCP URL          |
| `SHIPPING_MCP_URL`         | `http://127.0.0.1:8003/mcp`    | Shipping upstream MCP URL           |
| `GATEWAY_UPSTREAM_TIMEOUT` | `20`                           | Upstream connect/read timeout (sec) |
| `DATABASE_URL`             | *(required for DB features)*   | PostgreSQL URL for gateway persistence |
| `AUTH_JWT_SECRET`          | *(required for `/auth/*` and MCP auth)* | Random string ≥32 chars; signs session JWTs |
| `AUTH_JWT_EXPIRE_MINUTES`  | `1440`                         | JWT / cookie lifetime (minutes) |
| `AUTH_COOKIE_SECURE`       | `false`                        | Set `true` in production (HTTPS only cookies) |
| `AUTH_COOKIE_NAME`         | `urumi_session`                | HttpOnly session cookie name |
| `MCP_AUTH_REQUIRED`        | `true`                         | Protect `/mcp` with Bearer JWT or API keys. Set `false` only for local development without credentials (not recommended). |
| `GATEWAY_PUBLIC_URL`       | `http://127.0.0.1:8001`        | Public base URL used in MCP auth resource metadata |
| `UPSTREAM_SECRETS_KEY`     | *(optional)*                   | Fernet key or ≥32-char secret for encrypting optional upstream `Authorization` headers. Falls back to a key derived from `AUTH_JWT_SECRET`. |
| `GATEWAY_CORS_ORIGINS`     | `http://127.0.0.1:5173,http://localhost:5173` | Explicit dashboard origins for credentialed CORS (never `*`). Prefer opening the dashboard at `http://127.0.0.1:5173` so it matches `VITE_GATEWAY_API_URL=http://127.0.0.1:8001`. |

### Audit logs (HTTP)

`GET /audit-logs` — JWT cookie or Bearer JWT only (API-key Bearers are rejected).

| Query     | Default | Notes |
|-----------|---------|--------|
| `limit`   | `50`    | Integer 1–100 |
| `offset`  | `0`     | Non-negative integer |
| `outcome` | —       | Optional: `success`, `error`, or `timeout` |
| `user_id` | —       | Administrators only; members receive HTTP 403 if supplied |

Response: `{ "audit_logs": [...], "limit", "offset", "total" }`. Each entry includes
`id`, `user_id`, `server_name`, `tool_name`, `timestamp`, `duration_ms`, `outcome`,
and a sanitized `error_message`. Rows are ordered by `timestamp` DESC, then `id` DESC.
Members never see other users' rows or `user_id=NULL` rows.

### Chat agent

`POST /chat` — JWT cookie or Bearer JWT only (API-key Bearers rejected). Body:
`{ "messages": [ { "role": "user"|"assistant", "content": "..." }, ... ] }`
(at least one user message). Response:
`{ "message", "tool_activity", "rounds" }`.

Server-side loop uses the OpenAI-compatible client and only permits read-only
tools: `list_products`, `list_orders`, `get_order`, `get_inventory`,
`get_shipping_status`. Calls go through `invoke_chat_tool` (audited); routing
and credentials come from the live registry, never from client input. Dashboard
UI and conversation persistence are not included yet.

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_KEY` | *(required for LLM)* | Provider API key (gateway process only) |
| `LLM_MODEL` | *(required for LLM)* | Model id |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL |
| `CHAT_MAX_TOOL_ROUNDS` | `6` | Max tool iterations (agent loop) |
| `CHAT_TIMEOUT_SECONDS` | `60` | Bounded HTTP timeout for LLM calls |

The Streamable HTTP MCP endpoint is served at **`/mcp`** (FastMCP default), e.g. `http://127.0.0.1:8001/mcp`.

When `MCP_AUTH_REQUIRED` is true (default), every `/mcp` request requires `Authorization: Bearer <jwt|api_key>`. `/auth/register` and `/auth/login` stay public; `/auth/me` keeps its existing cookie/Bearer checks. `DATABASE_URL` and `AUTH_JWT_SECRET` are required for MCP auth.

## PostgreSQL (local)

Example using Docker:

```bash
docker run --name urumi-postgres -e POSTGRES_USER=urumi -e POSTGRES_PASSWORD=change_me \
  -e POSTGRES_DB=urumi_gateway -p 5432:5432 -d postgres:16
```

Set the URL (see root `.env.example`):

```bash
export DATABASE_URL=postgresql+psycopg://urumi:change_me@localhost:5432/urumi_gateway
```

## Database migrations (Alembic)

From `apps/gateway` with `DATABASE_URL` set:

```bash
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

Create a new revision after adding ORM models under `models/`:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Database health check

```bash
cd apps/gateway
export DATABASE_URL=postgresql+psycopg://urumi:change_me@localhost:5432/urumi_gateway
python -c "from db.health import check_database_health; print(check_database_health())"
```

Expected when PostgreSQL is reachable: `{'status': 'ok', 'database': 'connected'}`.

## Run locally

Start upstream servers first (WooCommerce `:8000`, Inventory `:8002`, Shipping `:8003`), then:

```bash
cd apps/gateway
source .venv/bin/activate
python server.py
```

Optional custom bind:

```bash
GATEWAY_HOST=127.0.0.1 GATEWAY_PORT=8001 python server.py
```

## Gateway tools

| Tool                      | Description                                           |
|---------------------------|-------------------------------------------------------|
| `gateway_health`          | Returns `{"status": "ok"}`                            |
| `list_upstream_tools`     | Lists tools from **WooCommerce MCP only** (legacy)    |
| `list_all_upstream_tools` | Status + tools for **all** configured upstreams       |

Proxied upstream tools are registered at startup with the same names and schemas as on each upstream server.

## Upstream behavior

- If one upstream is down at startup, the gateway still starts and registers tools from reachable upstreams.
- Duplicate tool names: the **first** upstream to register a name wins; later collisions are **skipped** and logged (not overwritten).

Connect with any MCP client that supports Streamable HTTP against the gateway URL above.
