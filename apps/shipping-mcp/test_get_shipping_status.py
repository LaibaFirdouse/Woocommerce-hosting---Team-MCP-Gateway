"""Unit tests for get_shipping_status (fixture rows injected — no hardcoded asserts on live SKUs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import server as shipping


@pytest.fixture()
def sample_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows = [
        {
            "order_id": 101,
            "tracking_number": "TRK-101",
            "carrier": "Carrier A",
            "shipping_status": "in_transit",
            "estimated_delivery": "2099-01-01",
        },
        {
            "order_id": 202,
            "tracking_number": "TRK-202",
            "carrier": "Carrier B",
            "shipping_status": "delivered",
            "estimated_delivery": "2099-02-02",
        },
    ]
    path = tmp_path / "shipments.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setenv("SHIPPING_DATA_PATH", str(path))
    shipping.SHIPMENT_ROWS[:] = shipping._load_shipment_rows()
    return rows


def test_normalize_order_id_variants() -> None:
    assert shipping._normalize_order_id(101) == 101
    assert shipping._normalize_order_id("101") == 101
    assert shipping._normalize_order_id([101]) == 101
    assert shipping._normalize_order_id([]) is None
    assert shipping._normalize_order_id(None) is None
    assert shipping._normalize_order_id("x") is None


def test_get_shipping_status_found(sample_rows: list[dict]) -> None:
    result = shipping.get_shipping_status(order_id="101")
    assert result["found"] is True
    assert result["order_id"] == 101
    assert result["tracking_number"] == "TRK-101"


def test_get_shipping_status_unknown_id(sample_rows: list[dict]) -> None:
    result = shipping.get_shipping_status(order_id=999)
    assert result["found"] is False
    assert result["order_id"] == 999


def test_get_shipping_status_invalid_id(sample_rows: list[dict]) -> None:
    result = shipping.get_shipping_status(order_id=0)
    assert result["found"] is False
    result2 = shipping.get_shipping_status(order_id=None)
    assert result2["found"] is False


def test_get_shipping_status_list_shape(sample_rows: list[dict]) -> None:
    result = shipping.get_shipping_status(order_id=[202])
    assert result["found"] is True
    assert result["shipping_status"] == "delivered"
