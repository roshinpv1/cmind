from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .orchestration_policies import MIN_REPO_TOOL_CALLS, should_force_continuation


@dataclass
class FinalStateDecision:
    is_final: bool
    reason: str = ""
    continue_prompt: str | None = None


def _extract_json_object(text: str) -> dict | None:
    s = (text or "").strip()
    if not s:
        return None

    fenced = re.search(r"```json\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    dec = json.JSONDecoder()
    start = s.find("{")
    if start >= 0:
        try:
            obj, _ = dec.raw_decode(s[start:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        t = value.strip()
        return bool(t and t not in {"{}", "[]", "null", "none", "n/a"})
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _required_fields_from_schema_model(schema_model: Any) -> list[str]:
    if not schema_model or not hasattr(schema_model, "model_fields"):
        return []
    required: list[str] = []
    for name, finfo in schema_model.model_fields.items():
        try:
            if finfo.is_required():
                required.append(name)
        except Exception:
            # Conservative fallback: if requirement introspection fails, skip.
            continue
    return required


def evaluate_final_state(
    *,
    response_text: str,
    repo_id: str | None,
    tool_calls_made: int,
    has_tool_history: bool,
    output_type: str = "",
    output_schema_model: Any = None,
) -> FinalStateDecision:
    text = (response_text or "").strip()
    if not text:
        return FinalStateDecision(
            is_final=False,
            reason="empty_response",
            continue_prompt=(
                "Your previous response was empty. Continue the task and either call tools "
                "or provide a complete final answer."
            ),
        )

    if should_force_continuation(text, tool_calls_made):
        return FinalStateDecision(
            is_final=False,
            reason="intermediate_or_giveup_language",
            continue_prompt=(
                "Do not return intermediate planning text. Continue executing the task now: "
                "either call the next tool(s) or provide the completed final answer only."
            ),
        )

    if repo_id and has_tool_history and tool_calls_made < MIN_REPO_TOOL_CALLS:
        # Enforce minimal evidence collection for repo-scoped analysis before finalization.
        return FinalStateDecision(
            is_final=False,
            reason="insufficient_tool_coverage",
            continue_prompt=(
                "Insufficient repository evidence to finalize. Continue tool-driven analysis "
                "with additional targeted calls (read_file/search_code/call graph tools) "
                "before concluding."
            ),
        )

    if (output_type or "").strip().lower() == "tool_call":
        return FinalStateDecision(
            is_final=False,
            reason="expected_tool_call_output",
            continue_prompt=(
                "This playbook requires a terminal tool call output. Do not finish with prose. "
                "Call the required tool with the structured result."
            ),
        )

    if (output_type or "").strip().lower() == "json_response":
        obj = _extract_json_object(text)
        if not isinstance(obj, dict):
            return FinalStateDecision(
                is_final=False,
                reason="json_required_but_not_parseable",
                continue_prompt=(
                    "Final output must be a valid JSON object. Return only JSON matching the "
                    "required schema."
                ),
            )
        required = _required_fields_from_schema_model(output_schema_model)
        missing = [k for k in required if not _is_non_empty(obj.get(k))]
        if missing:
            return FinalStateDecision(
                is_final=False,
                reason="json_missing_required_fields",
                continue_prompt=(
                    "Final JSON is missing required non-empty fields: "
                    + ", ".join(missing)
                    + ". Fill them using observed evidence only."
                ),
            )

    return FinalStateDecision(is_final=True, reason="accepted")
