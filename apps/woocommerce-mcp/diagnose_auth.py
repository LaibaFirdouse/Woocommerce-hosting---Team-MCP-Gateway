"""Minimal WooCommerce REST API auth diagnostic (no credential output)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

store_url = os.getenv("WOOCOMMERCE_URL", "").strip().rstrip("/")
consumer_key = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "").strip()
consumer_secret = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "").strip()

TIMEOUT = 20.0
PRODUCTS_PATH = "/wp-json/wc/v3/products"


def credential_checks() -> list[str]:
    issues: list[str] = []
    if not store_url:
        issues.append("WOOCOMMERCE_URL is missing")
    if not consumer_key:
        issues.append("WOOCOMMERCE_CONSUMER_KEY is missing")
    elif not consumer_key.startswith("ck_"):
        issues.append(
            "WOOCOMMERCE_CONSUMER_KEY should start with ck_ (64-char hex values are "
            "hashed DB storage, not the key WooCommerce shows once at creation)"
        )
    if not consumer_secret:
        issues.append("WOOCOMMERCE_CONSUMER_SECRET is missing")
    elif not consumer_secret.startswith("cs_"):
        issues.append("WOOCOMMERCE_CONSUMER_SECRET should start with cs_")
    return issues


def parse_error(response: httpx.Response) -> tuple[str, str]:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("code", "unknown")), str(data.get("message", ""))
    except json.JSONDecodeError:
        pass
    return "non_json_response", response.text[:200]


def try_request(auth_mode: str) -> httpx.Response:
    url = f"{store_url}{PRODUCTS_PATH}"
    params = {"per_page": 1}
    client_kwargs: dict = {"timeout": TIMEOUT}

    if auth_mode == "basic":
        client_kwargs["auth"] = (consumer_key, consumer_secret)
    elif auth_mode == "query":
        params["consumer_key"] = consumer_key
        params["consumer_secret"] = consumer_secret
    else:
        raise ValueError(auth_mode)

    return httpx.get(url, params=params, **client_kwargs)


def main() -> int:
    print(f"Env loaded from: {SCRIPT_DIR / '.env'}")
    print(f"Store URL configured: {bool(store_url)}")
    print(f"Consumer key present: {bool(consumer_key)} (prefix ck_: {consumer_key.startswith('ck_')})")
    print(f"Consumer secret present: {bool(consumer_secret)} (prefix cs_: {consumer_secret.startswith('cs_')})")

    issues = credential_checks()
    if issues:
        print("\nConfiguration issues:")
        for item in issues:
            print(f"  - {item}")
        return 1

    for auth_mode in ("basic", "query"):
        label = "Basic Authentication" if auth_mode == "basic" else "query parameters"
        print(f"\n--- Auth mode: {label} ---")
        try:
            response = try_request(auth_mode)
        except httpx.RequestError as exc:
            print(f"HTTP status: (connection failed)")
            print(f"Error: {type(exc).__name__}: {exc}")
            continue

        print(f"HTTP status: {response.status_code}")
        if response.status_code == 200:
            try:
                data = response.json()
                count = len(data) if isinstance(data, list) else 0
                print(f"SUCCESS: JSON array with {count} product(s)")
            except json.JSONDecodeError:
                print("SUCCESS: HTTP 200 but body was not JSON")
        else:
            code, message = parse_error(response)
            print(f"WooCommerce code: {code}")
            print(f"Message: {message}")

    if store_url.lower().startswith("http://"):
        print(
            "\nNote: On plain HTTP, WooCommerce 11.x expects OAuth 1.0a unless the cluster "
            "mu-plugin urumi-wc-http-rest-auth is enabled (Helm: wordpress.allowHttpRestAuth)."
        )
    print(
        "\nNote: Basic auth avoids putting secrets in URLs/logs; query auth is supported "
        "when API key authentication is allowed for your transport."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
