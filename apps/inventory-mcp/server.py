import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

HOST = os.getenv("INVENTORY_HOST", "0.0.0.0")
PORT = int(os.getenv("INVENTORY_PORT", "8002"))

_DEFAULT_DATA_PATH = Path(__file__).resolve().parent / "data" / "inventory.json"


def _load_inventory_rows() -> list[dict[str, Any]]:
    """
    Load inventory rows from ``INVENTORY_DATA_PATH`` or the packaged sample file.

    Rows are data, not routing logic — override the path in deployment/tests.
    """
    configured = (os.getenv("INVENTORY_DATA_PATH") or "").strip()
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


# Mutable for tests; refreshed from disk at import / explicit reload.
INVENTORY_ROWS: list[dict[str, Any]] = _load_inventory_rows()

mcp = FastMCP(
    "Urumi Inventory",
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _normalize_product_id(product_id: Any) -> int | None:
    """Accept int or single-element list/str from small local LLMs."""
    if product_id is None or product_id == "":
        return None
    if isinstance(product_id, list):
        if not product_id:
            return None
        product_id = product_id[0]
    try:
        return int(product_id)
    except (TypeError, ValueError):
        return None


def _as_optional_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


@mcp.tool()
def get_inventory(
    product_id: int | str | list[int] | None = None,
    product_name: str | None = None,
) -> dict[str, Any]:
    """Return inventory levels, optionally filtered by product ID or name."""
    results = list(INVENTORY_ROWS)

    normalized_id = _normalize_product_id(product_id)
    if normalized_id is not None:
        results = [row for row in results if row.get("product_id") == normalized_id]

    name_query = _as_optional_text(product_name)
    if name_query:
        needle = name_query.casefold()
        results = [
            row
            for row in results
            if needle in str(row.get("product_name") or "").casefold()
        ]

    return {"inventory": results}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
