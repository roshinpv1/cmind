"""
Extract a single top-level JSON object from LLM assistant text.

Used by final-state checks, catalog persistence, and similar flows. Fenced
`` ```json `` blocks are sliced by fence boundaries (not brace-counting regex)
so strings may contain ``}`` without corrupting extraction.
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_top_level_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None

    # 1) Fenced ```json ... ``` — slice strictly between opening and closing fences
    for m in re.finditer(r"```json\s*", s, flags=re.IGNORECASE):
        start = m.end()
        end = s.find("```", start)
        if end <= start:
            continue
        chunk = s[start:end].strip()
        if not chunk.startswith("{"):
            continue
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                unwrapped = _unwrap_catalog_tool_envelope(obj)
                return unwrapped
        except json.JSONDecodeError:
            continue

    # 2) Whole string is JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return _unwrap_catalog_tool_envelope(obj)
    except json.JSONDecodeError:
        pass

    # 3) First balanced object via JSONDecoder (handles leading prose before "{")
    dec = json.JSONDecoder()
    idx = s.find("{")
    while idx >= 0:
        try:
            obj, _ = dec.raw_decode(s[idx:])
            if isinstance(obj, dict):
                return _unwrap_catalog_tool_envelope(obj)
        except json.JSONDecodeError:
            pass
        idx = s.find("{", idx + 1)

    return None


def _unwrap_catalog_tool_envelope(obj: dict[str, Any]) -> dict[str, Any]:
    """Support legacy ``{tool: save_catalog_entry, params: {...}}`` shape."""
    if obj.get("tool") == "save_catalog_entry" and isinstance(obj.get("params"), dict):
        return dict(obj["params"])
    return obj
