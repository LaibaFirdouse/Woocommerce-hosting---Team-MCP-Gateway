import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

store_url = os.getenv("WOOCOMMERCE_URL", "").strip().rstrip("/")
consumer_key = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "").strip()
consumer_secret = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "").strip()

print(f"Connecting to: {store_url}")

async def main():
    if not store_url or not consumer_key or not consumer_secret:
        print("ERROR: Check that all three settings are filled in your .env file.")
        return

    # 1. Direct path instead of using 'rest_route' query params
    url = f"{store_url}/wp-json/wc/v3/products"

    params = {"per_page": 5}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                params=params,
                auth=(consumer_key, consumer_secret),
            )

        if response.status_code == 200:
            products = response.json()
            print("SUCCESS: Connected to WooCommerce!")
            print(f"Products returned: {len(products)}")

            for product in products:
                print(
                    f"- {product.get('name')} | "
                    f"Price: {product.get('price')} | "
                    f"ID: {product.get('id')}"
                )
        elif response.status_code in (401,403):
            print("AUTHENTICATION ERROR: Check your REST API key and permissions.")
            print(f"HTTP status: {response.status_code}")
            print("Response:", response.text[:300])
        else:
            print(f"API ERROR: HTTP {response.status_code}")
            print("Response:", response.text[:300])

    except httpx.ConnectError as ce:
        print(f"CONNECTION ERROR: Is the WordPress port-forward running? Details: {ce}")
    except httpx.TimeoutException:
        print("TIMEOUT: WooCommerce did not respond within 15 seconds.")
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}")


if __name__ == "__main__":
    asyncio.run(main())

