# Urumi AI Dashboard

React (Vite) frontend for the Urumi gateway. Session auth uses the gateway’s
HttpOnly `urumi_session` cookie — JWTs are **not** stored in
`localStorage` / `sessionStorage`.

## Setup

```bash
cd apps/dashboard
cp .env.example .env
npm install
npm run dev
```

Open **http://127.0.0.1:5173** (not `localhost` — keep the hostname aligned with
`VITE_GATEWAY_API_URL`, which defaults to `http://127.0.0.1:8001`).

## Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_GATEWAY_API_URL` | `http://127.0.0.1:8001` | Gateway HTTP base URL (no trailing slash). Must use the same hostname as the Vite URL you open. |

## Gateway requirements for local cookies

On the gateway:

```bash
export AUTH_COOKIE_SECURE=false
export GATEWAY_CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
```

Then run `python server.py` from `apps/gateway`. CORS must list the Vite origin
and allow credentials so browser `fetch(..., { credentials: "include" })` works.

**Hostname rule:** open the dashboard at `http://127.0.0.1:5173` and point
`VITE_GATEWAY_API_URL` at `http://127.0.0.1:8001`. Mixing `localhost` and
`127.0.0.1` makes the session cookie cross-site under `SameSite=Lax`, so
`GET /auth/me` after refresh sends no `Cookie` and returns 401.

## Scripts

- `npm run dev` — Vite dev server
- `npm run build` — production build
- `npm test` — Vitest unit/UI tests
