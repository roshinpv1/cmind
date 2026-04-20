from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_TELEMETRY_STEM = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe_telemetry_stem(stem: str) -> bool:
    if not stem or len(stem) > 128:
        return False
    return bool(_SAFE_TELEMETRY_STEM.fullmatch(stem))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on", "y")


class AgentTelemetry:
    """Best-effort JSONL telemetry writer for agent/planner internals.

    Kill switch:
      - CODEMIND_TELEMETRY_ENABLED=0|1
    Path:
      - CODEMIND_TELEMETRY_DIR (default: /tmp/codemind-telemetry)
    Optional verbosity:
      - CODEMIND_TELEMETRY_INCLUDE_CONTENT=0|1 (default: 0)
      - CODEMIND_TELEMETRY_MAX_FIELD_CHARS (default: 2000)
      - CODEMIND_TELEMETRY_TOOL_DETAIL=0|1 (default: 1) — scrubbed tool *arg* values in ``tool_dispatch`` / repair events
      - CODEMIND_TELEMETRY_TOOL_RESULT_PREVIEW=int (default: 800) — max chars of JSON *result* preview per tool message
    """

    _lock = threading.Lock()

    def __init__(
        self,
        *,
        run_id: str | None,
        component: str,
        playbook: str | None = None,
        repo_id: str | None = None,
    ) -> None:
        self.enabled = _env_enabled("CODEMIND_TELEMETRY_ENABLED", "0")
        self.include_content = _env_enabled("CODEMIND_TELEMETRY_INCLUDE_CONTENT", "0")
        self.max_chars = max(256, int(os.getenv("CODEMIND_TELEMETRY_MAX_FIELD_CHARS", "2000")))
        self.run_id = (run_id or "no_run").strip() or "no_run"
        self.component = component
        self.playbook = playbook
        self.repo_id = repo_id

        base = os.getenv("CODEMIND_TELEMETRY_DIR", "/tmp/codemind-telemetry")
        self.base_dir = Path(base).expanduser()
        self.path = self.base_dir / f"{self.run_id}.jsonl"
        if self.enabled:
            try:
                self.base_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.enabled = False

    def _truncate(self, v: Any) -> Any:
        if isinstance(v, str):
            if len(v) <= self.max_chars:
                return v
            return v[: self.max_chars] + "...<truncated>"
        if isinstance(v, list):
            return [self._truncate(x) for x in v[:50]]
        if isinstance(v, dict):
            out: dict[str, Any] = {}
            for i, (k, val) in enumerate(v.items()):
                if i >= 80:
                    out["__truncated_keys__"] = True
                    break
                out[str(k)] = self._truncate(val)
            return out
        return v

    def emit(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        payload: dict[str, Any] = {
            "ts": _now_iso(),
            "event": event,
            "run_id": self.run_id,
            "component": self.component,
            "playbook": self.playbook,
            "repo_id": self.repo_id,
        }
        if not self.include_content:
            # Drop high-sensitivity/high-volume fields by default.
            fields.pop("content", None)
            fields.pop("prompt", None)
            fields.pop("system_prompt", None)
            fields.pop("messages", None)
            fields.pop("tool_output_raw", None)
        payload.update(self._truncate(fields))
        line = json.dumps(payload, ensure_ascii=False, default=str)
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception:
            # Telemetry must never affect runtime behavior.
            return


_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
)


def telemetry_tool_detail_enabled() -> bool:
    return _env_enabled("CODEMIND_TELEMETRY_TOOL_DETAIL", "1")


def _scrub_key(key: str) -> bool:
    k = key.lower()
    return any(part in k for part in _SENSITIVE_KEY_PARTS)


def _arg_leaf_for_telemetry(val: Any, budget: int, depth: int) -> Any:
    if depth > 4:
        return "<max_depth>"
    if val is None or isinstance(val, (bool, int, float)):
        return val
    if isinstance(val, str):
        if len(val) > budget:
            return val[:budget] + "...<truncated>"
        return val
    if isinstance(val, list):
        n = min(12, len(val))
        out = [_arg_leaf_for_telemetry(x, min(budget, 400), depth + 1) for x in val[:n]]
        if len(val) > n:
            out.append(f"<{len(val) - n} more>")
        return out
    if isinstance(val, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(val.items()):
            if i >= 24:
                out["__truncated__"] = True
                break
            ks = str(k)[:80]
            if _scrub_key(ks):
                out[ks] = "<redacted>"
            else:
                out[ks] = _arg_leaf_for_telemetry(v, min(budget, 400), depth + 1)
        return out
    s = str(val)
    return s[:budget] + ("...<truncated>" if len(s) > budget else "")


def tool_calls_detail_for_telemetry(
    tool_calls: list[Any] | None,
    *,
    telemetry: AgentTelemetry,
) -> list[dict[str, Any]]:
    """Structured tool invocations: names, ids, scrubbed args or arg key list."""
    if not tool_calls:
        return []
    include_vals = telemetry_tool_detail_enabled()
    max_leaf = min(800, max(128, telemetry.max_chars // 4))
    out: list[dict[str, Any]] = []
    for tc in tool_calls:
        d = tc if isinstance(tc, dict) else {}
        name = str(d.get("name") or "")
        tid = str(d.get("id") or "")[:64]
        args = d.get("args")
        if not isinstance(args, dict):
            args = {}
        entry: dict[str, Any] = {
            "name": name,
            "id": tid,
            "type": str(d.get("type") or "tool_call")[:32],
        }
        if include_vals and args:
            entry["args"] = _arg_leaf_for_telemetry(args, max_leaf, 0)
        else:
            keys = sorted(str(k) for k in list(args.keys())[:40])
            if keys:
                entry["arg_keys"] = keys
        out.append(entry)
    return out


_HEAVY_PAYLOAD_KEYS = frozenset({"results", "content", "chunks", "data", "items", "rows"})


def _thin_payload_for_preview(payload: dict[str, Any]) -> dict[str, Any]:
    thin: dict[str, Any] = {}
    for k, v in list(payload.items())[:25]:
        if k in _HEAVY_PAYLOAD_KEYS and isinstance(v, list) and len(v) > 3:
            thin[k] = {"_len": len(v), "_sample": v[:2]}
        elif k == "_meta":
            thin[k] = v
        elif isinstance(v, list) and len(v) > 5:
            thin[k] = {"_len": len(v), "_type": "list"}
        elif isinstance(v, dict) and len(v) > 12:
            thin[k] = {"_len": len(v), "_type": "dict"}
        else:
            thin[k] = v
    return thin


def _tool_result_preview_limit(telemetry: AgentTelemetry) -> int:
    raw = os.getenv("CODEMIND_TELEMETRY_TOOL_RESULT_PREVIEW", "").strip()
    if raw.isdigit():
        return max(0, min(int(raw), 50_000))
    if telemetry.include_content:
        return min(12_000, max(telemetry.max_chars, 4000))
    return 800


def tool_result_summaries_for_telemetry(
    tool_messages: list[Any] | None,
    *,
    telemetry: AgentTelemetry,
) -> list[dict[str, Any]]:
    """Per ToolMessage: success, paths, errors, top-level keys, optional JSON preview."""
    preview_limit = _tool_result_preview_limit(telemetry)
    summaries: list[dict[str, Any]] = []
    for tm in tool_messages or []:
        name = str(getattr(tm, "name", "") or "")
        raw = str(getattr(tm, "content", "") or "{}")
        top_keys: list[str] = []
        preview = ""
        err = ""
        success = False
        fp = ""
        has_results = False
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            top_keys = sorted(str(k) for k in list(payload.keys())[:30])
            err = str(payload.get("error") or "")[:500]
            success = bool(payload.get("success"))
            fp = str(payload.get("file_path") or "")
            has_results = bool(payload.get("results") or payload.get("content"))
            if preview_limit > 0:
                thin = _thin_payload_for_preview(payload)
                try:
                    s = json.dumps(thin, ensure_ascii=False, default=str)
                except TypeError:
                    s = str(thin)
                preview = s[:preview_limit] if len(s) > preview_limit else s
        else:
            if preview_limit > 0:
                preview = raw[:preview_limit]
        row: dict[str, Any] = {
            "name": name,
            "success": success,
            "has_results": has_results,
            "file_path": fp,
            "error": err,
            "content_length": len(raw),
        }
        if top_keys:
            row["top_level_keys"] = top_keys
        if preview:
            row["result_preview"] = preview
        summaries.append(row)
    return summaries


def telemetry_file_path(run_id: str) -> Path:
    """Resolved JSONL path for a run_id (same layout as AgentTelemetry.path)."""
    rid = (run_id or "no_run").strip() or "no_run"
    base = os.getenv("CODEMIND_TELEMETRY_DIR", "/tmp/codemind-telemetry")
    return Path(base).expanduser() / f"{rid}.jsonl"


def read_telemetry_events(
    run_id: str,
    *,
    after: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Read incremental JSONL telemetry for API/UI streaming.

    Args:
        run_id: Correlates with autonomous job_id / planner telemetry_run_id.
        after: Number of non-empty JSONL records already delivered to the client.
        limit: Max new records to return (capped).

    Returns:
        dict with telemetry_enabled, events, next_after, file_exists.
    """
    enabled = _env_enabled("CODEMIND_TELEMETRY_ENABLED", "0")
    lim = max(1, min(int(limit), 2000))
    start = max(0, int(after))
    path = telemetry_file_path(run_id)
    if not path.is_file():
        return {
            "telemetry_enabled": enabled,
            "events": [],
            "next_after": start,
            "file_exists": False,
        }

    out: list[dict[str, Any]] = []
    record_no = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                if record_no < start:
                    record_no += 1
                    continue
                if len(out) >= lim:
                    break
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    out.append({"event": "_json_parse_error", "detail": line[:400]})
                record_no += 1
    except OSError:
        return {
            "telemetry_enabled": enabled,
            "events": [],
            "next_after": start,
            "file_exists": path.is_file(),
        }

    return {
        "telemetry_enabled": enabled,
        "events": out,
        "next_after": start + len(out),
        "file_exists": True,
    }


def telemetry_base_dir() -> Path:
    return Path(os.getenv("CODEMIND_TELEMETRY_DIR", "/tmp/codemind-telemetry")).expanduser()


def list_telemetry_sessions(*, limit: int = 100, base: Path | None = None) -> list[dict[str, Any]]:
    """Recent ``*.jsonl`` telemetry files (stem = run_id), newest first."""
    root = base if base is not None else telemetry_base_dir()
    lim = max(1, min(int(limit), 500))
    if not root.is_dir():
        return []
    try:
        paths = [
            p
            for p in root.glob("*.jsonl")
            if p.is_file() and _safe_telemetry_stem(p.stem)
        ]
    except OSError:
        return []

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    paths.sort(key=_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for p in paths[:lim]:
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(
            {
                "run_id": p.stem,
                "mtime": st.st_mtime,
                "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                "size": st.st_size,
            }
        )
    return out


def poll_telemetry_feed(
    cursors: dict[str, int],
    *,
    session_limit: int = 50,
    limit_per_run: int = 200,
    max_cursor_keys: int = 100,
    extra_stale_runs: int = 25,
) -> dict[str, Any]:
    """Poll incremental events across telemetry JSONL files; merge by ``ts``.

    Used by the admin live view: tracks per-run line cursors client-side.
    """
    enabled = _env_enabled("CODEMIND_TELEMETRY_ENABLED", "0")
    base = telemetry_base_dir()
    sessions = list_telemetry_sessions(limit=session_limit, base=base)
    active_ids = {s["run_id"] for s in sessions}

    clean: dict[str, int] = {}
    for i, (k, v) in enumerate(cursors.items()):
        if i >= max_cursor_keys:
            break
        if not _safe_telemetry_stem(k):
            continue
        try:
            clean[k] = max(0, int(v))
        except (TypeError, ValueError):
            clean[k] = 0

    extras = [r for r in clean if r not in active_ids][:extra_stale_runs]
    order: list[str] = [s["run_id"] for s in sessions]
    for e in extras:
        if e not in order:
            order.append(e)

    flat: list[dict[str, Any]] = []
    next_cursors: dict[str, int] = {}
    per_lim = max(1, min(int(limit_per_run), 2000))
    for rid in order:
        after = int(clean.get(rid, 0))
        chunk = read_telemetry_events(rid, after=after, limit=per_lim)
        flat.extend(chunk["events"])
        next_cursors[rid] = chunk["next_after"]

    flat.sort(key=lambda e: str(e.get("ts") or ""))
    return {
        "telemetry_enabled": enabled,
        "telemetry_dir": str(base),
        "sessions": sessions,
        "events": flat,
        "cursors": next_cursors,
    }

