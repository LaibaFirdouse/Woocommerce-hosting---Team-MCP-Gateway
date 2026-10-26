# Urumi platform Helm chart

Same chart for Kind (local) and production-like k3s. Environment differences
live in `values-local.yaml` and `values-prod.yaml`.

## Components

| Workload | Service | Image (local) |
|----------|---------|---------------|
| WordPress | `wordpress:80` | `wordpress:php8.3-apache` |
| MariaDB | `mariadb:3306` | `mariadb:11.4` |
| Postgres (gateway) | `postgres:5432` | `postgres:16` |
| WooCommerce MCP | `woocommerce-mcp:8000` | `urumi/woocommerce-mcp:local` |
| Inventory MCP | `inventory-mcp:8002` | `urumi/inventory-mcp:local` |
| Shipping MCP | `shipping-mcp:8003` | `urumi/shipping-mcp:local` |
| Gateway | `gateway:8001` | `urumi/gateway:local` |
| Dashboard | `dashboard:80` | `urumi/dashboard:local` |

Upstream MCP Services are **ClusterIP only** (not on Ingress). External MCP
clients use `http://gateway.urumi.local/mcp` (or the prod gateway host).

## Secrets (create before upgrade)

```bash
# Names only — fill values from your existing env files without committing them.
kubectl -n urumi create secret generic urumi-gateway-secrets \
  --from-literal=auth-jwt-secret=... \
  --from-literal=postgres-password=... \
  --from-literal=database-url='postgresql+psycopg://urumi:...@postgres:5432/urumi_gateway' \
  --from-literal=llm-api-key=ollama

kubectl -n urumi create secret generic urumi-woocommerce-mcp-secrets \
  --from-literal=consumer-key=... \
  --from-literal=consumer-secret=...
```

`urumi-store-secrets` (MariaDB) should already exist from the original store deploy.

## Build & load images (Kind)

```bash
# Build
docker build -t urumi/woocommerce-mcp:local apps/woocommerce-mcp
docker build -t urumi/inventory-mcp:local apps/inventory-mcp
docker build -t urumi/shipping-mcp:local apps/shipping-mcp
docker build -t urumi/gateway:local apps/gateway
# Dashboard SPA uses same-origin API (empty VITE_GATEWAY_API_URL); nginx in the
# image proxies /auth,/admin,/chat,/audit-logs,/mcp to the gateway Service so
# the HttpOnly session cookie stays first-party on dashboard.urumi.local.
docker build -t urumi/dashboard:local apps/dashboard

# Load into Kind (image-archive avoids kind load docker-image /tmp issues)
mkdir -p .image-tars
for n in woocommerce-mcp inventory-mcp shipping-mcp gateway dashboard; do
  docker save urumi/$n:local -o .image-tars/$n.tar
  kind load image-archive .image-tars/$n.tar --name urumi-local
done
# Also load postgres:16 if the node cannot pull it quickly
docker save postgres:16 -o .image-tars/postgres16.tar
kind load image-archive .image-tars/postgres16.tar --name urumi-local
```

## Install / upgrade

```bash
# Ingress controller (Kind) — once
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/kind/deploy.yaml
kubectl label node urumi-local-control-plane ingress-ready=true --overwrite

helm upgrade --install woocommerce deploy/helm/woocommerce \
  --kube-context kind-urumi-local \
  -n urumi --create-namespace \
  -f deploy/helm/woocommerce/values.yaml \
  -f deploy/helm/woocommerce/values-local.yaml
```

## Local hosts / Ingress access

Add to `/etc/hosts` (for browser Host headers when using a port-forward):

```text
127.0.0.1 store.urumi.local dashboard.urumi.local gateway.urumi.local
```

**Important:** on this machine, host `:80` is often occupied by Apache, so
`http://store.urumi.local` (no port) hits Ubuntu Apache, not Kind.

```bash
# Recommended: port-forward ingress-nginx (keep this running)
kubectl --context kind-urumi-local -n ingress-nginx \
  port-forward svc/ingress-nginx-controller 8090:80
```

Browser URLs (with `/etc/hosts` entries above):
- Store: http://store.urumi.local:8090/
- Dashboard: http://dashboard.urumi.local:8090/
- Gateway: http://gateway.urumi.local:8090/

`values-local.yaml` sets `wordpress.url` to `http://store.urumi.local:8090` so
home/siteurl and WooCommerce `COOKIEHASH` stay stable (cart cookies persist)
and redirects never drop to host Apache `:80`. Production values keep a normal
`https://…` URL without a local forward port.

External MCP clients use the **gateway** only (`/mcp`). Upstream MCP Services
remain ClusterIP and are not on Ingress.

## Ollama

Ollama is **not** scheduled in-cluster (host RAM is limited). The gateway uses
`host.docker.internal` → Kind docker bridge (`172.25.0.1` in values-local) to
reach host `localhost:11434`. Keep `ollama serve` running on the host.

## Production-like render

```bash
helm lint deploy/helm/woocommerce -f deploy/helm/woocommerce/values-prod.yaml
helm template woocommerce deploy/helm/woocommerce \
  -f deploy/helm/woocommerce/values.yaml \
  -f deploy/helm/woocommerce/values-prod.yaml >/tmp/urumi-prod.yaml
```

Replace registry image tags and TLS hosts in `values-prod.yaml` before applying
to k3s. Do not claim a live prod deploy until those images and secrets exist.
