# Bonus / stand-out items

Items beyond a minimal “MCP proxy + login” demo, and where to find them in this repo.

| Item | Where |
|------|--------|
| Full Kind/Helm platform (WP, MariaDB, Postgres, 3 MCPs, gateway, dashboard, Ingress) | `deploy/helm/woocommerce/` |
| Local vs prod values (TLS annotations, registry images, cookie secure) | `values-local.yaml`, `values-prod.yaml` |
| Stable storefront URL / cart cookies on port-forward `:8090` (MU plugin) | `files/mu-plugins/urumi-public-url.php` |
| HTTP WooCommerce REST auth for local (MU plugin) | `files/mu-plugins/urumi-wc-http-rest-auth.php` |
| Dashboard same-origin nginx proxy (fixes ADMIN badge vs 403 cookie mismatch) | `apps/dashboard/nginx.conf`, `apps/dashboard/Dockerfile` |
| Post-login `/auth/me` revalidation | `apps/dashboard/src/auth/AuthContext.tsx` |
| Role-gated chat + MCP tools (`create_product` admin-only) | `apps/gateway/auth/permissions.py`, `apps/gateway/chat/invoke.py` |
| Admin team management (list/create members, issue/revoke keys for others) | `apps/gateway/admin/users_http.py`, `apps/dashboard/src/pages/AdminTeamSection.tsx` |
| Audit logs with actor + target metadata for admin key ops | `admin_issue_api_key` / `admin_revoke_api_key` in `users_http.py` |
| Upstream disable removes tools from discovery without redeploy | `apps/gateway/servers/registry.py`, Admin → Servers UI |
| Client auth headers / URL overrides stripped before upstream calls | `apps/gateway/chat/invoke.py`, gateway proxy path in `server.py` |
| Secret redaction in chat/audit error paths | `apps/gateway/audit/service.py`, chat agent sanitization |
| Grounded chat replies with tool activity surfaced in UI | `apps/gateway/chat/`, `apps/dashboard/src/pages/ChatPage.tsx` |
| Demo password seed / auth verify scripts (no secrets in git) | `scripts/seed_demo_logins.py`, `scripts/verify_demo_auth.py` |
