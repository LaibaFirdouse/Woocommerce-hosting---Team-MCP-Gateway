import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    async with streamablehttp_client("http://localhost:8000/mcp") as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            result = await session.call_tool(
                "update_order_status",
                {
                    "order_id": 21,
                    "status": "on-hold",
                },
            )

            if result.isError:
                print("Tool failed:")
                for item in result.content:
                    if hasattr(item, "text"):
                        print(item.text)
                return

            for item in result.content:
                if hasattr(item, "text"):
                    order = json.loads(item.text)
                    print("Order status updated successfully!")
                    print(f"Order ID: {order['id']}")
                    print(f"New status: {order['status']}")
                    print(f"Total: ₹{order['total']}")


if __name__ == "__main__":
    asyncio.run(main())