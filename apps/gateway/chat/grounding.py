"""
Schema-agnostic helpers for compacting tool payloads and grounding answers.

No tool-name or business-entity special cases: any successful tool preview is
rendered from its JSON shape so small local models cannot invent conflicting
facts.
"""

from __future__ import annotations

import json
from typing import Any

# Scalar-ish values safe to keep when shrinking bulky upstream objects.
_SCALAR_TYPES = (str, int, float, bool, type(None))

MAX_LIST_ITEMS = 25
MAX_OBJECT_KEYS = 24
# Drop oversized string scalars from list-item projections (descriptions, HTML).
MAX_SCALAR_STRING_CHARS = 160


def parse_preview_json(preview: str) -> Any | None:
    text = (preview or "").strip()
    if not text:
        return None
    if text.endswith("…"):
        text = text[:-1].rstrip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def compact_tool_result(value: Any, *, depth: int = 0) -> Any:
    """
    Shrink bulky JSON for the model / activity preview.

    Strategy (structure-driven, not domain-driven):
    - Lists of objects → keep only scalar fields on each item (drops nested
      blobs that blow the preview budget mid-key).
    - Nested containers are walked shallowly; depth is capped.
    """
    if depth > 4:
        return value

    if isinstance(value, list):
        items = value[:MAX_LIST_ITEMS]
        if items and all(isinstance(item, dict) for item in items):
            return [_scalar_projection(item) for item in items]
        return [compact_tool_result(item, depth=depth + 1) for item in items]

    if isinstance(value, dict):
        # Common envelope: {"result": [...]} / {"items": [...]} / etc.
        list_key = _primary_list_key(value)
        if list_key is not None:
            items = value[list_key]
            if isinstance(items, list):
                out = {
                    k: v
                    for k, v in value.items()
                    if k != list_key and isinstance(v, _SCALAR_TYPES)
                }
                out["count"] = len(items)
                out[list_key] = compact_tool_result(items, depth=depth + 1)
                return out
        return {
            key: compact_tool_result(val, depth=depth + 1)
            for key, val in list(value.items())[:MAX_OBJECT_KEYS]
        }

    return value


def _primary_list_key(obj: dict[str, Any]) -> str | None:
    """Prefer the longest list-of-dicts value as the primary collection."""
    best_key: str | None = None
    best_len = -1
    for key, val in obj.items():
        if isinstance(val, list) and val and all(isinstance(i, dict) for i in val):
            if len(val) > best_len:
                best_key = key
                best_len = len(val)
    return best_key


def _scalar_projection(item: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key, val in item.items():
        if not isinstance(val, _SCALAR_TYPES):
            continue
        if isinstance(val, str) and len(val) > MAX_SCALAR_STRING_CHARS:
            continue
        projected[key] = val
        if len(projected) >= MAX_OBJECT_KEYS:
            break
    if projected:
        return projected
    # Fallback: keep a few keys so the item is not empty in previews.
    fallback: dict[str, Any] = {}
    for key, val in list(item.items())[:8]:
        if isinstance(val, str) and len(val) > MAX_SCALAR_STRING_CHARS:
            fallback[key] = val[:MAX_SCALAR_STRING_CHARS] + "…"
        else:
            fallback[key] = val
    return fallback


def render_structured(value: Any, *, indent: int = 0) -> str:
    """Render arbitrary JSON as indented key=value / list lines."""
    prefix = "  " * indent
    if isinstance(value, dict):
        if not value:
            return f"{prefix}(empty object)"
        lines: list[str] = []
        for key, val in value.items():
            if isinstance(val, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(render_structured(val, indent=indent + 1))
            else:
                lines.append(f"{prefix}{key}={_format_scalar(val)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{prefix}(empty list)"
        lines = []
        for index, item in enumerate(value):
            if isinstance(item, dict):
                lines.append(f"{prefix}- [{index}]")
                lines.append(render_structured(item, indent=indent + 1))
            else:
                lines.append(f"{prefix}- {_format_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_format_scalar(value)}"


def _format_scalar(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    return json.dumps(value, default=str)


def grounded_answer_from_tool_activity(
    tool_activity: list[dict[str, Any]],
) -> str | None:
    """
    Build a factual answer from successful tool previews only.

    Sections are labeled by the tool name that produced them (from the live
    allowlisted invocation), not by hardcoded business entity types.
    """
    if not tool_activity:
        return None

    sections: list[str] = []
    any_success = False

    for row in tool_activity:
        name = str(row.get("name") or "tool").strip() or "tool"
        if row.get("outcome") != "success":
            err = str(row.get("error") or "failed").strip()
            sections.append(f"{name} (error): {err}")
            continue

        any_success = True
        preview = str(row.get("result_preview") or "").strip()
        payload = parse_preview_json(preview)
        if payload is None:
            sections.append(f"{name}: {preview}" if preview else f"{name}: success")
            continue

        body = render_structured(payload)
        sections.append(f"{name}:\n{body}")

    if not any_success or not sections:
        return None
    return "\n\n".join(sections)
