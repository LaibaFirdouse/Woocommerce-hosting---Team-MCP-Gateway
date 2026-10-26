"""Unit tests for WooCommerce MCP tools (HTTP mocked — no live store writes)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from server import (
    _as_positive_id,
    create_product,
    get_order,
    list_orders,
    list_products,
    update_order_status,
)


def test_as_positive_id_accepts_llm_shapes() -> None:
    assert _as_positive_id(21, field_name="order_id") == 21
    assert _as_positive_id("21", field_name="order_id") == 21
    assert _as_positive_id([21], field_name="order_id") == 21
    with pytest.raises(ValueError, match="order_id"):
        _as_positive_id(0, field_name="order_id")
    with pytest.raises(ValueError, match="order_id"):
        _as_positive_id(None, field_name="order_id")
    with pytest.raises(ValueError, match="order_id"):
        _as_positive_id([], field_name="order_id")
    with pytest.raises(ValueError, match="order_id"):
        _as_positive_id("x", field_name="order_id")


def test_list_products_omitted_and_null_optionals() -> None:
    captured: dict[str, Any] = {}

    async def fake_wc(method, resource, *, params=None, payload=None):
        captured["params"] = params
        return [{"id": 1}]

    with patch("server.wc_request", side_effect=fake_wc):
        result = asyncio.run(list_products(search=None, per_page=None, page=None))

    assert result == [{"id": 1}]
    assert captured["params"] == {"per_page": 10, "page": 1}


def test_list_products_ignores_identifier_search() -> None:
    captured: dict[str, Any] = {}

    async def fake_wc(method, resource, *, params=None, payload=None):
        captured["params"] = params
        return []

    with patch("server.wc_request", side_effect=fake_wc):
        asyncio.run(list_products(search="22", per_page="5", page="2"))

    assert "search" not in captured["params"]
    assert captured["params"]["per_page"] == 5
    assert captured["params"]["page"] == 2


def test_list_orders_status_null_and_string_per_page() -> None:
    captured: dict[str, Any] = {}

    async def fake_wc(method, resource, *, params=None, payload=None):
        captured.update(method=method, resource=resource, params=params)
        return []

    with patch("server.wc_request", side_effect=fake_wc):
        asyncio.run(list_orders(per_page="3", status=None))

    assert captured["resource"] == "orders"
    assert captured["params"] == {"per_page": 3}


def test_get_order_string_id_and_unknown_propagates() -> None:
    async def not_found(method, resource, *, params=None, payload=None):
        raise RuntimeError("WooCommerce API returned HTTP 404: not found")

    with patch("server.wc_request", side_effect=not_found):
        with pytest.raises(RuntimeError, match="404"):
            asyncio.run(get_order(order_id="999999"))


def test_get_order_calls_correct_resource() -> None:
    captured: dict[str, Any] = {}

    async def fake_wc(method, resource, *, params=None, payload=None):
        captured.update(method=method, resource=resource)
        return {"id": 7, "status": "pending"}

    with patch("server.wc_request", side_effect=fake_wc):
        result = asyncio.run(get_order(order_id=[7]))

    assert result["id"] == 7
    assert captured["resource"] == "orders/7"


def test_create_product_validates_and_posts() -> None:
    captured: dict[str, Any] = {}

    async def fake_wc(method, resource, *, params=None, payload=None):
        captured.update(method=method, resource=resource, payload=payload)
        return {"id": 50, "name": payload["name"]}

    with patch("server.wc_request", side_effect=fake_wc):
        result = asyncio.run(
            create_product(
                name="  Fixture Item ",
                regular_price=12,
                description=None,
                sku=None,
            )
        )

    assert result["id"] == 50
    assert captured["method"] == "POST"
    assert captured["payload"]["name"] == "Fixture Item"
    assert captured["payload"]["regular_price"] == "12.0"
    assert "sku" not in captured["payload"]


def test_create_product_rejects_empty_name_and_bad_price() -> None:
    with pytest.raises(ValueError, match="name"):
        asyncio.run(create_product(name="  ", regular_price="1"))
    with pytest.raises(ValueError, match="regular_price"):
        asyncio.run(create_product(name="X", regular_price=None))
    with pytest.raises(ValueError, match="regular_price"):
        asyncio.run(create_product(name="X", regular_price="-1"))


def test_update_order_status_validates_and_puts() -> None:
    captured: dict[str, Any] = {}

    async def fake_wc(method, resource, *, params=None, payload=None):
        captured.update(method=method, resource=resource, payload=payload)
        return {"id": 7, "status": "completed"}

    with patch("server.wc_request", side_effect=fake_wc):
        result = asyncio.run(
            update_order_status(order_id="7", status=" Completed ")
        )

    assert result["status"] == "completed"
    assert captured["method"] == "PUT"
    assert captured["payload"] == {"status": "completed"}


def test_update_order_status_rejects_bad_status() -> None:
    with pytest.raises(ValueError, match="Unsupported status"):
        asyncio.run(update_order_status(order_id=1, status="shipped-yesterday"))
