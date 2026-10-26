# Bonus and stand-out items (honest inventory)

This file maps the assignment’s **optional** bonus and stand-out list to what
is actually in this repository. It does **not** claim work that was not done.

## Optional bonus: multi-store provisioning

**Not implemented.**

There is a single WooCommerce store (one WordPress/MariaDB stack). There is no
dashboard UI to create/delete many stores, no per-store namespaces, no
per-store Helm releases, no automatic per-store MCP registration with
store-scoped tool names, no NetworkPolicies between stores, and no
ResourceQuota / LimitRange / per-user store caps / provisioning controller.

Do not demo “compare orders across two stores” — that path does not exist.

---

## Other ways to stand out

| # | Topic | Status in this repo | Where / notes |
|---|--------|---------------------|---------------|
| 1 | Production-like VPS (k3s) | **Partial (docs + values only)** | `deploy/helm/woocommerce/values-prod.yaml`, chart README, root README “VPS / production-like”. Same chart as Kind; TLS annotations for cert-manager are present. **No claim of a live VPS demo in this submission** unless you deploy it separately. |
| 2 | Tool permissions | **Implemented** | Role-based allow/deny: members get read/status tools; admins get mutations such as `create_product` / `update_order_status`. Restricted tools are **not offered** to member chat and are **rejected** on direct MCP invoke. See `apps/gateway/auth/permissions.py`, `apps/gateway/chat/invoke.py`, `apps/gateway/server.py` (proxy gate), tests in `test_permissions.py` / chat tests. |
| 3 | OAuth on the gateway | **Not implemented** | Dashboard uses email/password + JWT cookie; external clients use API keys (`uk_*`). |
| 4 | Handling many tools | **Not implemented** | No meta “search then execute” tool layer; the gateway exposes a modest fixed set of upstream tools. |
| 5 | Hosted MCP servers (by image) | **Not implemented** | Admins register upstreams by **URL** (`/admin/servers`), not by deploying a container image into the cluster. |
| 6 | Security hardening | **Partial** | **Yes:** upstream optional `Authorization` headers encrypted at rest (Fernet) — `apps/gateway/servers/credentials.py`. **No:** SSRF allowlist for admin URLs, default-deny NetworkPolicies, non-root securityContexts across workloads, or least-privilege K8s RBAC for a provisioner (there is no provisioner). |
| 7 | Scaling | **Not implemented** | No demonstrated multi-replica gateway sticky sessions / shared state story; no per-user or per-key rate limits. |
| 8 | Observability | **Partial** | **Yes:** audit log of tool calls (user, server, tool, duration, outcome) and client-safe upstream error messages. **No:** Prometheus/metrics dashboards or SLOs. |
| 9 | Upgrades and rollback | **Partial (documented pattern only)** | Standard Helm upgrade/rollback applies to this chart; not a separate runbook with a recorded production rollback demo. |

---

## What graders should treat as the stand-out

The clear, intentional stand-out against the rubric is **#2 Tool permissions**.

Everything else above is either absent or only supporting documentation /
encryption / audit — not a full bonus or a full stand-out item.
