from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .json_answer_extract import extract_top_level_json_object
from .orchestration_policies import MIN_REPO_TOOL_CALLS, should_force_continuation


@dataclass
class FinalStateDecision:
    is_final: bool
    reason: str = ""
    continue_prompt: str | None = None


_MIN_UNIQUE_READ_FILES = max(
    1, int(os.getenv("CODEMIND_MIN_UNIQUE_READ_FILES", "2"))
)
_MIN_STRUCTURAL_CALLS = max(
    1, int(os.getenv("CODEMIND_MIN_STRUCTURAL_CALLS", "1"))
)
_MIN_LEXICAL_CALLS = max(
    1, int(os.getenv("CODEMIND_MIN_LEXICAL_CALLS", "1"))
)
_MIN_EVIDENCE_MESSAGES = max(
    1, int(os.getenv("CODEMIND_MIN_EVIDENCE_MESSAGES", "2"))
)
_MIN_CRITICAL_COVERAGE_RATIO = max(
    0.0, min(1.0, float(os.getenv("CODEMIND_MIN_CRITICAL_COVERAGE_RATIO", "0.4")))
)


def _extract_json_object(text: str) -> dict | None:
    return extract_top_level_json_object(text)


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
    evidence_stats: dict[str, Any] | None = None,
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

    if repo_id and has_tool_history:
        stats = evidence_stats or {}
        unique_read_files = int(stats.get("unique_read_files", 0) or 0)
        structural_calls = int(stats.get("structural_calls", 0) or 0)
        lexical_calls = int(stats.get("lexical_calls", 0) or 0)
        evidence_messages = int(stats.get("evidence_messages", 0) or 0)
        critical_total = int(stats.get("critical_candidates_total", 0) or 0)
        critical_read = int(stats.get("critical_candidates_read", 0) or 0)
        critical_ratio = float(stats.get("critical_coverage_ratio", 0.0) or 0.0)
        missing: list[str] = []
        if unique_read_files < _MIN_UNIQUE_READ_FILES:
            missing.append(
                f"read at least {_MIN_UNIQUE_READ_FILES} unique repository files"
            )
        if structural_calls < _MIN_STRUCTURAL_CALLS:
            missing.append(
                "perform structural tracing (e.g., get_map/get_callers/get_callees/get_dependencies/search_symbol)"
            )
        if lexical_calls < _MIN_LEXICAL_CALLS:
            missing.append(
                "perform lexical search (e.g., search_code/grep_search/list_files)"
            )
        if evidence_messages < _MIN_EVIDENCE_MESSAGES:
            missing.append(
                f"collect at least {_MIN_EVIDENCE_MESSAGES} evidence-bearing tool outputs"
            )
        if critical_total > 0 and critical_ratio < _MIN_CRITICAL_COVERAGE_RATIO:
            min_read = max(1, int(critical_total * _MIN_CRITICAL_COVERAGE_RATIO + 0.999))
            missing.append(
                "cover critical ranked files before finalizing "
                f"(read {critical_read}/{critical_total}; need at least {min_read})"
            )
        if missing:
            return FinalStateDecision(
                is_final=False,
                reason="evidence_contract_not_met",
                continue_prompt=(
                    "Do not finalize yet. Evidence coverage is incomplete for repository analysis. "
                    "Continue tool-driven investigation and satisfy ALL missing checks:\n- "
                    + "\n- ".join(missing)
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
