"""
Tool dispatch pipeline for the ReAct agent.

Pipeline (in order):
  1. Repair     – recover tool calls from plain-text JSON (local models don't do native function calling)
  2. Coalesce   – merge multiple search_code calls into one batched call
  3. Enforce    – cap total calls per turn; one-search-per-turn policy
  4. Execute    – call PlaybookTools.execute_tool() for each effective call
  5. Sanitize   – trim output to per-tool token budget; wrap as ToolMessage
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from langchain_core.messages import ToolMessage

from codemind.llm.context_manager import sanitize_tool_output_for_tool

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_MAX_CALLS_PER_TURN = int(os.getenv("CODEMIND_MAX_TOOL_CALLS_PER_TURN", "8"))
_ONE_SEARCH_PER_TURN = (
    os.getenv("CODEMIND_ONE_SEARCH_PER_TURN", "1").lower() not in ("0", "false", "no")
)
_NOISE_TOOLS = frozenset({"search_codebase", "search_code"})
_NOISE_PATTERNS = (
    "graph.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    ".map",
    ".bin",
    "node_modules",
    ".min.js",
    ".min.css",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_str(v: Any) -> str:
    """Coerce any value to a stable string (for use in dict keys and sets)."""
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    if isinstance(v, (int, float, bool)):
        return str(v)
    try:
        return json.dumps(v, sort_keys=True, default=str)
    except Exception:
        return str(v)


def _make_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:8]}"


# ── ToolCallRepair ────────────────────────────────────────────────────────────

class ToolCallRepair:
    """
    Recover tool calls from plain-text JSON that local models emit instead of
    native function-call tokens.

    Recognised formats:
      {"tool_calls": [{"name": "...", "args": {...}}]}
      {"tool": "name", "args": {...}}
      {"name": "tool_name", "arguments": {...}}
      {"action": "tool_name", ...}            ← "planning JSON" emitted by many local models
    """

    def __init__(self, available_tool_names: set[str]) -> None:
        self._tools = available_tool_names

    def repair(self, content: str) -> list[dict]:
        """Return normalised tool_call dicts parsed from *content*, or []."""
        import re

        calls: list[dict] = []

        # Strategy A: ```json { ... } ``` fenced blocks
        for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL):
            obj = _try_parse_json(m.group(1))
            if obj:
                calls.extend(self._extract_calls(obj))
        if calls:
            return calls

        # Strategy B: leading balanced JSON object
        obj = _leading_json(content)
        if obj:
            calls = self._extract_calls(obj)
            if calls:
                return calls

        # Strategy C: every JSON object in the text
        for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL):
            obj = _try_parse_json(m.group(0))
            if obj:
                calls.extend(self._extract_calls(obj))

        return calls

    def _extract_calls(self, data: dict) -> list[dict]:
        calls: list[dict] = []

        # {"tool_calls": [...]}
        if "tool_calls" in data:
            for tc in data["tool_calls"]:
                name = tc.get("name", "")
                if name in self._tools:
                    calls.append({
                        "name": name,
                        "args": tc.get("args") or tc.get("arguments") or {},
                        "id": tc.get("id") or _make_call_id(),
                        "type": "tool_call",
                    })
            return calls

        # {"tool": "name", "args": {...}}
        if "tool" in data and _to_str(data.get("tool")) in self._tools:
            name = _to_str(data["tool"])
            calls.append({
                "name": name,
                "args": data.get("args") or data.get("arguments") or data.get("parameters") or {},
                "id": _make_call_id(),
                "type": "tool_call",
            })
            return calls

        # {"name": "tool_name", ...}
        if "name" in data and _to_str(data.get("name")) in self._tools:
            name = _to_str(data["name"])
            calls.append({
                "name": name,
                "args": data.get("args") or data.get("arguments") or data.get("parameters") or {},
                "id": _make_call_id(),
                "type": "tool_call",
            })
            return calls

        # {"action": "tool_name", ...} — "planning JSON" common in local models
        if "action" in data and isinstance(data["action"], str) and data["action"] in self._tools:
            skip = {"action", "phase", "tool", "name", "arguments", "args", "parameters"}
            args = {k: v for k, v in data.items() if k not in skip}
            calls.append({
                "name": data["action"],
                "args": args,
                "id": _make_call_id(),
                "type": "tool_call",
            })
            return calls

        return calls


# ── ToolDispatcher ────────────────────────────────────────────────────────────

class ToolDispatcher:
    """
    Executes a list of tool calls against *PlaybookTools*, applying the full
    sanitisation/coalescing pipeline before and after execution.

    *enforced_repo_id* — when set, every tool call that accepts a ``repo_id``
    parameter will have it stamped in before execution.  This is the single
    source of truth; it does NOT rely on the LLM to remember to pass the id.
    """

    def __init__(
        self,
        playbook_tools,
        available_tool_names: set[str],
        enforced_repo_id: str | list[str] | None = None,
    ) -> None:
        self.tools = playbook_tools
        self._repair = ToolCallRepair(available_tool_names)
        # Normalise to a single string for injection
        if isinstance(enforced_repo_id, list):
            self._repo_id: str | None = enforced_repo_id[0] if enforced_repo_id else None
        else:
            self._repo_id = enforced_repo_id or None

    # ── public repair entry point ─────────────────────────────────────────────

    def repair_tool_calls(self, content: str) -> list[dict]:
        """Try to extract tool calls from plain-text LLM content."""
        return self._repair.repair(content)

    # ── coalescing ────────────────────────────────────────────────────────────

    def _coalesce(self, calls: list[dict]) -> list[dict]:
        """Merge all search_code calls with the same (repo_id, includes, limit) into one."""
        grouped: dict[tuple, dict] = {}
        others: list[tuple[int, dict]] = []

        for idx, tc in enumerate(calls):
            if tc.get("name") != "search_code":
                others.append((idx, tc))
                continue

            args = tc.get("args") or {}
            raw_includes = args.get("includes") or []
            if not isinstance(raw_includes, list):
                raw_includes = [raw_includes]
            includes = tuple(
                sorted(_to_str(x).strip() for x in raw_includes if _to_str(x).strip())
            )
            repo_id = _to_str(args.get("repo_id")) or None
            try:
                limit = int(args.get("limit", 500))
            except (TypeError, ValueError):
                limit = 500

            key = (repo_id, includes, limit)
            if key not in grouped:
                grouped[key] = {
                    "idx": idx,
                    "id": tc.get("id"),
                    "repo_id": repo_id,
                    "includes": list(includes),
                    "limit": limit,
                    "queries": [],
                    "_seen": set(),
                }
            g = grouped[key]
            for q in _extract_queries(args):
                if q not in g["_seen"]:
                    g["_seen"].add(q)
                    g["queries"].append(q)

        search_items: list[tuple[int, dict]] = []
        for g in grouped.values():
            qs = g["queries"][:25]
            if not qs:
                continue
            search_items.append((
                g["idx"],
                {
                    "name": "search_code",
                    "args": {
                        "repo_id": g["repo_id"],
                        "includes": g["includes"],
                        "limit": g["limit"],
                        "query": qs[0],
                        "queries": qs,
                    },
                    "id": g["id"] or _make_call_id(),
                    "type": "tool_call",
                },
            ))

        original_search = sum(1 for tc in calls if tc.get("name") == "search_code")
        merged_search = len(search_items)
        if original_search > merged_search:
            logger.info("Coalesced %d search_code → %d", original_search, merged_search)

        all_items = others + search_items
        all_items.sort(key=lambda x: x[0])
        return [tc for _, tc in all_items]

    # ── limit enforcement ─────────────────────────────────────────────────────

    def _enforce_limits(self, calls: list[dict]) -> tuple[list[dict], list[str]]:
        logs: list[str] = []
        cap = max(1, min(_MAX_CALLS_PER_TURN, 50))

        if len(calls) > cap:
            dropped = len(calls) - cap
            calls = calls[:cap]
            logs.append(f"Dropped {dropped} excess tool calls (cap={cap})")

        if _ONE_SEARCH_PER_TURN:
            seen_search = False
            filtered: list[dict] = []
            dropped_search = 0
            for tc in calls:
                if tc.get("name") == "search_code":
                    if not seen_search:
                        seen_search = True
                        filtered.append(tc)
                    else:
                        dropped_search += 1
                else:
                    filtered.append(tc)
            if dropped_search:
                logs.append(f"One-search-per-turn: dropped {dropped_search} extra search_code calls")
            calls = filtered

        return calls, logs

    # ── noise filtering ───────────────────────────────────────────────────────

    @staticmethod
    def _filter_noise(tool_name: str, result: dict) -> dict:
        if tool_name not in _NOISE_TOOLS:
            return result
        res_list = result.get("results") or []
        before = len(res_list)

        def _is_noisy(r) -> bool:
            # results can be dicts (search_codebase) or strings (search_code match lines)
            if isinstance(r, dict):
                path = r.get("file_path", "").lower()
            else:
                path = str(r).lower()
            return any(n in path for n in _NOISE_PATTERNS)

        filtered = [r for r in res_list if not _is_noisy(r)]
        if len(filtered) < before:
            result = dict(result)
            result["results"] = filtered
            logger.debug("Filtered %d noise results from %s", before - len(filtered), tool_name)
        return result

    # ── repo_id injection ─────────────────────────────────────────────────────

    def _inject_repo_id(self, calls: list[dict]) -> list[dict]:
        """
        Stamp ``repo_id`` into every tool call that doesn't already have one.

        This guarantees the correct repository is queried regardless of whether
        the LLM remembered to include it — fixes ``KeyError: 'repo_id'`` and
        cross-repository data contamination.
        """
        if not self._repo_id:
            return calls
        result = []
        for tc in calls:
            args = dict(tc.get("args") or {})
            if not args.get("repo_id"):
                args["repo_id"] = self._repo_id
                tc = {**tc, "args": args}
            result.append(tc)
        return result

    # ── dispatch ──────────────────────────────────────────────────────────────

    async def dispatch(self, raw_calls: list[dict]) -> list[ToolMessage]:
        """
        Full pipeline: inject_repo_id → coalesce → enforce limits → execute → filter noise → sanitize.
        Returns a list of ToolMessages ready to append to the conversation.
        """
        calls = self._inject_repo_id(raw_calls)
        calls = self._coalesce(calls)
        calls, limit_logs = self._enforce_limits(calls)
        for log in limit_logs:
            logger.info(log)

        messages: list[ToolMessage] = []
        for tc in calls:
            name = _to_str(tc.get("name"))
            args = tc.get("args") or {}
            call_id = tc.get("id") or _make_call_id()

            try:
                result = await self.tools.execute_tool(name, args)
                result = self._filter_noise(name, result)
                content = sanitize_tool_output_for_tool(
                    name, json.dumps(result, default=str)
                )
            except Exception as exc:
                logger.exception("Tool '%s' execution failed: %s", name, exc)
                content = json.dumps({"error": str(exc), "tool": name})

            messages.append(
                ToolMessage(content=content, tool_call_id=call_id, name=name)
            )

        return messages


# ── Private helpers ───────────────────────────────────────────────────────────

def _try_parse_json(text: str) -> dict | None:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def _leading_json(text: str) -> dict | None:
    s = text.strip()
    start = s.find("{")
    if start < 0:
        return None
    dec = json.JSONDecoder()
    try:
        obj, _ = dec.raw_decode(s[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_queries(args: dict) -> list[str]:
    qs: list[str] = []
    q = args.get("query")
    if isinstance(q, str) and q.strip():
        qs.append(q.strip())
    qv = args.get("queries")
    if isinstance(qv, list):
        qs.extend(str(qi).strip() for qi in qv if str(qi).strip())
    return qs
