# Urumi Shipping MCP

Mock shipping-status MCP server (Streamable HTTP). No database or authentication yet.

## Install

```bash
cd apps/shipping-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

Defaults: **`0.0.0.0:8003`**, MCP endpoint **`http://127.0.0.1:8003/mcp`**.

Optional env vars: `SHIPPING_HOST`, `SHIPPING_PORT`.

## Tool: `get_shipping_status`

| Argument   | Type | Description        |
|------------|------|--------------------|
| `order_id` | int  | WooCommerce order ID (required) |

Returns mock fields: `order_id`, `tracking_number`, `carrier`, `shipping_status`, `estimated_delivery`.

When a record exists, `found` is `true` and the shipment fields are included.  
When unknown, `found` is `false` with a short `message`. Sample order **21** matches WooCommerce test data.

## Quick test

With the server running:

```bash
.venv/bin/python -c "
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client('http://127.0.0.1:8003/mcp') as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            out = await session.call_tool('get_shipping_status', {'order_id': 21})
            print(out.content[0].text)

asyncio.run(main())
"
```
