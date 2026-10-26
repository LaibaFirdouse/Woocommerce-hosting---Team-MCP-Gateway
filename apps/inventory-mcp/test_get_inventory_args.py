"""Unit tests for get_inventory argument normalization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import server as inventory


@pytest.fixture()
def sample_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows = [
        {
            "product_id": 7,
            "product_name": "Fixture Alpha",
            "available_quantity": 12,
            "warehouse": "Zone North",
        },
        {
            "product_id": 9,
            "product_name": "Fixture Beta",
            "available_quantity": 0,
            "warehouse": "Zone South",
        },
    ]
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setenv("INVENTORY_DATA_PATH", str(path))
    inventory.INVENTORY_ROWS[:] = inventory._load_inventory_rows()
    return rows


def test_normalize_product_id_variants() -> None:
    assert inventory._normalize_product_id(22) == 22
    assert inventory._normalize_product_id("22") == 22
    assert inventory._normalize_product_id([22]) == 22
    assert inventory._normalize_product_id([]) is None
    assert inventory._normalize_product_id(None) is None
    assert inventory._normalize_product_id("x") is None


def test_get_inventory_accepts_list_product_id(sample_rows: list[dict]) -> None:
    result = inventory.get_inventory(product_id=[7])
    assert len(result["inventory"]) == 1
    assert result["inventory"][0]["product_id"] == 7
    assert result["inventory"][0]["available_quantity"] == 12


def test_get_inventory_null_filters_returns_all(sample_rows: list[dict]) -> None:
    result = inventory.get_inventory(product_id=None, product_name=None)
    assert len(result["inventory"]) == 2


def test_get_inventory_unknown_id_empty(sample_rows: list[dict]) -> None:
    result = inventory.get_inventory(product_id=404)
    assert result["inventory"] == []


def test_get_inventory_name_filter(sample_rows: list[dict]) -> None:
    result = inventory.get_inventory(product_name="alpha")
    assert len(result["inventory"]) == 1
    assert result["inventory"][0]["product_id"] == 7
