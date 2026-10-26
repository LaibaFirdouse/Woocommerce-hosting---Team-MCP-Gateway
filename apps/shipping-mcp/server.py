import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

HOST = os.getenv("SHIPPING_HOST", "0.0.0.0")
PORT = int(os.getenv("SHIPPING_PORT", "8003"))

_DEFAULT_DATA_PATH = Path(__file__).resolve().parent / "data" / "shipments.json"


def _load_shipment_rows() -> list[dict[str, Any]]:
    """
    Load shipment rows from ``SHIPPING_DATA_PATH`` or the packaged sample file.

    Rows are data, not routing logic — override the path in deployment/tests.
    """
    configured = (os.getenv("SHIPPING_DATA_PATH") or "").strip()
    path = Path(configured) if configured else _DEFAULT_DATA_PATH
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


SHIPMENT_ROWS: list[dict[str, Any]] = _load_shipment_rows()

mcp = FastMCP(
    "Urumi Shipping",
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _normalize_order_id(order_id: Any) -> int | None:
    """Accept int or single-element list/str from small local LLMs."""
    if order_id is None or order_id == "":
        return None
    if isinstance(order_id, list):
        if not order_id:
            return None
        order_id = order_id[0]
    try:
        value = int(order_id)
    except (TypeError, ValueError):
        return None
    return value


@mcp.tool()
def get_shipping_status(
    order_id: int | str | list[int] | None = None,
) -> dict[str, Any]:
    """Return shipping status for a WooCommerce order ID."""
    normalized = _normalize_order_id(order_id)
    if normalized is None or normalized <= 0:
        return {
            "found": False,
            "order_id": order_id,
            "message": "order_id must be a positive integer.",
        }

    for shipment in SHIPMENT_ROWS:
        if shipment.get("order_id") == normalized:
            return {"found": True, **shipment}

    return {
        "found": False,
        "order_id": normalized,
        "message": "No shipping record found for this order ID.",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
