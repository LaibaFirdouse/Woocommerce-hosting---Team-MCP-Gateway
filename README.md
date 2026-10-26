# Urumi AI — WooCommerce Hosting with Team MCP Gateway

Authenticated MCP gateway in front of WooCommerce, inventory, and shipping tools; React dashboard with JWT sessions, API keys, audit logs, admin team/server management, and an LLM chat agent.

**Repository:** [LaibaFirdouse/Woocommerce-hosting---Team-MCP-Gateway](https://github.com/LaibaFirdouse/Woocommerce-hosting---Team-MCP-Gateway)

## Deliverables map

| Deliverable | Location |
|-------------|----------|
| Dashboard (React) | `apps/dashboard/` |
| Gateway / backend / chat agent | `apps/gateway/` |
| WooCommerce MCP | `apps/woocommerce-mcp/` |
| Inventory / shipping MCPs | `apps/inventory-mcp/`, `apps/shipping-mcp/` |
| Helm chart + local/prod values | `deploy/helm/woocommerce/` |
| System design & tradeoffs | [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) |
| Threat model | [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) |
| Bonus / stand-out (honest rubric map) | [docs/BONUS_AND_STANDOUT.md](docs/BONUS_AND_STANDOUT.md) — multi-store **not** done; tool permissions **yes** |

Do not commit secrets. Use `.env.example` files and Kubernetes Secrets (names only in docs).

---

## Local setup (Kind + Helm)

### Prerequisites

- Docker, Kind, kubectl, Helm 3
- Node 22+ (dashboard tests/dev only)
- Ollama on the host (`ollama serve`, model `qwen2.5:1.5b`)
- `/etc/hosts` entries:

```text
127.0.0.1 store.urumi.local dashboard.urumi.local gateway.urumi.local
```

### 1. Create cluster and Ingress

```bash
kind create cluster --name urumi-local   # if needed

kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/kind/deploy.yaml
kubectl label node urumi-local-control-plane ingress-ready=true --overwrite
```

### 2. Create Secrets (once)

```bash
kubectl create namespace urumi

# Fill values from your local env — do not commit them.
kubectl -n urumi create secret generic urumi-gateway-secrets \
  --from-literal=auth-jwt-secret='<≥32 random chars>' \
  --from-literal=postgres-password='<postgres password>' \
  --from-literal=database-url='postgresql+psycopg://urumi:<postgres password>@postgres:5432/urumi_gateway' \
  --from-literal=llm-api-key=ollama

kubectl -n urumi create secret generic urumi-woocommerce-mcp-secrets \
  --from-literal=consumer-key='<wc consumer key>' \
  --from-literal=consumer-secret='<wc consumer secret>'

# Store DB secret (MariaDB) — adapt if your chart expects urumi-store-secrets already.
kubectl -n urumi create secret generic urumi-store-secrets \
  --from-literal=db-password='<mariadb password>' \
  --from-literal=wordpress-password='<wp admin password>'
```

Exact secret key names: see `deploy/helm/woocommerce/README.md` and `values.yaml`.

### 3. Build and load images

```bash
docker build -t urumi/woocommerce-mcp:local apps/woocommerce-mcp
docker build -t urumi/inventory-mcp:local apps/inventory-mcp
docker build -t urumi/shipping-mcp:local apps/shipping-mcp
docker build -t urumi/gateway:local apps/gateway
# Same-origin API: empty VITE_GATEWAY_API_URL (dashboard nginx proxies to gateway)
docker build -t urumi/dashboard:local apps/dashboard

mkdir -p .image-tars
for n in woocommerce-mcp inventory-mcp shipping-mcp gateway dashboard; do
  docker save urumi/$n:local -o .image-tars/$n.tar
  kind load image-archive .image-tars/$n.tar --name urumi-local
done
```

### 4. Helm install

```bash
helm upgrade --install woocommerce deploy/helm/woocommerce \
  --kube-context kind-urumi-local \
  -n urumi --create-namespace \
  -f deploy/helm/woocommerce/values.yaml \
  -f deploy/helm/woocommerce/values-local.yaml
```

### 5. Access via Ingress port-forward

If host `:80` is Apache (not Kind), forward Ingress:

```bash
kubectl --context kind-urumi-local -n ingress-nginx \
  port-forward svc/ingress-nginx-controller 8090:80
```

| Surface | URL |
|---------|-----|
| Store | http://store.urumi.local:8090/ |
| Dashboard | http://dashboard.urumi.local:8090/ |
| Gateway MCP | http://gateway.urumi.local:8090/mcp |

Keep `ollama serve` running on the host. More detail: `deploy/helm/woocommerce/README.md`.

### Optional: process-based local stack (without full Helm MCPs)

You can still run MCPs + gateway + Vite dashboard as processes against a Kind WordPress — see older notes in `apps/*/README.md`. The **supported assignment path** is the Helm Kind stack above.

---

## VPS / production-like setup (k3s)

Same chart; use production values and a real registry.

1. Install [k3s](https://docs.k3s.io/) (or equivalent) with an Ingress controller (nginx Traefik is fine if you set `ingress.className`).
2. Point DNS `A`/`AAAA` records for `store`, `dashboard`, and `gateway` hosts at the VPS.
3. Create the same Secret objects in namespace `urumi` (strong passwords, real WC keys, JWT secret ≥ 32 chars).
4. Build/push images to your registry; set tags in `values-prod.yaml`.
5. Install cert-manager (or terminate TLS at a reverse proxy) so Ingress TLS secrets exist as referenced in `values-prod.yaml`.
6. Deploy:

```bash
helm upgrade --install woocommerce deploy/helm/woocommerce \
  -n urumi --create-namespace \
  -f deploy/helm/woocommerce/values.yaml \
  -f deploy/helm/woocommerce/values-prod.yaml
```

Production differences (TLS, `AUTH_COOKIE_SECURE`, StorageClass sizing, LLM URL): [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md).

---

## How to place an order (storefront)

1. Open http://store.urumi.local:8090/ (or your prod store URL).
2. Open a product → **Add to cart**.
3. Open **Cart** → proceed to checkout.
4. Use **Cash on delivery** (or whatever gateways are enabled) and place the order.
5. Confirm the order appears in WooCommerce admin / thank-you page.

Local cart cookies stay valid when `wordpress.url` includes `:8090` and the MU plugin `urumi-public-url.php` is active (Helm local values). Avoid browsing the store on host Apache `:80`.

---

## Connect an external MCP client

1. Sign in to the **dashboard** as a member or admin.
2. Open **API keys** → create a key → **copy the raw secret once**.
3. Point the MCP client at the **gateway only** (not upstream ports):

   - Local: `http://gateway.urumi.local:8090/mcp`
   - Header: `Authorization: Bearer uk_<prefix>.<secret>`

4. Discover tools / call allowlisted tools. Upstream MCP Services are ClusterIP-only.
5. Revoke the key in the dashboard (or Admin → Team) → next call must return **401**.

Admins can also issue keys for a member under **Admin → Team & API keys**.

---

## Use the chat agent

1. Open http://dashboard.urumi.local:8090/login and sign in.
2. Go to **Chat**.
3. Ask natural-language questions, e.g. list products, check inventory, shipping status.
4. Admins can ask to create a product; members cannot (tool blocked server-side).
5. Inspect **tool activity** on the reply and **Audit** for attribution.

Chat uses the gateway LLM settings (`LLM_BASE_URL` / Ollama locally). Sessions use the HttpOnly `urumi_session` cookie via the dashboard’s same-origin proxy.

### Two-account demo

| Role | Example | Can |
|------|---------|-----|
| User A (admin) | `user.a.admin@urumi.local` | Chat + create product, Admin servers, team keys |
| User B (member) | `user.b.member@urumi.local` | Chat (read tools), own API keys; no admin APIs |

Use separate browser profiles. Credentials for a local demo may live in `~/.config/urumi/demo-logins.env` (not in git); re-seed with `scripts/seed_demo_logins.py` inside the gateway pod if needed.

---

## Tests

```bash
cd apps/gateway && .venv/bin/python -m pytest -q
cd apps/woocommerce-mcp && ../gateway/.venv/bin/python -m pytest -q
cd apps/inventory-mcp && ../gateway/.venv/bin/python -m pytest -q
cd apps/shipping-mcp && ../gateway/.venv/bin/python -m pytest -q
cd apps/dashboard && npm ci && npm test -- --run
helm lint deploy/helm/woocommerce -f deploy/helm/woocommerce/values.yaml -f deploy/helm/woocommerce/values-local.yaml
```

---

## Design notes (summary)

- **Architecture, routing, failure handling, production deltas:** [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)
- **Threat model** (shared WC credentials, no client token passthrough, untrusted tool descriptions, cross-member abuse): [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)
- **Bonus / stand-out (rubric checklist):** [docs/BONUS_AND_STANDOUT.md](docs/BONUS_AND_STANDOUT.md)
