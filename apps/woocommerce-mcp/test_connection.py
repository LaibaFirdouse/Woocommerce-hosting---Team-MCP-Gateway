import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

store_url = os.getenv("WOOCOMMERCE_URL", "").strip().rstrip("/")
consumer_key = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "").strip()
consumer_secret = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "").strip()

async def main():
    if not store_url or not consumer_key or not consumer_secret:
        print("ERROR: Check that all three settings are filled in your .env file.")
        return

    url = f"{store_url}/wp-json/wc/v3/products"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                auth=(consumer_key, consumer_secret),
                params={"per_page": 5},
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
        elif response.status_code == 401 or response.status_code == 403:
            print("AUTHENTICATION ERROR: Check your REST API key and permissions.")
            print(f"HTTP status: {response.status_code}")
        else:
            print(f"API ERROR: HTTP {response.status_code}")
            print("Response:", response.text[:300])

    except httpx.ConnectError:
        print("CONNECTION ERROR: Is the WordPress port-forward running on port 8080?")
    except httpx.TimeoutException:
        print("TIMEOUT: WooCommerce did not respond within 15 seconds.")
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}")


asyncio.run(main())
