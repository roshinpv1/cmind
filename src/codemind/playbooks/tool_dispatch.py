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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage

from codemind.llm.context_manager import sanitize_tool_output_for_tool

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_MAX_CALLS_PER_TURN = int(os.getenv("CODEMIND_MAX_TOOL_CALLS_PER_TURN", "8"))
_ONE_SEARCH_PER_TURN = (
    os.getenv("CODEMIND_ONE_SEARCH_PER_TURN", "1").lower() not in ("0", "false", "no")
)
_ENFORCE_MIRROR_WRITES = (
    os.getenv("CODEMIND_ENFORCE_MIRROR_WRITES", "0").lower() in ("1", "true", "yes")
)
_MAX_IDENTICAL_CALLS_PER_RUN = max(
    2,
    int(os.getenv("CODEMIND_MAX_IDENTICAL_CALLS_PER_RUN", "4")),
)
_NOISE_TOOLS = frozenset({"search_codebase", "search_code", "search_bm25"})
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
_TRACE_DIR = os.getenv("CODEMIND_TRACE_DIR", "/tmp")

# Local models often emit near-miss tool names. Map them to canonical tools.
_TOOL_NAME_ALIASES = {
    "write_file": "write_file_system",
    "write_file_to_disk": "write_file_system",
}

# Tools that operate on repository-scoped graph/index data and therefore require
# a repo_id (either provided in args or enforced by dispatcher context).
_REPO_REQUIRED_TOOLS = frozenset({
    "get_map",
    "trace_path",
    "graphify_query",
    "graphify_shortest_path",
    "graphify_path",
    "graphify_explain",
    "graphify_neighbors",
    "graphify_community",
    "graphify_god_nodes",
    "graphify_run",
    "search_code",
    "search_bm25",
    "search_codebase",
    "read_file",
    "get_file_outline",
    "search_symbol",
    "get_callers",
    "get_callees",
    "get_dependencies",
    "list_files",
    "list_repo_directory",
})

# Tools with potential side effects.
_WRITE_TOOLS = frozenset({
    "write_file_system",
    "save_catalog_entry",
    "graphify_add",
})

# Read-only introspection/query tools.
_READ_ONLY_TOOLS = frozenset({
    "get_map",
    "trace_path",
    "graphify_query",
    "graphify_shortest_path",
    "graphify_path",
    "graphify_explain",
    "graphify_neighbors",
    "graphify_community",
    "graphify_god_nodes",
    "search_code",
    "search_bm25",
    "search_codebase",
    "read_file",
    "get_file_outline",
    "search_symbol",
    "get_callers",
    "get_callees",
    "get_dependencies",
    "list_files",
    "list_repo_directory",
    "list_file_system",
    "read_file_system",
    "search_catalogs",
})


@dataclass(frozen=True)
class ToolSpec:
    """Runtime policy metadata for one tool."""

    name: str
    read_only: bool = True
    concurrency_safe: bool = True
    requires_repo: bool = False
    requires_mirror: bool = False
    idempotent: bool = True
    fallback_tools: tuple[str, ...] = ()


def _build_tool_specs(available_tool_names: set[str]) -> dict[str, ToolSpec]:
    """Build default ToolSpec entries for currently available tools."""
    specs: dict[str, ToolSpec] = {}
    for raw_name in available_tool_names:
        name = _canonical_tool_name(raw_name)
        if not name:
            continue
        is_write = name in _WRITE_TOOLS
        read_only = (name in _READ_ONLY_TOOLS) and not is_write
        specs[name] = ToolSpec(
            name=name,
            read_only=read_only,
            concurrency_safe=True,
            requires_repo=name in _REPO_REQUIRED_TOOLS,
            requires_mirror=(name == "write_file_system"),
            idempotent=(name not in {"graphify_add", "save_catalog_entry"}),
            fallback_tools=(
                ("list_repo_directory", "get_map")
                if name == "list_files"
                else ("list_files", "get_map")
                if name == "list_repo_directory"
                else ("list_repo_directory", "list_files")
                if name == "get_map"
                else ("list_repo_directory", "get_map")
                if name in {"search_code", "search_bm25", "search_codebase", "read_file"}
                else ()
            ),
        )
    return specs


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


def _canonical_tool_name(name: Any) -> str:
    raw = _to_str(name).strip()
    if not raw:
        return ""
    return _TOOL_NAME_ALIASES.get(raw, raw)


def _safe_filename_part(value: str) -> str:
    keep = []
    for ch in value:
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep)[:120] or "unknown"


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

        # Strategy A: ```json { ... } ``` or ```json [ ... ] ``` fenced blocks
        for m in re.finditer(r"```(?:json)?\s*([{\[].*?[}\]])\s*```", content, re.DOTALL):
            parsed = _try_parse_json(m.group(1))
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        calls.extend(self._extract_calls(item))
            elif isinstance(parsed, dict):
                calls.extend(self._extract_calls(parsed))
        if calls:
            return calls

        # Strategy B: leading balanced JSON object or array
        raw = content.strip()
        leading = None
        if raw.startswith("["):
            # Try to parse a leading JSON array
            try:
                import json as _json
                dec = _json.JSONDecoder()
                arr, _ = dec.raw_decode(raw)
                if isinstance(arr, list):
                    leading = arr
            except Exception:
                pass
        if leading is None:
            leading = _leading_json(content)

        if isinstance(leading, list):
            for item in leading:
                if isinstance(item, dict):
                    calls.extend(self._extract_calls(item))
        elif isinstance(leading, dict):
            calls = self._extract_calls(leading)
        if calls:
            return calls

        # Strategy C: every JSON object in the text
        for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL):
            obj = _try_parse_json(m.group(0))
            if obj:
                calls.extend(self._extract_calls(obj))

        return calls

    def _extract_calls(self, data: dict) -> list[dict]:
        """Extract tool calls from a single JSON object in any recognised format."""
        calls: list[dict] = []

        def _resolve_args(tc: dict) -> dict:
            """Return args dict from any of the common arg-key aliases."""
            return (
                tc.get("args")
                or tc.get("arguments")
                or tc.get("parameters")
                or tc.get("params")
                or {}
            )

        def _resolve_name(tc: dict) -> str:
            """Return canonical tool name from any of the common name-key aliases."""
            raw = tc.get("name") or tc.get("tool_name") or tc.get("tool") or tc.get("action") or ""
            return _canonical_tool_name(raw)

        # {"tool_calls": [...]}
        if "tool_calls" in data:
            for tc in data["tool_calls"]:
                name = _resolve_name(tc)
                if name in self._tools:
                    calls.append({
                        "name": name,
                        "args": _resolve_args(tc),
                        "id": tc.get("id") or _make_call_id(),
                        "type": "tool_call",
                    })
            return calls

        # Single-call object: any combination of (name|tool_name|tool|action) + (args|params|arguments|parameters)
        name = _resolve_name(data)
        if name in self._tools:
            calls.append({
                "name": name,
                "args": _resolve_args(data),
                "id": _make_call_id(),
                "type": "tool_call",
            })
            return calls

        # {"action": "tool_name", ...} — "planning JSON" common in local models
        canonical_action = _canonical_tool_name(data.get("action"))
        if canonical_action and canonical_action in self._tools:
            skip = {"action", "phase", "tool", "name", "arguments", "args", "parameters"}
            args = {k: v for k, v in data.items() if k not in skip}
            calls.append({
                "name": canonical_action,
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
        enforced_mirror_root: str | None = None,
        prefer_mirror_reads: bool = False,
    ) -> None:
        self.tools = playbook_tools
        self._repair = ToolCallRepair(available_tool_names)
        self._tool_specs = _build_tool_specs(available_tool_names)
        # Normalise to a single string for injection
        if isinstance(enforced_repo_id, list):
            self._repo_id: str | None = enforced_repo_id[0] if enforced_repo_id else None
        else:
            self._repo_id = enforced_repo_id or None
        self._mirror_root = str(Path(enforced_mirror_root).resolve()) if enforced_mirror_root else None
        self._prefer_mirror_reads = bool(prefer_mirror_reads)
        self._call_signature_counts: dict[str, int] = {}
        # Pre-create execution trace directory so it is always visible on disk.
        # This avoids confusion when no tool calls have been executed yet.
        self._exec_trace_dir: Path | None = None
        try:
            trace_root = Path(_TRACE_DIR) / "codemind_executed_tool_calls"
            trace_root.mkdir(parents=True, exist_ok=True)
            self._exec_trace_dir = trace_root
        except Exception:
            logger.debug("Failed to initialize executed tool call trace directory", exc_info=True)

    # ── public repair entry point ─────────────────────────────────────────────

    def repair_tool_calls(self, content: str) -> list[dict]:
        """Try to extract tool calls from plain-text LLM content."""
        return self._repair.repair(content)

    def get_tool_specs(self) -> dict[str, ToolSpec]:
        """Expose immutable runtime tool policy metadata for debugging/telemetry."""
        return dict(self._tool_specs)

    @staticmethod
    def _sanitize_args_for_signature(args: dict) -> dict:
        """Drop volatile/runtime-only keys before computing call signatures."""
        if not isinstance(args, dict):
            return {}
        volatile = {
            "_mirror_root",
            "_prefer_mirror_reads",
            "_mirrored_from_path",
            "id",
            "request_id",
            "timestamp",
        }
        return {k: v for k, v in args.items() if k not in volatile}

    def _call_signature(self, name: str, args: dict) -> str:
        """Return stable signature for repetition checks and telemetry."""
        safe_args = self._sanitize_args_for_signature(args)
        try:
            arg_blob = json.dumps(safe_args, sort_keys=True, default=str)
        except Exception:
            arg_blob = _to_str(safe_args)
        return f"{name}|{arg_blob}"

    def _classify_outcome(
        self,
        *,
        name: str,
        args: dict,
        result: dict | None = None,
        error: str | None = None,
        code: str | None = None,
    ) -> dict:
        """
        Normalize tool outcome for agent-orchestration decisions.

        outcome ∈ {success, no_data, retryable_error, fatal_error}
        """
        spec = self._tool_specs.get(name)
        signature = self._call_signature(name, args)
        fallback_tools = list(spec.fallback_tools) if spec else []
        evidence_score = 0.0
        outcome = "success"

        if error:
            low = error.lower()
            if any(x in low for x in ("timeout", "temporar", "rate", "unavailable", "connection")):
                outcome = "retryable_error"
            else:
                outcome = "fatal_error"
        elif code and str(code).startswith("policy_"):
            outcome = "fatal_error"
        else:
            payload = result if isinstance(result, dict) else {}
            if payload.get("error"):
                low = str(payload.get("error", "")).lower()
                if "no matches found" in low or "not found" in low:
                    outcome = "no_data"
                else:
                    outcome = "retryable_error"
            else:
                count = payload.get("count")
                if isinstance(count, int):
                    if count > 0:
                        evidence_score += 1.0
                    else:
                        outcome = "no_data"
                for key in ("results", "files", "content", "context", "path", "top_nodes", "entry_points"):
                    val = payload.get(key)
                    if isinstance(val, list) and len(val) > 0:
                        evidence_score += 0.6
                    elif isinstance(val, str):
                        txt = val.strip().lower()
                        if txt and txt not in {"no matches found.", "no matching files.", "no results found.", "[]", "{}"}:
                            evidence_score += 0.4
                if evidence_score <= 0.0 and outcome == "success":
                    outcome = "no_data"

        return {
            "outcome": outcome,
            "evidence_score": round(min(evidence_score, 2.0), 3),
            "call_signature": signature,
            "fallback_tools": fallback_tools,
            "tool": name,
        }

    def _check_repetition(self, *, name: str, args: dict) -> tuple[bool, dict | None]:
        """
        Deny identical repeated calls beyond threshold (loop breaker).
        """
        signature = self._call_signature(name, args)
        new_count = self._call_signature_counts.get(signature, 0) + 1
        self._call_signature_counts[signature] = new_count
        if new_count <= _MAX_IDENTICAL_CALLS_PER_RUN:
            return True, None
        spec = self._tool_specs.get(name)
        return False, {
            "error": (
                f"Identical tool call '{name}' repeated {new_count} times. "
                f"Loop breaker triggered at {_MAX_IDENTICAL_CALLS_PER_RUN}."
            ),
            "code": "policy_repetition_limit",
            "tool": name,
            "call_signature": signature,
            "repeat_count": new_count,
            "max_identical_calls": _MAX_IDENTICAL_CALLS_PER_RUN,
            "tool_spec": {
                "fallback_tools": list(spec.fallback_tools) if spec else [],
            },
        }

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

    def _rewrite_write_path_to_mirror(self, args: dict) -> dict:
        """
        Rewrite write_file_system path into run-scoped mirror root.

        Guarantees generated files never mutate original source paths.
        """
        if not self._mirror_root:
            return args

        # Accept both 'path' and 'file_path' — models commonly use file_path
        raw_path = _to_str(args.get("path") or args.get("file_path")).strip()
        if not raw_path:
            return args

        mirror_root = Path(self._mirror_root)
        mirror_root.mkdir(parents=True, exist_ok=True)
        path_obj = Path(raw_path)
        target_path: Path

        # If path is absolute and belongs to repo root, preserve repo-relative structure.
        if path_obj.is_absolute() and self._repo_id and hasattr(self.tools, "_get_repo_root_sync"):
            repo_root = self.tools._get_repo_root_sync(self._repo_id)  # internal helper usage by design
            if repo_root:
                try:
                    rel = path_obj.resolve().relative_to(repo_root.resolve())
                    target_path = mirror_root / rel
                except ValueError:
                    # External absolute path: keep under _external with safe flattening.
                    safe_rel = str(path_obj).lstrip("/").replace(":", "_")
                    target_path = mirror_root / "_external" / safe_rel
            else:
                safe_rel = str(path_obj).lstrip("/").replace(":", "_")
                target_path = mirror_root / "_external" / safe_rel
        elif path_obj.is_absolute():
            safe_rel = str(path_obj).lstrip("/").replace(":", "_")
            target_path = mirror_root / "_external" / safe_rel
        else:
            target_path = mirror_root / path_obj

        patched = dict(args)
        patched["_mirrored_from_path"] = raw_path
        patched["path"] = str(target_path.resolve())
        return patched

    def _inject_execution_context(self, args: dict) -> dict:
        """Attach mirror execution context so read/search tools can prefer mirror files."""
        if not self._mirror_root:
            return args
        patched = dict(args)
        patched.setdefault("_mirror_root", self._mirror_root)
        if self._prefer_mirror_reads:
            patched.setdefault("_prefer_mirror_reads", True)
        return patched

    def _evaluate_policy(self, name: str, args: dict) -> tuple[bool, dict | None]:
        """
        Enforce dispatcher-level tool policy before execution.

        Returns:
            (True, None) if allowed
            (False, error_payload) if denied
        """
        spec = self._tool_specs.get(name)
        if not spec:
            return False, {
                "error": f"Tool '{name}' is not in the active tool registry.",
                "code": "policy_tool_not_allowed",
                "tool": name,
            }

        effective_repo_id = _to_str(args.get("repo_id")) or _to_str(self._repo_id)
        if spec.requires_repo and not effective_repo_id:
            return False, {
                "error": (
                    f"Tool '{name}' requires repo_id but none was provided/enforced."
                ),
                "code": "policy_repo_required",
                "tool": name,
                "tool_spec": {
                    "requires_repo": spec.requires_repo,
                    "read_only": spec.read_only,
                    "fallback_tools": list(spec.fallback_tools),
                },
            }

        if spec.requires_mirror and _ENFORCE_MIRROR_WRITES and not self._mirror_root:
            return False, {
                "error": (
                    f"Tool '{name}' requires an enforced mirror root for safe writes, "
                    "but no mirror root is configured."
                ),
                "code": "policy_mirror_required",
                "tool": name,
                "tool_spec": {
                    "requires_mirror": spec.requires_mirror,
                    "read_only": spec.read_only,
                },
            }

        return True, None

    def _write_executed_tool_call_trace(
        self,
        *,
        call_id: str,
        name: str,
        args: dict,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        """
        Persist one JSON file per executed tool call for post-run debugging.
        """
        try:
            trace_root = self._exec_trace_dir or (Path(_TRACE_DIR) / "codemind_executed_tool_calls")
            trace_root.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            fname = f"{ts}_{_safe_filename_part(call_id)}_{_safe_filename_part(name)}.json"
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "call_id": call_id,
                "tool_name": name,
                "args": args,
                "result": result,
                "error": error,
                "repo_id_enforced": self._repo_id,
                "mirror_root": self._mirror_root,
            }
            with open(trace_root / fname, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, default=str)
        except Exception:
            # tracing is best-effort and must never break tool execution
            logger.debug("Failed to write executed tool call trace", exc_info=True)

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
            args = self._inject_execution_context(args)
            if name == "write_file_system":
                args = self._rewrite_write_path_to_mirror(args)
            call_id = tc.get("id") or _make_call_id()

            allowed, deny_payload = self._evaluate_policy(name, args)
            if not allowed:
                if isinstance(deny_payload, dict):
                    deny_payload = dict(deny_payload)
                    deny_payload["_meta"] = self._classify_outcome(
                        name=name,
                        args=args,
                        result=deny_payload,
                        error=deny_payload.get("error"),
                        code=deny_payload.get("code"),
                    )
                content = sanitize_tool_output_for_tool(
                    name, json.dumps(deny_payload, default=str)
                )
                self._write_executed_tool_call_trace(
                    call_id=call_id,
                    name=name,
                    args=args,
                    result=deny_payload,
                    error=deny_payload.get("error") if isinstance(deny_payload, dict) else "policy_denied",
                )
                messages.append(
                    ToolMessage(content=content, tool_call_id=call_id, name=name)
                )
                continue

            allowed_repeat, repeat_payload = self._check_repetition(name=name, args=args)
            if not allowed_repeat:
                repeat_payload = dict(repeat_payload or {})
                repeat_payload["_meta"] = self._classify_outcome(
                    name=name,
                    args=args,
                    result=repeat_payload,
                    error=repeat_payload.get("error"),
                    code=repeat_payload.get("code"),
                )
                content = sanitize_tool_output_for_tool(
                    name, json.dumps(repeat_payload, default=str)
                )
                self._write_executed_tool_call_trace(
                    call_id=call_id,
                    name=name,
                    args=args,
                    result=repeat_payload,
                    error=repeat_payload.get("error"),
                )
                messages.append(
                    ToolMessage(content=content, tool_call_id=call_id, name=name)
                )
                continue

            try:
                result = await self.tools.execute_tool(name, args)
                result = self._filter_noise(name, result)
                if isinstance(result, dict):
                    result = dict(result)
                else:
                    result = {"result": result}
                result["_meta"] = self._classify_outcome(
                    name=name,
                    args=args,
                    result=result,
                    error=result.get("error"),
                )
                content = sanitize_tool_output_for_tool(
                    name, json.dumps(result, default=str)
                )
                self._write_executed_tool_call_trace(
                    call_id=call_id,
                    name=name,
                    args=args,
                    result=result,
                    error=None,
                )
            except Exception as exc:
                logger.exception("Tool '%s' execution failed: %s", name, exc)
                err_payload = {
                    "error": str(exc),
                    "tool": name,
                }
                err_payload["_meta"] = self._classify_outcome(
                    name=name,
                    args=args,
                    result=err_payload,
                    error=str(exc),
                )
                content = json.dumps(err_payload)
                self._write_executed_tool_call_trace(
                    call_id=call_id,
                    name=name,
                    args=args,
                    result=err_payload,
                    error=str(exc),
                )

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
