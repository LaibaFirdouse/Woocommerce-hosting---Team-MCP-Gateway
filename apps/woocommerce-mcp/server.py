
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


load_dotenv()

STORE_URL = os.getenv("WOOCOMMERCE_URL", "").rstrip("/")
CONSUMER_KEY = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "")

if not all((STORE_URL, CONSUMER_KEY, CONSUMER_SECRET)):
    raise RuntimeError(
        "Missing WooCommerce configuration. Check WOOCOMMERCE_URL, "
        "WOOCOMMERCE_CONSUMER_KEY, and WOOCOMMERCE_CONSUMER_SECRET."
    )

HOST = os.getenv("WOOCOMMERCE_HOST", "0.0.0.0")
PORT = int(os.getenv("WOOCOMMERCE_PORT", "8000"))

mcp = FastMCP(
    "Urumi WooCommerce",
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


# Identifier-shaped values that are not useful as free-text ``search`` filters
# (WooCommerce search matches name/description, not numeric ids).
_IDENTIFIER_ONLY_SEARCH = re.compile(
    r"(?i)^(?:id|product[_\s-]?id|sku)\s*[:=#-]?\s*\d+$|^#\d+$"
)

_ALLOWED_ORDER_STATUSES = frozenset(
    {
        "pending",
        "processing",
        "on-hold",
        "completed",
        "cancelled",
        "refunded",
        "failed",
    }
)


def _as_search_text(value: Any) -> str:
    """
    Normalize optional text ``search`` arguments from tool callers.

    Local LLMs often put a bare identifier into a free-text search field
    (``22``, ``"22"``, ``"ID=22"``). Those are not name queries and yield empty
    result sets even when matching rows exist under other fields. Treat
    identifier-only values as "no text filter". Ordinary name queries are kept.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit():
        return ""
    if _IDENTIFIER_ONLY_SEARCH.match(text):
        return ""
    return text


def _as_optional_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_positive_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _as_positive_id(value: Any, *, field_name: str) -> int:
    """
    Coerce a required positive integer id from LLM-friendly shapes
    (int, digit string, or single-element list).
    """
    raw = value
    if isinstance(raw, list):
        if not raw:
            raise ValueError(f"{field_name} must be a positive integer.")
        raw = raw[0]
    try:
        number = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer.") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")
    return number


async def wc_request(
    method: str,
    resource: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Make an authenticated request to the WooCommerce REST API."""
    url = f"{STORE_URL}/wp-json/wc/v3/{resource.lstrip('/')}"

    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        response = await client.request(
            method,
            url,
            auth=(CONSUMER_KEY, CONSUMER_SECRET),
            params=params,
            json=payload,
        )

    if response.is_error:
        raise RuntimeError(
            f"WooCommerce API returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"WooCommerce returned invalid JSON. "
            f"HTTP status: {response.status_code}"
        ) from exc


@mcp.tool()
async def list_products(
    search: str | int | float | None = None,
    per_page: int | str | None = 10,
    page: int | str | None = 1,
) -> list[dict[str, Any]]:
    """
    List WooCommerce products.

    ``search`` filters by product name/text, not numeric product ID.
    For stock by product ID, use the inventory ``get_inventory`` tool.
    Local LLMs often pass an integer here; that is accepted and ignored
    (treated as no text filter) so listing still succeeds.
    """
    search_text = _as_search_text(search)
    per_page_n = _as_positive_int(per_page, default=10, minimum=1, maximum=100)
    page_n = _as_positive_int(page, default=1, minimum=1, maximum=100)

    params: dict[str, Any] = {"per_page": per_page_n, "page": page_n}
    if search_text:
        params["search"] = search_text

    return await wc_request("GET", "products", params=params)


@mcp.tool()
async def create_product(
    name: str | None = None,
    regular_price: str | int | float | None = None,
    description: str | None = None,
    sku: str | None = None,
) -> dict[str, Any]:
    """Create a simple WooCommerce product."""
    name_text = _as_optional_text(name)
    if not name_text:
        raise ValueError("Product name cannot be empty.")

    if regular_price is None or (isinstance(regular_price, str) and not regular_price.strip()):
        raise ValueError("regular_price must be a non-negative number.")

    try:
        price = float(regular_price)
        if price < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("regular_price must be a non-negative number.") from None

    payload: dict[str, Any] = {
        "name": name_text,
        "type": "simple",
        "regular_price": str(price),
        "description": _as_optional_text(description),
    }

    sku_text = _as_optional_text(sku)
    if sku_text:
        payload["sku"] = sku_text

    return await wc_request("POST", "products", payload=payload)


@mcp.tool()
async def list_orders(
    per_page: int | str | None = 10,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List WooCommerce orders, optionally filtering by order status."""
    per_page_n = _as_positive_int(per_page, default=10, minimum=1, maximum=100)
    params: dict[str, Any] = {"per_page": per_page_n}

    status_text = _as_optional_text(status).lower()
    if status_text:
        params["status"] = status_text

    return await wc_request("GET", "orders", params=params)


@mcp.tool()
async def get_order(
    order_id: int | str | list[int] | None = None,
) -> dict[str, Any]:
    """Retrieve one WooCommerce order by its numeric ID."""
    oid = _as_positive_id(order_id, field_name="order_id")
    return await wc_request("GET", f"orders/{oid}")


@mcp.tool()
async def update_order_status(
    order_id: int | str | list[int] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Update an order status. Valid examples: processing, completed, on-hold, cancelled."""
    oid = _as_positive_id(order_id, field_name="order_id")
    normalized_status = _as_optional_text(status).lower()

    if normalized_status not in _ALLOWED_ORDER_STATUSES:
        raise ValueError(
            "Unsupported status. Choose one of: "
            + ", ".join(sorted(_ALLOWED_ORDER_STATUSES))
        )

    return await wc_request(
        "PUT",
        f"orders/{oid}",
        payload={"status": normalized_status},
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
