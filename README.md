# Urumi AI — WooCommerce Hosting with Team MCP Gateway

Local demo stack: WordPress/WooCommerce (Kind), custom MCP servers, authenticated MCP gateway, dashboard, and Ollama chat.

## Running services (typical local ports)

| Component | URL |
|-----------|-----|
| Storefront (port-forward) | http://127.0.0.1:8080 |
| WooCommerce MCP | http://127.0.0.1:8000/mcp |
| Gateway | http://127.0.0.1:8001 (`/mcp`, `/chat`, `/auth/*`) |
| Inventory MCP | http://127.0.0.1:8002/mcp |
| Shipping MCP | http://127.0.0.1:8003/mcp |
| Dashboard | http://127.0.0.1:5173 |
| Ollama | http://127.0.0.1:11434 |
| Gateway Postgres | localhost:5432 (`urumi-postgres` container) |

Do not commit secrets. Configure via each app’s `.env` (see `.env.example` for gateway variable **names**).

## Clean-terminal launch (if services are down)

```bash
# 1) Postgres (gateway DB)
docker start urumi-postgres || docker run --name urumi-postgres \
  -e POSTGRES_USER=urumi -e POSTGRES_PASSWORD=change_me \
  -e POSTGRES_DB=urumi_gateway -p 5432:5432 -d postgres:16

# 2) Kind store (if cluster exists)
kubectl --context kind-urumi-local -n urumi get pods
kubectl --context kind-urumi-local -n urumi port-forward svc/wordpress 8080:80

# 3) Upstream MCPs
cd apps/woocommerce-mcp && source .venv/bin/activate && python server.py
cd apps/inventory-mcp && python server.py
cd apps/shipping-mcp && python server.py

# 4) Gateway (loads apps/gateway/.env)
cd apps/gateway && source .venv/bin/activate && alembic upgrade head && python server.py

# 5) Dashboard
cd apps/dashboard && npm install && npm run dev -- --host 127.0.0.1 --port 5173

# 6) Ollama model used by gateway chat
ollama pull qwen2.5:1.5b
```

## Browser demo

1. Open http://127.0.0.1:5173 — register or log in (session cookie).
2. **Chat** — ask for products, inventory, and shipping; confirm tool activity + grounded reply.
3. **API keys** — create a key (copy once); use it as `Authorization: Bearer <key>` against `http://127.0.0.1:8001/mcp`.
4. **Audit logs** — confirm user, server, tool, duration, outcome after calls.
5. **Admin** (admin role) — Servers page: disable/enable an upstream; tools disappear/reappear on `/mcp`.

## Two-user demo

1. Owner/admin: log in at the dashboard; leave Servers and Audit open.
2. Member: register a second account (or use an existing member); create an API key.
3. Member MCP client: discover tools and call read tools **only** via the gateway `/mcp` URL + API key (not upstream ports).
4. Revoke **only** that member’s demo key; next MCP request must return 401.
5. Admin: briefly disable shipping; member discovery loses `get_shipping_status` while inventory remains; re-enable afterward.

## Tests

```bash
cd apps/gateway && .venv/bin/python -m pytest test_upstream.py test_chat_invoke.py test_required_tools.py -q
cd apps/woocommerce-mcp && ../gateway/.venv/bin/python -m pytest -q
cd apps/inventory-mcp && ../gateway/.venv/bin/python -m pytest -q
cd apps/shipping-mcp && ../gateway/.venv/bin/python -m pytest -q
cd apps/dashboard && npm run build && npx vitest run
```

## Helm

`deploy/helm/woocommerce` deploys WordPress + MariaDB into namespace `urumi` (probes, PVCs, resources). Gateway / MCP / dashboard are local processes in this demo, not chart workloads.
