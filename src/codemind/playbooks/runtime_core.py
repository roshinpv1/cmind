"""
Core runtime primitives for server-side code analysis execution.

This module provides a small, stable surface around four concerns:
1) fetching code/graph context,
2) compacting context safely,
3) normalizing playbook execution envelopes,
4) mapping outputs into deterministic API-friendly shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuntimeContextBundle:
    """Structured context emitted before/while playbook execution."""

    repo_id: str | None = None
    sources: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    evidence_count: int = 0
    diagnostics: list[str] = field(default_factory=list)


class PlaybookResultMapper:
    """
    Normalize playbook results to one stable envelope.

    This is intentionally conservative: it keeps existing fields while ensuring
    callers can always rely on:
      - success (bool)
      - error (str|None)
      - outputs.result (str)
      - outputs.data (dict|None)
      - outputs.playbook (str)
      - outputs.context (dict)
    """

    @staticmethod
    def _normalize_outputs(
        playbook_name: str,
        outputs: dict[str, Any] | None,
        logs: list[str] | None,
    ) -> dict[str, Any]:
        out = dict(outputs or {})

        if "result" not in out:
            # Keep backward compatibility: if no explicit textual result exists,
            # serialize known structured data to text only when needed.
            if isinstance(out.get("data"), dict):
                out["result"] = "Structured result available in outputs.data"
            elif isinstance(out.get("report"), str):
                out["result"] = out["report"]
            else:
                out["result"] = ""

        if "data" in out:
            data_val = out.get("data")
            if data_val is None:
                out["data"] = None
            elif isinstance(data_val, dict):
                out["data"] = data_val
            elif isinstance(data_val, list):
                # Keep list payloads as-is (some playbooks naturally return arrays).
                out["data"] = data_val
            else:
                # Preserve scalar payloads but avoid turning `None` into {"value": null}
                # which can be misinterpreted as valid evidence.
                out["data"] = {"value": data_val}
        else:
            out["data"] = None

        out.setdefault("tool_executed", bool(out.get("tool_result")))
        out.setdefault("tool_result", None)
        out.setdefault("iterations", out.get("iteration", 0))
        out.setdefault("playbook", playbook_name)

        # Stable context sub-object (used by API/UI for telemetry and summaries).
        context_obj = out.get("context")
        if not isinstance(context_obj, dict):
            context_obj = {}
        context_obj.setdefault("sources", [])
        context_obj.setdefault("evidence_count", 0)
        context_obj.setdefault("log_count", len(logs or []))
        out["context"] = context_obj

        return out

    def map_result(
        self,
        *,
        playbook_name: str,
        success: bool,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        logs: list[str] | None = None,
    ) -> dict[str, Any]:
        mapped_outputs = self._normalize_outputs(playbook_name, outputs, logs)
        return {
            "success": bool(success),
            "outputs": mapped_outputs,
            "error": error,
            "logs": list(logs or []),
        }
