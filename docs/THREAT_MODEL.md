# Threat model (short)

Scope: multi-user Team MCP Gateway in front of shared WooCommerce / inventory / shipping MCP servers.

## Shared upstream credentials

WooCommerce consumer keys (and optional upstream `Authorization` headers) live in Kubernetes Secrets / encrypted DB fields and are injected **only** by the gateway when calling ClusterIP upstreams.

A **member never sees** those credentials. What they *can* still do:

- Call **gateway-exposed** tools their role allows (e.g. list products, get inventory/shipping status) — i.e. they inherit the *capabilities* of the shared upstream identity, filtered by gateway authorization.
- They **cannot** call admin-only tools (`create_product`, `update_order_status`, …) even though the upstream WC key could perform those actions if called directly.
- They cannot read Secrets, decrypt stored headers, or open upstream ClusterIP services from outside the cluster.

**Risk residual:** a compromised gateway process or admin account can still use full upstream power. Mitigations: least-privilege WC keys where possible, audit logs, admin-only mutations, ClusterIP-only upstreams.

## Why client tokens are not passed upstream

Dashboard JWTs and member API keys authenticate the **gateway**. Upstream MCPs authenticate with **server-held** credentials.

Passing client Bearer tokens upstream would:

- Leak team session/API keys to every upstream (including future untrusted ones).
- Let clients forge upstream identity or confuse “who called WooCommerce.”
- Break the model where the gateway is the policy enforcement point.

Implementation: chat/MCP strips client `authorization` / URL override arguments (`chat/invoke.py`); proxies use `UPSTREAM_AUTH_HEADERS` / encrypted server credentials only.

## Untrusted upstreams (prompt / tool-description injection)

Tool **names and descriptions** returned by upstreams are shown to the LLM during chat. A malicious or compromised upstream could craft descriptions that manipulate the model (“ignore previous instructions…”, “call create_product…”).

Controls in this design:

- Only **enabled, admin-registered** upstreams are aggregated.
- Chat offers a **role allowlist**; members never receive admin tool schemas.
- Tool execution still checks `role_may_invoke_tool` server-side before proxying.
- Admins can **disable** an upstream; its tools disappear from discovery.
- Treat upstream text as untrusted input; prefer allowlisting and human review of new servers.

Residual risk: a malicious description can still bias the model within allowed tools (e.g. spam `list_products`). Defense in depth: review registrations, monitor audit logs.

## What one team member can do to another

| Action | Member | Admin |
|--------|--------|-------|
| Use another user’s API key | Only if they somehow obtained the raw secret (shown once); gateway does not re-display secrets | Can **issue** and **revoke** any member’s keys |
| Revoke another user’s key via `/auth/api-keys` | **No** (403) | **Yes** (`as_admin`) |
| List other users / create members | **No** | **Yes** (`/admin/users`) |
| See other users’ audit rows | **No** (own rows only) | **Yes** (optional `user_id` filter) |
| Disable upstreams / change routing | **No** | **Yes** (`/admin/servers`) |
| Impersonate another user on `/chat` or `/mcp` | **No** — principal is JWT/`uk_*` subject only; client-supplied `user_id` is ignored | Same |

A malicious member with their own key can still burn shared upstream rate limits / read catalog data within allowlisted tools. They cannot escalate to admin via public registration (`allow_admin_role=False`).
