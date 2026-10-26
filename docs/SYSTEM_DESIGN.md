# System design and tradeoffs

## Architecture

```text
Browser / MCP client
        │
        ▼
   Ingress (nginx)
   ├── store.*      → WordPress / WooCommerce
   ├── dashboard.*  → React SPA (nginx proxies /auth,/admin,/chat,/audit-logs,/mcp → gateway)
   └── gateway.*    → MCP Gateway (external API-key clients)
        │
        ├── Postgres (users, api_keys, audit_logs, registered_servers)
        ├── LLM (Ollama locally / OpenAI-compatible in prod)
        └── ClusterIP upstreams only
              ├── woocommerce-mcp  → WordPress REST (shared WC consumer key in Secret)
              ├── inventory-mcp
              └── shipping-mcp
```

**Choices**

| Choice | Why |
|--------|-----|
| Single Helm chart + `values-local` / `values-prod` | Same manifests; env differences stay in values, not forks. |
| Gateway as the only public MCP surface | Upstream MCPs stay ClusterIP; clients cannot bypass authz or hit Woo secrets. |
| Dashboard same-origin proxy | HttpOnly session cookie is first-party on `dashboard.*`, avoiding cross-host cookie mismatch. |
| JWT cookie (dashboard) + `uk_*` API keys (MCP) | Browser sessions vs machine clients; keys are hash-stored and revocable. |
| Tool allowlists by role | Members get read/status tools; admins get mutations (`create_product`, etc.). |
| Ollama on the host (local) | Avoids scheduling a large LLM in Kind with limited RAM. |

## Tool aggregation, routing, and failure handling

**Aggregation.** On startup (and after admin server CRUD), the gateway discovers tools from each *enabled* registered upstream (`servers/registry.py`). Tools are registered as gateway proxies. Maps such as `TOOL_OWNER`, `TOOL_UPSTREAM_URL`, and `TOOL_SERVER_NAME` track ownership for disable/delete.

**Routing.** A client `tools/call` (or chat tool call) resolves the tool name → owning upstream URL. The gateway attaches the **server-side** upstream `Authorization` header from encrypted credentials / env — never from the client. Chat uses a stricter allowlist (`CHAT_BASE_TOOLS` ∪ admin tools) so the model only sees tools the principal may invoke (`chat/invoke.py`, `auth/permissions.py`).

**Failure handling.**

- Upstream connect/timeout/HTTP errors are turned into client-safe messages (`format_upstream_error`); secrets and response bodies are not forwarded.
- Per-upstream refresh is isolated: one bad server does not block registering others.
- Disabling an upstream unregisters its tools; discovery no longer lists them.
- Tool invocations are audited (`audit_logs`: actor, server, tool, duration, outcome). Admin team actions (issue/revoke key, create member) are audited with actor `user_id` and target metadata in the message field.
- Chat redacts secrets from tool activity / errors before returning to the browser.

## What changes for production

| Concern | Local (Kind) | Production-like (k3s) |
|---------|--------------|------------------------|
| DNS | `/etc/hosts` → `*.urumi.local` | Real DNS for store/dashboard/gateway |
| Ingress | Port-forward `:8090` if host `:80` is busy | Load balancer / NodePort; TLS via cert-manager (`values-prod.yaml`) |
| URLs | `http://…:8090`, `AUTH_COOKIE_SECURE=false` | `https://…`, `authCookieSecure: true` |
| Images | `urumi/*:local` loaded into Kind | Registry tags (`registry.example.com/…`) |
| Storage | Default StorageClass / hostPath-friendly PVCs | Explicit production StorageClass + sized PVCs in `values-prod.yaml` |
| Secrets | kubectl-created Secrets (not in git) | Same pattern; prefer sealed-secrets / external-secrets in real ops |
| LLM | Host Ollama via `host.docker.internal` | Provider URL + `llm-api-key` Secret |
| Woo REST | HTTP mu-plugin for Basic auth on plain HTTP | HTTPS; disable `allowHttpRestAuth` |

See `deploy/helm/woocommerce/values-prod.yaml` and `deploy/helm/woocommerce/README.md`.
