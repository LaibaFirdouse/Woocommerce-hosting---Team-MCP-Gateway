"""Schema-agnostic grounding / compaction tests (mock fixtures only)."""

from __future__ import annotations

import json

from chat.grounding import (
    compact_tool_result,
    grounded_answer_from_tool_activity,
    render_structured,
)


def test_compact_tool_result_keeps_scalars_on_list_of_objects() -> None:
    bulky = {
        "result": [
            {
                "id": 7,
                "label": "Alpha",
                "code": "A-7",
                "meta": {"nested": "drop-me", "huge": "x" * 500},
                "tags": ["a", "b"],
            },
            {
                "id": 9,
                "label": "Beta",
                "code": "",
                "description": "plain",
            },
        ]
    }
    compact = compact_tool_result(bulky)
    assert compact["count"] == 2
    assert compact["result"][0]["id"] == 7
    assert compact["result"][0]["label"] == "Alpha"
    assert compact["result"][0]["code"] == "A-7"
    assert "meta" not in compact["result"][0]
    assert "tags" not in compact["result"][0]
    assert compact["result"][1]["label"] == "Beta"


def test_compact_works_for_arbitrary_collection_key() -> None:
    payload = {
        "widgets": [
            {"widget_id": 1, "title": "One", "blob": {"x": 1}},
            {"widget_id": 2, "title": "Two", "blob": {"x": 2}},
        ]
    }
    compact = compact_tool_result(payload)
    assert compact["count"] == 2
    assert compact["widgets"][0] == {"widget_id": 1, "title": "One"}


def test_grounded_answer_is_tool_name_and_json_driven() -> None:
    activity = [
        {
            "name": "lookup_widgets",
            "outcome": "success",
            "result_preview": json.dumps(
                {
                    "count": 2,
                    "widgets": [
                        {"widget_id": 1, "title": "One", "qty": 4},
                        {"widget_id": 2, "title": "Two", "qty": 0},
                    ],
                }
            ),
        },
        {
            "name": "check_bin",
            "outcome": "success",
            "result_preview": json.dumps(
                {
                    "bins": [
                        {
                            "bin_id": "B1",
                            "widget_id": 1,
                            "on_hand": 4,
                            "site": "North",
                        }
                    ]
                }
            ),
        },
    ]
    answer = grounded_answer_from_tool_activity(activity)
    assert answer is not None
    assert answer.startswith("lookup_widgets:")
    assert "check_bin:" in answer
    assert "widget_id=1" in answer
    assert "qty=4" in answer
    assert "on_hand=4" in answer
    assert "site=" in answer
    # Must not invent values absent from tool JSON.
    assert "P-049" not in answer
    assert "Test Prod1" not in answer
    assert "-5" not in answer


def test_grounded_answer_none_when_only_errors() -> None:
    activity = [
        {
            "name": "lookup_widgets",
            "outcome": "error",
            "error": "upstream unavailable",
        }
    ]
    assert grounded_answer_from_tool_activity(activity) is None


def test_render_structured_empty_containers() -> None:
    assert "(empty list)" in render_structured([])
    assert "(empty object)" in render_structured({})


def test_grounded_answer_preserves_exact_numeric_fields() -> None:
    """Regression: model must not be able to rewrite tool numerics in the reply."""
    activity = [
        {
            "name": "stock_probe",
            "outcome": "success",
            "result_preview": json.dumps(
                {
                    "rows": [
                        {
                            "ref": 99,
                            "label": "Fixture Item",
                            "on_hand": 12,
                            "location": "Zone A",
                        }
                    ]
                }
            ),
        }
    ]
    answer = grounded_answer_from_tool_activity(activity)
    assert answer is not None
    assert "on_hand=12" in answer
    assert "ref=99" in answer
    assert "on_hand=-5" not in answer
    assert "on_hand=1\n" not in answer and not answer.endswith("on_hand=1")
