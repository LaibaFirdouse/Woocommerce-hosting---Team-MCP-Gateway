# Urumi Inventory MCP

Mock inventory MCP server (Streamable HTTP). No database or authentication yet.

## Install

```bash
cd apps/inventory-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

Defaults: **`0.0.0.0:8002`**, MCP endpoint **`http://127.0.0.1:8002/mcp`**.

Optional env vars: `INVENTORY_HOST`, `INVENTORY_PORT`.

## Tool: `get_inventory`

| Argument       | Type    | Description                          |
|----------------|---------|--------------------------------------|
| `product_id`   | int?    | Filter by exact WooCommerce product ID |
| `product_name` | string  | Filter by substring (case-insensitive) |

Returns `{"inventory": [...]}` where each item has `product_id`, `product_name`, `available_quantity`, and `warehouse`.

Sample product IDs **14** (Tshirt) and **22** (MCP Test Product) match WooCommerce test data.

## Quick test

With the server running:

```bash
.venv/bin/python -c "
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client('http://127.0.0.1:8002/mcp') as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            out = await session.call_tool('get_inventory', {'product_id': 14})
            print(out.content[0].text)

asyncio.run(main())
"
```
