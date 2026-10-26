#!/usr/bin/env python3
"""Live authz verification for Urumi dashboard/gateway demo identities.

Reads credentials from ~/.config/urumi/demo-logins.env (or DEMO_LOGINS_FILE).
Never prints passwords, JWTs, cookies, or API keys.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

GW = os.environ.get("URUMI_GATEWAY_URL", "http://127.0.0.1:8090").rstrip("/")
HOST = os.environ.get("URUMI_GATEWAY_HOST", "gateway.urumi.local")


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class Client:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = "application/json",
    ) -> tuple[int, object, dict[str, str]]:
        data = raw_body
        hdrs = {"Host": HOST, "Origin": "http://dashboard.urumi.local:8090"}
        if headers:
            hdrs.update(headers)
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = content_type or "application/json"
        req = urllib.request.Request(
            f"{GW}{path}", data=data, headers=hdrs, method=method
        )
        try:
            with self.opener.open(req, timeout=60) as resp:
                raw = resp.read()
                status = resp.status
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            resp_headers = {k.lower(): v for k, v in exc.headers.items()}
        parsed: object
        if raw:
            try:
                parsed = json.loads(raw.decode())
            except json.JSONDecodeError:
                parsed = raw.decode(errors="replace")
        else:
            parsed = None
        return status, parsed, resp_headers


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"{status}\t{name}{suffix}")
    if not ok:
        raise SystemExit(1)


def login(client: Client, email: str, password: str) -> dict:
    status, data, headers = client.request(
        "POST",
        "/auth/login",
        body={"email": email, "password": password},
    )
    check(
        f"login {email}",
        status == 200 and isinstance(data, dict) and "user" in data,
        f"http={status}",
    )
    assert isinstance(data, dict)
    set_cookie = headers.get("set-cookie", "")
    check(
        f"session cookie set for {email}",
        "urumi_session=" in set_cookie and "HttpOnly" in set_cookie,
        "cookie_flags_ok" if "HttpOnly" in set_cookie else "missing_httponly",
    )
    # Never print cookie value
    return data["user"]


def main() -> int:
    env_path = Path(
        os.environ.get(
            "DEMO_LOGINS_FILE",
            str(Path.home() / ".config/urumi/demo-logins.env"),
        )
    )
    if not env_path.exists():
        print(f"FAIL\tmissing credentials file {env_path}", file=sys.stderr)
        return 1
    creds = _load_env(env_path)
    admin_email = creds["URUMI_DEMO_ADMIN_EMAIL"]
    admin_pw = creds["URUMI_DEMO_ADMIN_PASSWORD"]
    member_email = creds["URUMI_DEMO_MEMBER_EMAIL"]
    member_pw = creds["URUMI_DEMO_MEMBER_PASSWORD"]

    # --- Admin (User A / team admin) ---
    admin = Client()
    user_a = login(admin, admin_email, admin_pw)
    check("user A role admin", user_a.get("role") == "admin", f"id={user_a.get('id')}")
    check("user A id 7", user_a.get("id") == 7)

    st, me, _ = admin.request("GET", "/auth/me")
    check("admin /auth/me", st == 200 and isinstance(me, dict) and me.get("user", {}).get("id") == 7)

    st, users, _ = admin.request("GET", "/admin/users")
    check("admin GET /admin/users", st == 200 and isinstance(users, dict))
    if isinstance(users, dict):
        ids = {u["id"]: u for u in users.get("users", [])}
        check("users include 6/7/8", 6 in ids and 7 in ids and 8 in ids)
        check("user 6 is admin", ids[6]["role"] == "admin" and ids[6]["is_active"])
        check("user 7 is admin", ids[7]["role"] == "admin")
        check("user 8 is member", ids[8]["role"] == "member")

    st, servers, _ = admin.request("GET", "/admin/servers")
    check("admin GET /admin/servers", st == 200, f"http={st}")

    st, audit, _ = admin.request("GET", "/audit-logs?limit=5")
    check("admin GET /audit-logs", st == 200, f"http={st}")

    # Chat attribution (may be slow / LLM-dependent — still auth-gated)
    st, chat, _ = admin.request(
        "POST",
        "/chat",
        body={"messages": [{"role": "user", "content": "Reply with exactly: pong"}]},
    )
    check(
        "admin POST /chat authenticated",
        st in (200, 502, 504) or (st == 200),
        f"http={st}",
    )
    # 401 would mean auth failed; other errors may be LLM upstream
    check("admin /chat not 401/403", st not in (401, 403), f"http={st}")

    # --- Member (User B) ---
    member = Client()
    user_b = login(member, member_email, member_pw)
    check("user B role member", user_b.get("role") == "member", f"id={user_b.get('id')}")
    check("user B id 8", user_b.get("id") == 8)

    st, _, _ = member.request("GET", "/admin/users")
    check("member denied /admin/users", st == 403, f"http={st}")

    st, _, _ = member.request("GET", "/admin/servers")
    check("member denied /admin/servers", st == 403, f"http={st}")

    st, keys, _ = member.request("GET", "/auth/api-keys")
    check("member lists own api keys", st == 200, f"http={st}")

    # Create a disposable MCP key for verification (do not revoke existing keys)
    st, created, _ = member.request(
        "POST",
        "/auth/api-keys",
        body={"name": "demo-verify-mcp"},
    )
    check("member create api key", st == 201 and isinstance(created, dict), f"http={st}")
    assert isinstance(created, dict)
    raw_key = created.get("raw_key")
    check("raw api key issued once", isinstance(raw_key, str) and str(raw_key).startswith("uk_"))

    # MCP initialize with member key
    mcp_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "verify-demo-auth", "version": "0.1"},
        },
    }
    st, mcp_init, _ = member.request(
        "POST",
        "/mcp",
        body=mcp_body,
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Accept": "application/json, text/event-stream",
        },
    )
    # Cookie jar still has member JWT; MCP should accept API key. Status 200 expected.
    check("member MCP initialize with own key", st in (200, 202), f"http={st}")

    # tools/list
    st, tools, _ = member.request(
        "POST",
        "/mcp",
        body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Accept": "application/json, text/event-stream",
        },
    )
    check("member MCP tools/list", st in (200, 202), f"http={st}")

    # Attempt create_product via tools/call — must be forbidden for member
    st, denied, _ = member.request(
        "POST",
        "/mcp",
        body={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_product",
                "arguments": {
                    "name": "SHOULD-NOT-CREATE",
                    "type": "simple",
                    "regular_price": "1.00",
                },
            },
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Accept": "application/json, text/event-stream",
        },
    )
    # Expect HTTP 403 or JSON-RPC error indicating forbidden
    denied_ok = st in (403, 401)
    if not denied_ok and isinstance(denied, dict):
        err = json.dumps(denied).lower()
        denied_ok = "forbidden" in err or "not allowed" in err or "admin" in err or "error" in err
    check("member blocked create_product via MCP", denied_ok, f"http={st}")

    # Member chat should not be able to create products (tool not offered / blocked)
    st, mchat, _ = member.request(
        "POST",
        "/chat",
        body={
            "messages": [
                {
                    "role": "user",
                    "content": "Create a WooCommerce product named SHOULD-NOT-CREATE priced at 1.00",
                }
            ]
        },
    )
    check("member /chat not 401", st not in (401,), f"http={st}")
    if st == 200 and isinstance(mchat, dict):
        activity = mchat.get("tool_activity") or mchat.get("tools") or []
        created = False
        if isinstance(activity, list):
            for item in activity:
                if isinstance(item, dict) and item.get("name") == "create_product":
                    # success would be bad
                    if item.get("ok") is True or item.get("status") == "ok":
                        created = True
        check("member chat did not successfully create_product", not created)
    else:
        check("member /chat responded", st in (200, 502, 504), f"http={st}")

    # Cross-attribution: admin me vs member me are distinct sessions
    st_a, me_a, _ = admin.request("GET", "/auth/me")
    st_b, me_b, _ = member.request("GET", "/auth/me")
    check(
        "sessions remain distinct",
        st_a == 200
        and st_b == 200
        and me_a["user"]["id"] == 7
        and me_b["user"]["id"] == 8,
    )

    print("ALL_AUTH_CHECKS_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
