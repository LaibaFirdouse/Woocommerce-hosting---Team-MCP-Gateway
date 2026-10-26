"""Unit tests for list_products argument coercion (no live WooCommerce calls)."""

from __future__ import annotations

from server import _as_positive_int, _as_search_text


def test_as_search_text_accepts_name_queries() -> None:
    assert _as_search_text("  shirt ") == "shirt"
    assert _as_search_text("Alpha") == "Alpha"
    assert _as_search_text("Fixture Widget") == "Fixture Widget"
    assert _as_search_text("") == ""
    assert _as_search_text(None) == ""
    # Optional nulls from tool callers must not crash.
    assert _as_search_text(None) == ""


def test_as_search_text_ignores_identifier_only_values() -> None:
    # Bare identifiers are not free-text name filters.
    assert _as_search_text(22) == ""
    assert _as_search_text(22.0) == ""
    assert _as_search_text("22") == ""
    assert _as_search_text(" 22 ") == ""
    assert _as_search_text("ID=22") == ""
    assert _as_search_text("id:22") == ""
    assert _as_search_text("product_id=22") == ""
    assert _as_search_text("#22") == ""


def test_as_search_text_keeps_non_identifier_filters() -> None:
    assert _as_search_text("220v") == "220v"
    assert _as_search_text("sku-22-extra") == "sku-22-extra"


def test_as_positive_int_clamps_to_bounds() -> None:
    assert _as_positive_int(5, default=10, minimum=1, maximum=100) == 5
    assert _as_positive_int("5", default=10, minimum=1, maximum=100) == 5
    assert _as_positive_int(0, default=10, minimum=1, maximum=100) == 1
    assert _as_positive_int(999, default=10, minimum=1, maximum=100) == 100
    assert _as_positive_int("x", default=10, minimum=1, maximum=100) == 10
    assert _as_positive_int(None, default=10, minimum=1, maximum=100) == 10
