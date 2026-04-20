"""
ReAct (Reason + Act) loop for playbooks.

Design:
  - Plain async loop — no LangGraph StateGraph, making the flow transparent and debuggable.
  - The agent autonomously decides which tools to call based on the playbook instructions
    and the user's goal.  There is NO static classification of playbooks (generation vs
    analysis).  The agent figures out the right approach.
  - Stop conditions:
      * Model issues no tool calls and tool history exists  → natural finish.
      * Model issues no tool calls and no tool history      → finish with prose answer.
      * iteration >= max_iterations                         → synthesise from collected evidence.
  - Tool-call repair: plain-text JSON plans are parsed and converted to proper tool_calls.
  - Safety net: if the model outputs code as prose instead of calling write_file_system,
    we attempt to persist it automatically (applies to any playbook, not just "generators").
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from .final_state import evaluate_final_state
from .orchestration_controller import NextAction, OrchestrationController
from .orchestration_policies import MIN_REPO_TOOL_CALLS, should_force_continuation
from .quality_guard import evaluate_quality_guard
from codemind.utils.agent_telemetry import (
    AgentTelemetry,
    tool_calls_detail_for_telemetry,
    tool_result_summaries_for_telemetry,
)

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_MAX_ITER_DEFAULT = int(os.getenv("CODEMIND_REACT_MAX_ITERATIONS", "50"))
_THINK_FRACTION   = float(os.getenv("CODEMIND_THINK_FRACTION", "0.25"))
_MAX_THINK_TOKENS = int(os.getenv("CODEMIND_MAX_THINK_TOKENS", "4096"))
_MIN_THINK_TOKENS = int(os.getenv("CODEMIND_MIN_THINK_TOKENS", "512"))
_SYNTH_MAX_TOKENS = int(os.getenv("CODEMIND_SYNTH_MAX_TOKENS", "8192"))
_TRACE_DIR        = os.getenv("CODEMIND_TRACE_DIR", "/tmp")
_MAX_FINALIZATION_RETRIES = max(
    1, int(os.getenv("CODEMIND_FINALIZATION_MAX_RETRIES", "2"))
)
_ENABLE_VERIFIER_PASS = os.getenv("CODEMIND_ENABLE_VERIFIER_PASS", "0").lower() in ("1", "true", "yes")
_ENABLE_GROUNDING_GATE = os.getenv("CODEMIND_ENABLE_GROUNDING_GATE", "0").lower() in ("1", "true", "yes")
_ENABLE_RISK_WEIGHTED_COVERAGE = os.getenv(
    "CODEMIND_ENABLE_RISK_WEIGHTED_COVERAGE", "0"
).lower() in ("1", "true", "yes")
_QUALITY_GUARD_ENFORCE = os.getenv("CODEMIND_QUALITY_GUARD_ENFORCE", "0").lower() in ("1", "true", "yes")

_AGENT_DIRECTIVE = """
### AGENT PROTOCOL

You are an autonomous agent. Decide what tools to call based on the task at hand.

- Examine the user goal and the playbook instructions to determine the right approach.
- **For repository analysis tasks**: start by calling `get_map` with the provided repo_id to
  understand the codebase structure, then use `search_code`, `read_file`, `trace_path`,
  `get_callers`, `get_callees` etc. to explore deeply before drawing conclusions.
- **For code generation tasks**: call `write_file_system` to save each generated file to disk.
- **For general tasks without a repo**: use `write_file_system`, `read_file_system`,
  `list_file_system` as appropriate, or simply respond with prose if no tool is needed.
- If a tool returns an error (e.g. missing repo_id), adapt — try a different tool or approach.
- When you have enough evidence or have completed the task, provide your final answer.
- Base every claim on the tool data you observed. Do not hallucinate.
- **IMPORTANT**: Do NOT return a planning summary as your final answer. If you write
  "next I will explore X", you must IMMEDIATELY call the tools to do so — do not stop.
"""

_MAX_CONSECUTIVE_NO_EVIDENCE = max(
    1, int(os.getenv("CODEMIND_MAX_CONSECUTIVE_NO_EVIDENCE", "3"))
)
_MAX_CONSECUTIVE_PARSE_FAILURES = max(
    1, int(os.getenv("CODEMIND_MAX_CONSECUTIVE_PARSE_FAILURES", "2"))
)
_MAX_FORCED_RECOVERY_STEPS = max(
    1, int(os.getenv("CODEMIND_MAX_FORCED_RECOVERY_STEPS", "3"))
)

# ── Code-from-prose extraction ────────────────────────────────────────────────

# Map language hints from code fences to default file names.
# Only includes actual source/code file types — data/markup formats (JSON, YAML,
# XML, Markdown) are excluded because the safety net should not auto-save them;
# those are typically the agent's analysis output, not files to persist.
_LANG_EXT_MAP = {
    "html": "index.html",
    "htm": "index.html",
    "python": "main.py",
    "py": "main.py",
    "javascript": "index.js",
    "js": "index.js",
    "typescript": "index.ts",
    "ts": "index.ts",
    "css": "styles.css",
    "java": "Main.java",
    "go": "main.go",
    "rust": "main.rs",
    "c": "main.c",
    "cpp": "main.cpp",
    "csharp": "Program.cs",
    "cs": "Program.cs",
    "ruby": "main.rb",
    "php": "index.php",
    "sql": "schema.sql",
    "sh": "script.sh",
    "bash": "script.sh",
    "shell": "script.sh",
}

# Languages that are data/markup formats, not source files.
# The prose-extraction safety net skips these — they are almost always the
# agent's final analysis output (e.g. a JSON audit report), not files to save.
# If the agent genuinely wants to persist JSON/YAML it should call write_file_system.
_DATA_ONLY_LANGS = frozenset({
    "json", "yaml", "yml", "xml", "text", "txt", "markdown", "md", "csv", "toml",
})

_CODE_BLOCK_RE = re.compile(
    r"```(\w*)\s*\n(.*?)```",
    re.DOTALL,
)


_INLINE_HTML_RE = re.compile(
    r"(<!DOCTYPE\s+html[^>]*>.*?</html>)",
    re.DOTALL | re.IGNORECASE,
)

_POTENTIAL_SECURITY_PATTERNS: dict[str, dict[str, object]] = {
    "sql_injection_like": {
        "severity_hint": "HIGH",
        "regexes": (
            r"select\s+.+\+",
            r"execute\s*\(.+\+",
            r"raw\s*sql",
        ),
    },
    "command_injection_like": {
        "severity_hint": "CRITICAL",
        "regexes": (
            r"os\.system\s*\(",
            r"subprocess\.(run|popen|call)\s*\(",
            r"shell\s*=\s*true",
        ),
    },
    "dynamic_code_execution": {
        "severity_hint": "CRITICAL",
        "regexes": (
            r"\beval\s*\(",
            r"\bexec\s*\(",
        ),
    },
    "ssrf_like_http_fetch": {
        "severity_hint": "HIGH",
        "regexes": (
            r"requests\.(get|post|request)\s*\(",
            r"httpx\.(get|post|request)\s*\(",
            r"urllib\.request\.urlopen\s*\(",
        ),
    },
    "xss_like_render_sink": {
        "severity_hint": "HIGH",
        "regexes": (
            r"dangerouslysetinnerhtml",
            r"\binnerhtml\s*=",
            r"\bv-html\b",
        ),
    },
    "hardcoded_secret_like": {
        "severity_hint": "CRITICAL",
        "regexes": (
            r"(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]+['\"]",
            r"-----begin\s+private\s+key-----",
        ),
    },
    "weak_crypto_or_tls": {
        "severity_hint": "HIGH",
        "regexes": (
            r"\bmd5\s*\(",
            r"\bsha1\s*\(",
            r"\bdes\b",
            r"\brc4\b",
            r"mode_ecb",
            r"verify\s*=\s*false",
        ),
    },
}


def _extract_text_from_tool_payload(payload: dict) -> str:
    chunks: list[str] = []
    for key in ("results", "content", "outline", "context"):
        val = payload.get(key)
        if isinstance(val, str):
            chunks.append(val)
        elif isinstance(val, list):
            for item in val[:40]:
                chunks.append(str(item))
        elif isinstance(val, dict):
            chunks.append(json.dumps(val, default=str))
    return "\n".join(chunks)[:200_000]


def _detect_potential_security_patterns(payload: dict, tool_name: str) -> list[dict]:
    """
    Identify potential security issue patterns from tool outputs.
    This is heuristic and intentionally labels findings as "potential".
    """
    text = _extract_text_from_tool_payload(payload)
    if not text.strip():
        return []
    low = text.lower()
    findings: list[dict] = []
    for pattern_id, spec in _POTENTIAL_SECURITY_PATTERNS.items():
        regexes = spec.get("regexes", ())
        matches: list[str] = []
        for rx in regexes:
            m = re.search(str(rx), low, flags=re.IGNORECASE)
            if m:
                matches.append(m.group(0))
        if matches:
            findings.append(
                {
                    "pattern_id": pattern_id,
                    "severity_hint": spec.get("severity_hint", "MEDIUM"),
                    "source_tool": tool_name,
                    "indicators": sorted(set(matches))[:4],
                }
            )
    return findings


def _extract_files_from_manifest_obj(obj: object) -> list[tuple[str, str]]:
    """
    Convert a JSON manifest object to (file_path, content) pairs.

    Supported shapes:
      - {"files": [{"path"|"file_path", "content"}]}
      - [{"path"|"file_path", "content"}]
    """
    entries: list[object] = []
    if isinstance(obj, dict):
        files_val = obj.get("files")
        if isinstance(files_val, list):
            entries = files_val
    elif isinstance(obj, list):
        entries = obj

    out: list[tuple[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("file_path") or item.get("path") or "").strip()
        content = item.get("content")
        if not file_path:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        out.append((file_path, content))
    return out


def _extract_file_manifests_from_messages(messages: list[BaseMessage]) -> list[tuple[str, str]]:
    """
    Extract persisted-file manifests from AI prose when tool calls are missing.

    This intentionally looks only for explicit file manifests with both path and
    content fields, so analysis JSON (e.g., vulnerabilities/report data) is not
    mistaken for writable source files.
    """
    results: list[tuple[str, str]] = []
    seen_keys: set[tuple[str, int]] = set()
    manifest_blocks = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)

    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        text = str(msg.content or "")
        if not text.strip():
            continue

        candidates: list[str] = []
        for m in manifest_blocks.finditer(text):
            block = (m.group(1) or "").strip()
            if block:
                candidates.append(block)
        candidates.append(text.strip())

        for raw in candidates:
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            pairs = _extract_files_from_manifest_obj(parsed)
            for file_path, content in pairs:
                key = (file_path, hash(content))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                results.append((file_path, content))
    return results


def _extract_code_blocks_from_messages(messages: list[BaseMessage]) -> list[tuple[str, str]]:
    """Scan AI messages for code blocks (fenced or inline) and return (filename, content) pairs.

    Handles:
    - Standard fenced code blocks (```lang ... ```)
    - Inline HTML documents (<!DOCTYPE html> ... </html>)
    - Code wrapped in model-specific thought tags (<|channel>thought ... <channel|>)
    """
    results: list[tuple[str, str]] = []
    seen_content: set[int] = set()

    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        text = str(msg.content or "")

        # Strip model-specific thought channel tags
        text_clean = re.sub(
            r"<\|channel\>thought.*?<channel\|>",
            lambda m: m.group(0),  # keep the inner text
            text,
            flags=re.DOTALL,
        )

        # Strategy 1: fenced code blocks — skip data/markup formats
        for m in _CODE_BLOCK_RE.finditer(text_clean):
            lang = (m.group(1) or "").strip().lower()
            code = m.group(2).strip()
            if not code or len(code) < 20:
                continue
            if lang in _DATA_ONLY_LANGS:
                continue  # analysis output, not a file to persist
            content_hash = hash(code)
            if content_hash in seen_content:
                continue
            seen_content.add(content_hash)
            filename = _LANG_EXT_MAP.get(lang, f"output.{lang}" if lang else "output.txt")
            existing_names = {fn for fn, _ in results}
            if filename in existing_names:
                base, ext = os.path.splitext(filename)
                for i in range(2, 20):
                    candidate = f"{base}_{i}{ext}"
                    if candidate not in existing_names:
                        filename = candidate
                        break
            results.append((filename, code))

        if results:
            continue  # prefer fenced blocks if found

        # Strategy 2: inline HTML documents without fences
        for m in _INLINE_HTML_RE.finditer(text_clean):
            html = m.group(1).strip()
            if len(html) < 50:
                continue
            content_hash = hash(html)
            if content_hash in seen_content:
                continue
            seen_content.add(content_hash)
            filename = "index.html"
            existing_names = {fn for fn, _ in results}
            if filename in existing_names:
                for i in range(2, 20):
                    candidate = f"page_{i}.html"
                    if candidate not in existing_names:
                        filename = candidate
                        break
            results.append((filename, html))

    return results




# ── Result contract ───────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Outcome of one ReAct run."""
    answer: str
    iterations: int
    tool_calls_made: int
    generated_files: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    quality_scorecards: list[dict] = field(default_factory=list)
    quality_summary: dict = field(default_factory=dict)
    potential_issue_patterns: list[dict] = field(default_factory=list)
    error: str | None = None


# ── ReActAgent ────────────────────────────────────────────────────────────────

class ReActAgent:
    """
    Stateless async ReAct loop.

    Callers are responsible for:
      - Providing *llm_with_tools* (a CmindChatModelWithTools already bound to a tool set).
      - Providing *tool_dispatcher* (a ToolDispatcher that can execute those tools).
      - Providing *compactor*  (a ContextCompactor to keep messages within the context window).
    """

    def __init__(
        self,
        *,
        llm_driver,          # raw LLMDriver — used for synthesis and compaction
        llm_with_tools,      # CmindChatModelWithTools bound to the tool set
        tool_dispatcher,     # ToolDispatcher instance
        compactor,           # ContextCompactor instance
        repo_id: str | list[str] | None = None,
        telemetry_context: dict | None = None,
    ) -> None:
        self.llm            = llm_driver
        self.llm_with_tools = llm_with_tools
        self.dispatcher     = tool_dispatcher
        self.compactor      = compactor
        # Normalise to single string for enforcement injection
        if isinstance(repo_id, list):
            self._repo_id = repo_id[0] if repo_id else None
        else:
            self._repo_id = repo_id
        tctx = telemetry_context if isinstance(telemetry_context, dict) else {}
        self._telemetry = AgentTelemetry(
            run_id=(tctx.get("run_id") or ""),
            component="react_agent",
            playbook=tctx.get("playbook"),
            repo_id=tctx.get("repo_id") or self._repo_id,
        )

    # ── internals ────────────────────────────────────────────────────────────

    def _think_tokens(self) -> int:
        cfg     = getattr(self.llm, "config", None)
        cfg_max = int(getattr(cfg, "max_tokens", 4096) or 4096)
        tokens  = max(_MIN_THINK_TOKENS, int(cfg_max * _THINK_FRACTION))
        return min(tokens, _MAX_THINK_TOKENS)

    def _write_trace(self, playbook_name: str, iteration: int, response: AIMessage) -> None:
        try:
            path = os.path.join(_TRACE_DIR, f"codemind_react_{playbook_name}.log")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"\n{'='*60}\nITERATION {iteration}\n{'='*60}\n")
                if response.content:
                    fh.write(f"CONTENT:\n{str(response.content)[:2000]}\n")
                if getattr(response, "tool_calls", None):
                    fh.write(
                        f"TOOL_CALLS:\n{json.dumps(response.tool_calls, indent=2, default=str)}\n"
                    )
        except Exception:
            pass  # trace is best-effort

    def _collect_evidence_stats(
        self,
        messages: list[BaseMessage],
        expected_critical_files: list[str] | None = None,
    ) -> dict[str, int | float]:
        """
        Build lightweight evidence-coverage stats from executed tool messages.
        Used by final-state gating to prevent shallow completion.
        """
        structural_tool_names = {
            "get_map",
            "trace_path",
            "get_callers",
            "get_callees",
            "get_dependencies",
            "search_symbol",
            "get_file_outline",
        }
        lexical_tool_names = {
            "search_code",
            "grep_search",
            "list_files",
            "list_repo_directory",
        }
        read_tool_names = {"read_file", "read_file_system"}
        unique_read_files: set[str] = set()
        structural_calls = 0
        lexical_calls = 0
        evidence_messages = 0
        expected = {str(p).strip() for p in (expected_critical_files or []) if str(p).strip()}

        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue
            name = str(getattr(msg, "name", "") or "")
            if name in structural_tool_names:
                structural_calls += 1
            if name in lexical_tool_names:
                lexical_calls += 1
            try:
                payload = json.loads(str(msg.content or "{}"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if name in read_tool_names and payload.get("success"):
                fp = str(payload.get("file_path") or "").strip()
                if fp:
                    unique_read_files.add(fp)
            meta = payload.get("_meta")
            if isinstance(meta, dict) and float(meta.get("evidence_score", 0.0) or 0.0) > 0:
                evidence_messages += 1
            elif payload.get("success") and any(
                payload.get(k) for k in ("results", "content", "files", "top_nodes", "entry_points")
            ):
                evidence_messages += 1

        return {
            "unique_read_files": len(unique_read_files),
            "read_files": sorted(unique_read_files),
            "structural_calls": structural_calls,
            "lexical_calls": lexical_calls,
            "evidence_messages": evidence_messages,
            "critical_candidates_total": len(expected),
            "critical_candidates_read": len(unique_read_files & expected) if expected else 0,
            "critical_coverage_ratio": (
                float(len(unique_read_files & expected)) / float(len(expected))
                if expected
                else 1.0
            ),
        }

    def _collect_evidence_files(self, messages: list[BaseMessage]) -> set[str]:
        """
        Best-effort set of file paths observed in tool outputs.
        Used by grounding checks (shadow/enforced via feature flags).
        """
        files: set[str] = set()
        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue
            try:
                payload = json.loads(str(msg.content or "{}"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                continue
            for key in ("file_path", "absolute_path"):
                v = str(payload.get(key) or "").strip()
                if v:
                    files.add(v)
            for k in ("files", "results", "symbols", "callers", "callees", "dependencies"):
                val = payload.get(k)
                if isinstance(val, list):
                    for item in val[:200]:
                        if isinstance(item, dict):
                            fp = str(item.get("file_path") or item.get("path") or "").strip()
                            if fp:
                                files.add(fp)
                        elif isinstance(item, str):
                            s = item.strip()
                            if s and "/" in s and "." in s:
                                files.add(s)
        return files

    def _emit_tool_telemetry_round(
        self,
        *,
        iteration: int,
        source: str,
        tool_calls: list[Any],
        tool_messages: list[Any],
        no_evidence_streak: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Emit ``tool_dispatch`` + ``tool_results`` with scrubbed args and result previews."""
        if not tool_calls:
            return
        names: list[str | None] = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                names.append(tc.get("name"))
            else:
                names.append(getattr(tc, "name", None))
        self._telemetry.emit(
            "tool_dispatch",
            iteration=int(iteration),
            source=str(source)[:64],
            tool_calls_this_turn=int(len(tool_calls)),
            tool_call_names=[n for n in names if n],
            tool_calls_detail=tool_calls_detail_for_telemetry(tool_calls, telemetry=self._telemetry),
            **({"context": extra} if extra else {}),
        )
        self._telemetry.emit(
            "tool_results",
            iteration=int(iteration),
            source=str(source)[:64],
            outcomes=tool_result_summaries_for_telemetry(tool_messages, telemetry=self._telemetry),
            no_evidence_streak=int(no_evidence_streak),
        )

    # ── synthesis ─────────────────────────────────────────────────────────────

    async def _synthesize(
        self,
        messages: list[BaseMessage],
        goal: str,
        playbook_name: str,
        reason: str = "max_iterations",
    ) -> str:
        parts = [
            str(m.content)[:20_000]
            for m in messages
            if isinstance(m, ToolMessage) and m.content
        ]
        bundle = "\n\n---\n\n".join(parts[-16:])
        if not bundle.strip():
            if reason == "empty_response":
                return (
                    "The agent produced no output and called no tools. "
                    "This usually means the model could not parse the tool schemas or "
                    "the prompt was too large.  Try a narrower goal or a different playbook."
                )
            if reason == "insufficient_evidence":
                return (
                    "The agent ran multiple tool strategies but failed to collect enough evidence. "
                    "Try narrowing the goal or verifying repository/index coverage."
                )
            return (
                "The agent reached its iteration ceiling without calling any tools. "
                "This is unexpected — the agent should stop naturally once it has enough data. "
                "Check the trace log for LLM errors, or try a more specific goal."
            )

        cfg       = getattr(self.llm, "config", None)
        max_tok   = min(
            _SYNTH_MAX_TOKENS,
            int(getattr(cfg, "max_tokens", 4096) or 4096),
        )
        prompt = (
            f"GOAL:\n{goal}\n\n"
            f"COLLECTED TOOL DATA (playbook={playbook_name}):\n{bundle}\n\n"
            "Write the final analysis: key findings, relevant files and symbols, "
            "confidence level, and any remaining uncertainties. "
            "Use only the data above — do not invent."
        )
        try:
            return await self.llm.generate(
                prompt,
                system_prompt=(
                    "You are a senior software engineer. "
                    "Synthesise the tool results into a clear, actionable answer. "
                    "Use headings and bullet points where helpful."
                ),
                max_tokens=max_tok,
            )
        except Exception as exc:
            return (
                f"[Synthesis error: {exc}]\n\n"
                f"--- Tool excerpts (truncated) ---\n{bundle[:12_000]}"
            )

    # ── main entry point ──────────────────────────────────────────────────────

    async def run(
        self,
        *,
        goal: str,
        system_prompt: str,
        prefetch_block: str = "",
        max_iterations: int = _MAX_ITER_DEFAULT,
        playbook_name: str = "react",
        output_type: str = "",
        output_schema_model: object | None = None,
        expected_critical_files: list[str] | None = None,
    ) -> AgentResult:
        """
        Run the ReAct loop to completion.

        The agent autonomously decides which tools to call.  There is no
        static classification of playbooks — the playbook's own system prompt
        plus the user goal guide the agent's behaviour.
        """
        logs: list[str] = [
            f"ReAct start | playbook={playbook_name} | max_iter={max_iterations}"
        ]
        messages: list[BaseMessage] = [HumanMessage(content=goal)]
        tool_calls_made = 0
        generated_files: list[str] = []
        potential_issue_patterns: dict[str, dict] = {}
        finalization_retries = 0
        quality_scorecards: list[dict] = []
        cumulative_evidence_score = 0.0
        orchestration = OrchestrationController(
            repo_id=self._repo_id,
            dispatcher=self.dispatcher,
            max_consecutive_no_evidence=_MAX_CONSECUTIVE_NO_EVIDENCE,
            max_consecutive_parse_failures=_MAX_CONSECUTIVE_PARSE_FAILURES,
            max_forced_recovery_steps=_MAX_FORCED_RECOVERY_STEPS,
        )
        self._telemetry.emit(
            "run_start",
            playbook_name=playbook_name,
            max_iterations=max_iterations,
            goal_preview=(goal or "")[:500],
            repo_id=self._repo_id,
            output_type=output_type,
            expected_critical_files_count=len(expected_critical_files or []),
        )

        def _append_quality_scorecard(
            *,
            iteration_idx: int,
            response_text: str = "",
            tool_calls_this_turn: int = 0,
            turn_outcomes: list[dict] | None = None,
            stage: str = "continue",
            final_gate_reason: str = "",
        ) -> None:
            nonlocal cumulative_evidence_score
            outcomes = turn_outcomes or []
            turn_evidence = sum(
                float(o.get("evidence_score", 0.0) or 0.0) for o in outcomes
            )
            cumulative_evidence_score += turn_evidence
            parse_penalty = orchestration.parse_failure_streak * 8
            no_evidence_penalty = orchestration.no_evidence_streak * 10
            planning_penalty = 12 if should_force_continuation(response_text or "", tool_calls_made) else 0
            base = (
                40
                + min(35, int(turn_evidence * 12))
                + min(15, tool_calls_this_turn * 5)
                - parse_penalty
                - no_evidence_penalty
                - planning_penalty
            )
            quality_score = max(0, min(100, int(base)))
            card = {
                "iteration": int(iteration_idx),
                "stage": stage,
                "tool_calls_this_turn": int(tool_calls_this_turn),
                "turn_evidence_score": round(turn_evidence, 3),
                "cumulative_evidence_score": round(cumulative_evidence_score, 3),
                "parse_failure_streak": int(orchestration.parse_failure_streak),
                "no_evidence_streak": int(orchestration.no_evidence_streak),
                "planning_or_giveup_detected": bool(
                    should_force_continuation(response_text or "", tool_calls_made)
                ),
                "final_gate_reason": final_gate_reason or "",
                "quality_score": quality_score,
            }
            quality_scorecards.append(card)
            self._telemetry.emit("quality_scorecard", **card)

        def _quality_summary() -> dict:
            if not quality_scorecards:
                return {
                    "iterations_scored": 0,
                    "avg_quality_score": 0.0,
                    "final_quality_score": 0.0,
                    "cumulative_evidence_score": round(cumulative_evidence_score, 3),
                    "finalization_retries": int(finalization_retries),
                }
            avg_quality = sum(float(s.get("quality_score", 0.0)) for s in quality_scorecards) / len(quality_scorecards)
            return {
                "iterations_scored": len(quality_scorecards),
                "avg_quality_score": round(avg_quality, 2),
                "final_quality_score": float(quality_scorecards[-1].get("quality_score", 0.0)),
                "cumulative_evidence_score": round(cumulative_evidence_score, 3),
                "finalization_retries": int(finalization_retries),
            }

        def _result(*, answer: str, iterations: int, error: str | None = None) -> AgentResult:
            self._telemetry.emit(
                "run_complete",
                iterations=int(iterations),
                tool_calls_made=int(tool_calls_made),
                generated_files_count=len(generated_files),
                quality_summary=_quality_summary(),
                error=error or "",
                answer_preview=(answer or "")[:800],
            )
            return AgentResult(
                answer=answer,
                iterations=iterations,
                tool_calls_made=tool_calls_made,
                generated_files=generated_files,
                logs=logs,
                quality_scorecards=quality_scorecards,
                quality_summary=_quality_summary(),
                potential_issue_patterns=list(potential_issue_patterns.values()),
                error=error,
            )

        for iteration in range(max_iterations + 1):

            # ── compact context if growing too large ───────────────────────
            messages = await self.compactor.compact(messages)
            self._telemetry.emit(
                "iteration_start",
                iteration=int(iteration),
                message_count=len(messages),
                tool_calls_made=int(tool_calls_made),
                finalization_retries=int(finalization_retries),
            )

            # ── build per-turn system prompt ───────────────────────────────
            if iteration < 2:
                local_sys = system_prompt + _AGENT_DIRECTIVE
            else:
                local_sys = system_prompt + (
                    "\n\n### REMINDER\n"
                    "Continue working. Call tools as needed, or provide your final answer."
                )
            if iteration == 0 and prefetch_block:
                local_sys += prefetch_block
            if self._repo_id:
                local_sys += (
                    f"\n\n### REPO CONTEXT\n"
                    f"repo_id='{self._repo_id}' — pass this to repo-scoped tool calls."
                )

            full_messages = [SystemMessage(content=local_sys)] + messages

            # ── max iterations: synthesise from collected evidence ─────────
            if iteration >= max_iterations:
                logs.append(f"Max iterations ({max_iterations}) reached — synthesising")
                answer = await self._synthesize(messages, goal, playbook_name, reason="max_iterations")
                _append_quality_scorecard(
                    iteration_idx=iteration,
                    response_text=answer,
                    stage="max_iterations_synthesis",
                    final_gate_reason="max_iterations",
                )
                return _result(answer=answer, iterations=iteration)

            # ── call LLM ──────────────────────────────────────────────────
            try:
                response: AIMessage = await self.llm_with_tools.ainvoke(
                    full_messages,
                    max_tokens=self._think_tokens(),
                    temperature=0.1,
                )
            except Exception as exc:
                logger.error("LLM error at iteration %d: %s", iteration, exc)
                logs.append(f"LLM error at iteration {iteration}: {exc}")
                _append_quality_scorecard(
                    iteration_idx=iteration,
                    stage="llm_error",
                    final_gate_reason="llm_error",
                )
                return _result(answer="", iterations=iteration, error=str(exc))

            self._write_trace(playbook_name, iteration, response)

            tc_from_wrapper = getattr(response, "tool_calls", None) or []
            content_len = len(str(response.content or ""))
            self._telemetry.emit(
                "llm_response",
                iteration=int(iteration),
                content_chars=int(content_len),
                tool_calls_from_wrapper=len(tc_from_wrapper),
                tool_call_names=[tc.get("name") for tc in tc_from_wrapper],
            )
            print(
                f"[REACT] iter={iteration} | content_chars={content_len} | "
                f"tool_calls_from_wrapper={len(tc_from_wrapper)} | "
                f"repo_id={self._repo_id}"
            )
            if tc_from_wrapper:
                for tc in tc_from_wrapper:
                    print(f"[REACT]   tool_call: {tc.get('name')}")

            # ── repair plain-text tool plans ──────────────────────────────
            has_calls = bool(getattr(response, "tool_calls", None))

            if not has_calls and response.content:
                repaired = self.dispatcher.repair_tool_calls(str(response.content))
                if repaired:
                    response   = AIMessage(content="", tool_calls=repaired)
                    has_calls  = True
                    self._telemetry.emit(
                        "tool_call_repair",
                        iteration=int(iteration),
                        repaired_count=len(repaired),
                        repaired_names=[tc.get("name") for tc in repaired],
                        tool_calls_detail=tool_calls_detail_for_telemetry(
                            repaired, telemetry=self._telemetry
                        ),
                    )
                    orchestration.record_parse_attempt(
                        repaired=True, looks_like_tool_json=False
                    )
                    logs.append(
                        f"  Iter {iteration}: repaired {len(repaired)} tool call(s) from text"
                    )
                else:
                    txt = str(response.content or "")
                    looks_like_tool_json = bool(
                        re.search(
                            r'"(tool_calls|tool_name|name|tool|action|params|args|arguments|parameters)"',
                            txt,
                            flags=re.IGNORECASE,
                        )
                    )
                    orchestration.record_parse_attempt(
                        repaired=False, looks_like_tool_json=looks_like_tool_json
                    )
                    if looks_like_tool_json:
                        self._telemetry.emit(
                            "tool_call_repair_failed",
                            iteration=int(iteration),
                            parse_failure_streak=int(orchestration.parse_failure_streak),
                        )
                        logs.append(
                            f"  Iter {iteration}: tool-call parsing failed "
                            f"(streak={orchestration.parse_failure_streak})"
                        )

            # ── no tool calls → natural finish (with safety-net write) ────
            if not has_calls:
                if response.content:
                    messages.append(AIMessage(content=str(response.content)))

                decision = orchestration.decide_no_tool_iteration(
                    messages=messages,
                    iteration=iteration,
                    tool_calls_made=tool_calls_made,
                    response_text=str(response.content or ""),
                    min_repo_tool_calls=MIN_REPO_TOOL_CALLS,
                    should_force_continuation=should_force_continuation,
                )
                if decision.action == NextAction.FORCE_TOOL and decision.tool_call:
                    forced_name = decision.tool_call.get("name", "unknown")
                    logs.append(
                        f"  Iter {iteration}: orchestration forced recovery tool '{forced_name}' "
                        f"(step {orchestration.forced_recovery_steps}/{_MAX_FORCED_RECOVERY_STEPS})"
                    )
                    forced_ai_msg = AIMessage(content="", tool_calls=[decision.tool_call])
                    messages.append(forced_ai_msg)
                    tool_messages = await self.dispatcher.dispatch([decision.tool_call])
                    messages.extend(tool_messages)
                    tool_calls_made += 1
                    self._emit_tool_telemetry_round(
                        iteration=iteration,
                        source="forced_no_tool",
                        tool_calls=[decision.tool_call],
                        tool_messages=tool_messages,
                        no_evidence_streak=orchestration.no_evidence_streak,
                        extra={
                            "forced_recovery_step": int(orchestration.forced_recovery_steps),
                            "max_forced_recovery_steps": _MAX_FORCED_RECOVERY_STEPS,
                        },
                    )
                    if decision.prompt:
                        messages.append(HumanMessage(content=decision.prompt))
                    _append_quality_scorecard(
                        iteration_idx=iteration,
                        tool_calls_this_turn=1,
                        turn_outcomes=[
                            orchestration.tool_outcome_meta(tm)
                            for tm in tool_messages
                            if isinstance(tm, ToolMessage)
                        ],
                        stage="forced_recovery_no_tool_turn",
                    )
                    continue
                if decision.action == NextAction.CONTINUE_PROMPT and decision.prompt:
                    logs.append(
                        f"  Iter {iteration}: orchestration requested continuation prompt"
                    )
                    messages.append(HumanMessage(content=decision.prompt))
                    _append_quality_scorecard(
                        iteration_idx=iteration,
                        response_text=str(response.content or ""),
                        stage="continue_prompt_no_tool_turn",
                        final_gate_reason="orchestration_continue_prompt",
                    )
                    continue
                if decision.action == NextAction.SYNTHESIZE and decision.synth_reason:
                    logs.append(
                        f"  Iter {iteration}: evidence circuit exhausted "
                        f"(no-evidence streak={orchestration.no_evidence_streak}, "
                        f"forced_recovery_steps={orchestration.forced_recovery_steps}) — synthesising"
                    )
                    answer = await self._synthesize(
                        messages, goal, playbook_name, reason=decision.synth_reason
                    )
                    evidence_stats = self._collect_evidence_stats(
                        messages, expected_critical_files
                    )
                    synth_gate = evaluate_final_state(
                        response_text=answer,
                        repo_id=self._repo_id,
                        tool_calls_made=tool_calls_made,
                        has_tool_history=orchestration.has_tool_history(messages),
                        output_type=output_type,
                        output_schema_model=output_schema_model,
                        evidence_stats=evidence_stats,
                    )
                    if not synth_gate.is_final and finalization_retries < _MAX_FINALIZATION_RETRIES:
                        finalization_retries += 1
                        logs.append(
                            f"  Iter {iteration}: synthesized output rejected by final-state gate "
                            f"({synth_gate.reason}); retry {finalization_retries}/{_MAX_FINALIZATION_RETRIES}"
                        )
                        messages.append(
                            HumanMessage(
                                content=synth_gate.continue_prompt
                                or "Synthesis result is still intermediate. Continue tool-driven work."
                            )
                        )
                        _append_quality_scorecard(
                            iteration_idx=iteration,
                            response_text=answer,
                            stage="synthesis_rejected_by_final_gate",
                            final_gate_reason=synth_gate.reason,
                        )
                        continue
                    quality_guard = evaluate_quality_guard(
                        answer=answer,
                        output_type=output_type,
                        evidence_files=self._collect_evidence_files(messages),
                        read_files=set(evidence_stats.get("read_files", []) or []),
                        expected_critical_files=expected_critical_files,
                        enable_verifier=_ENABLE_VERIFIER_PASS,
                        enable_grounding=_ENABLE_GROUNDING_GATE,
                        enable_risk_coverage=_ENABLE_RISK_WEIGHTED_COVERAGE,
                        enforce=_QUALITY_GUARD_ENFORCE,
                    )
                    if quality_guard.reasons:
                        logs.append(
                            "  Iter "
                            f"{iteration}: quality-guard ({quality_guard.mode}) "
                            f"reasons={quality_guard.reasons}"
                        )
                    if not quality_guard.passed and finalization_retries < _MAX_FINALIZATION_RETRIES:
                        finalization_retries += 1
                        messages.append(
                            HumanMessage(
                                content=(
                                    "Quality guard blocked finalization due to: "
                                    + ", ".join(quality_guard.reasons)
                                    + ". Continue gathering targeted evidence."
                                )
                            )
                        )
                        _append_quality_scorecard(
                            iteration_idx=iteration,
                            response_text=answer,
                            stage="quality_guard_rejected",
                            final_gate_reason="quality_guard_rejected",
                        )
                        continue
                    _append_quality_scorecard(
                        iteration_idx=iteration,
                        response_text=answer,
                        stage="insufficient_evidence_synthesis",
                        final_gate_reason=decision.synth_reason,
                    )
                    return _result(answer=answer, iterations=iteration)

                # Safety net: if the model produced code as prose but never
                # called write_file_system, try to persist it.  This is a
                # model-limitation workaround, not playbook-specific.
                if not orchestration.has_tool_history(messages):
                    manifest_files = _extract_file_manifests_from_messages(messages)
                    if manifest_files:
                        logs.append(
                            f"  Iter {iteration}: extracted {len(manifest_files)} file(s) "
                            "from JSON manifest — auto-persisting via write_file_system"
                        )
                        synthetic_calls = []
                        for fname, content in manifest_files:
                            call_id = f"auto_write_manifest_{fname.replace('/', '_')}_{iteration}"
                            synthetic_calls.append({
                                "name": "write_file_system",
                                "args": {"file_path": fname, "content": content},
                                "id": call_id,
                                "type": "tool_call",
                            })
                        synth_msg = AIMessage(
                            content="Auto-persisting manifest files.",
                            tool_calls=synthetic_calls,
                        )
                        messages.append(synth_msg)
                        tool_messages = await self.dispatcher.dispatch(synthetic_calls)
                        messages.extend(tool_messages)
                        tool_calls_made += len(synthetic_calls)
                        self._emit_tool_telemetry_round(
                            iteration=iteration,
                            source="synthetic_manifest",
                            tool_calls=synthetic_calls,
                            tool_messages=tool_messages,
                            no_evidence_streak=orchestration.no_evidence_streak,
                            extra={"manifest_file_count": len(manifest_files)},
                        )
                        for tm in tool_messages:
                            try:
                                payload = json.loads(str(tm.content or "{}"))
                                fp = payload.get("file_path")
                                if fp and payload.get("success") and payload.get("bytes_written"):
                                    generated_files.append(str(fp))
                            except Exception:
                                continue
                        file_list = ", ".join(fn for fn, _ in manifest_files)
                        answer = (
                            f"Generated and saved {len(manifest_files)} file(s): {file_list}\n\n"
                            + str(response.content or "")
                        )
                        logs.append(f"ReAct finished with auto-manifest writes at iteration {iteration}")
                        _append_quality_scorecard(
                            iteration_idx=iteration,
                            response_text=answer,
                            tool_calls_this_turn=len(synthetic_calls),
                            stage="auto_manifest_write_final",
                        )
                        return _result(answer=answer, iterations=iteration)

                    code_blocks = _extract_code_blocks_from_messages(messages)
                    if code_blocks:
                        logs.append(
                            f"  Iter {iteration}: extracted {len(code_blocks)} code block(s) "
                            f"from prose — auto-persisting via write_file_system"
                        )
                        synthetic_calls = []
                        for fname, content in code_blocks:
                            call_id = f"auto_write_{fname.replace('/', '_')}_{iteration}"
                            synthetic_calls.append({
                                "name": "write_file_system",
                                "args": {"file_path": fname, "content": content},
                                "id": call_id,
                                "type": "tool_call",
                            })
                        synth_msg = AIMessage(
                            content="Auto-persisting extracted code blocks.",
                            tool_calls=synthetic_calls,
                        )
                        messages.append(synth_msg)
                        tool_messages = await self.dispatcher.dispatch(synthetic_calls)
                        messages.extend(tool_messages)
                        tool_calls_made += len(synthetic_calls)
                        self._emit_tool_telemetry_round(
                            iteration=iteration,
                            source="synthetic_codeblock",
                            tool_calls=synthetic_calls,
                            tool_messages=tool_messages,
                            no_evidence_streak=orchestration.no_evidence_streak,
                            extra={"code_block_count": len(code_blocks)},
                        )
                        for tm in tool_messages:
                            try:
                                payload = json.loads(str(tm.content or "{}"))
                                fp = payload.get("file_path")
                                if fp and payload.get("success") and payload.get("bytes_written"):
                                    generated_files.append(str(fp))
                            except Exception:
                                continue
                        file_list = ", ".join(fn for fn, _ in code_blocks)
                        answer = (
                            f"Generated and saved {len(code_blocks)} file(s): {file_list}\n\n"
                            + str(response.content or "")
                        )
                        logs.append(f"ReAct finished with auto-extracted writes at iteration {iteration}")
                        _append_quality_scorecard(
                            iteration_idx=iteration,
                            response_text=answer,
                            tool_calls_this_turn=len(synthetic_calls),
                            stage="auto_codeblock_write_final",
                        )
                        return _result(answer=answer, iterations=iteration)

                # Normal finish — either the agent completed its analysis or
                # there genuinely was nothing to do.
                answer = str(response.content or "")
                if not answer.strip():
                    logs.append(
                        f"  Iter {iteration}: empty response — synthesising from tool data"
                    )
                    answer = await self._synthesize(messages, goal, playbook_name, reason="empty_response")
                evidence_stats = self._collect_evidence_stats(
                    messages, expected_critical_files
                )
                decision = evaluate_final_state(
                    response_text=answer,
                    repo_id=self._repo_id,
                    tool_calls_made=tool_calls_made,
                    has_tool_history=orchestration.has_tool_history(messages),
                    output_type=output_type,
                    output_schema_model=output_schema_model,
                    evidence_stats=evidence_stats,
                )
                if not decision.is_final and finalization_retries < _MAX_FINALIZATION_RETRIES:
                    finalization_retries += 1
                    logs.append(
                        f"  Iter {iteration}: final-state gate rejected response "
                        f"({decision.reason}); retry {finalization_retries}/{_MAX_FINALIZATION_RETRIES}"
                    )
                    messages.append(
                        HumanMessage(
                            content=decision.continue_prompt
                            or "Response is not final yet. Continue the task."
                        )
                    )
                    _append_quality_scorecard(
                        iteration_idx=iteration,
                        response_text=answer,
                        stage="final_state_rejected",
                        final_gate_reason=decision.reason,
                    )
                    continue
                quality_guard = evaluate_quality_guard(
                    answer=answer,
                    output_type=output_type,
                    evidence_files=self._collect_evidence_files(messages),
                    read_files=set(evidence_stats.get("read_files", []) or []),
                    expected_critical_files=expected_critical_files,
                    enable_verifier=_ENABLE_VERIFIER_PASS,
                    enable_grounding=_ENABLE_GROUNDING_GATE,
                    enable_risk_coverage=_ENABLE_RISK_WEIGHTED_COVERAGE,
                    enforce=_QUALITY_GUARD_ENFORCE,
                )
                if quality_guard.reasons:
                    logs.append(
                        "  Iter "
                        f"{iteration}: quality-guard ({quality_guard.mode}) "
                        f"reasons={quality_guard.reasons}"
                    )
                if not quality_guard.passed and finalization_retries < _MAX_FINALIZATION_RETRIES:
                    finalization_retries += 1
                    messages.append(
                        HumanMessage(
                            content=(
                                "Quality guard blocked finalization due to: "
                                + ", ".join(quality_guard.reasons)
                                + ". Continue gathering targeted evidence."
                            )
                        )
                    )
                    _append_quality_scorecard(
                        iteration_idx=iteration,
                        response_text=answer,
                        stage="quality_guard_rejected",
                        final_gate_reason="quality_guard_rejected",
                    )
                    continue
                if decision.is_final:
                    _append_quality_scorecard(
                        iteration_idx=iteration,
                        response_text=answer,
                        stage="final_state_accepted",
                    )
                logs.append(f"ReAct finished naturally at iteration {iteration}")
                return _result(answer=answer, iterations=iteration)

            # ── dispatch tool calls ───────────────────────────────────────
            messages.append(response)
            call_count  = len(response.tool_calls)
            call_names  = [tc.get("name") for tc in response.tool_calls]
            logs.append(
                f"  Iter {iteration}: dispatching {call_count} tool(s) {call_names}"
            )

            tool_messages = await self.dispatcher.dispatch(response.tool_calls)
            messages.extend(tool_messages)
            tool_calls_made += call_count
            self._emit_tool_telemetry_round(
                iteration=iteration,
                source="model",
                tool_calls=list(response.tool_calls),
                tool_messages=tool_messages,
                no_evidence_streak=orchestration.no_evidence_streak,
            )
            turn_outcomes: list[dict] = []
            for tm in tool_messages:
                try:
                    raw = str(tm.content or "{}")
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        continue
                    meta = payload.get("_meta")
                    if isinstance(meta, dict):
                        turn_outcomes.append(meta)
                    fp = payload.get("file_path")
                    is_write = (
                        getattr(tm, "name", "") == "write_file_system"
                        or (fp and payload.get("success") and payload.get("bytes_written"))
                    )
                    if is_write and fp:
                        generated_files.append(str(fp))
                        print(f"[REACT] ✅ File written: {fp}")
                    detected = _detect_potential_security_patterns(
                        payload, str(getattr(tm, "name", "") or "")
                    )
                    for item in detected:
                        key = str(item.get("pattern_id") or "")
                        if not key:
                            continue
                        if key in potential_issue_patterns:
                            existing = potential_issue_patterns[key]
                            prev = set(existing.get("indicators", []) or [])
                            new = set(item.get("indicators", []) or [])
                            existing["indicators"] = sorted(prev | new)[:6]
                            src = set(existing.get("source_tools", []) or [])
                            src.add(str(item.get("source_tool") or ""))
                            existing["source_tools"] = sorted(s for s in src if s)
                        else:
                            potential_issue_patterns[key] = {
                                "pattern_id": key,
                                "severity_hint": item.get("severity_hint", "MEDIUM"),
                                "indicators": list(item.get("indicators", []) or []),
                                "source_tools": [str(item.get("source_tool") or "")],
                                "confidence": "potential",
                            }
                except Exception:
                    continue

            decision = orchestration.decide_after_tool_turn(
                messages=messages,
                iteration=iteration,
                turn_outcomes=turn_outcomes,
            )
            if turn_outcomes and not orchestration.outcomes_have_evidence(turn_outcomes):
                logs.append(
                    f"  Iter {iteration}: no evidence from tool outcomes "
                    f"(streak={orchestration.no_evidence_streak})"
                )
            if decision.action == NextAction.FORCE_TOOL and decision.tool_call:
                forced_name = decision.tool_call.get("name", "unknown")
                logs.append(
                    f"  Iter {iteration}: no-evidence circuit breaker triggered — "
                    f"auto-dispatching recovery tool '{forced_name}' "
                    f"(step {orchestration.forced_recovery_steps}/{_MAX_FORCED_RECOVERY_STEPS})"
                )
                forced_ai_msg = AIMessage(content="", tool_calls=[decision.tool_call])
                messages.append(forced_ai_msg)
                extra_tool_messages = await self.dispatcher.dispatch([decision.tool_call])
                messages.extend(extra_tool_messages)
                tool_calls_made += 1
                self._emit_tool_telemetry_round(
                    iteration=iteration,
                    source="forced_after_tool",
                    tool_calls=[decision.tool_call],
                    tool_messages=extra_tool_messages,
                    no_evidence_streak=orchestration.no_evidence_streak,
                    extra={
                        "forced_recovery_step": int(orchestration.forced_recovery_steps),
                        "max_forced_recovery_steps": _MAX_FORCED_RECOVERY_STEPS,
                        "prior_tool_calls_this_turn": int(call_count),
                    },
                )
                _append_quality_scorecard(
                    iteration_idx=iteration,
                    tool_calls_this_turn=call_count + 1,
                    turn_outcomes=turn_outcomes,
                    stage="forced_recovery_after_tool_turn",
                )
                continue

            _append_quality_scorecard(
                iteration_idx=iteration,
                response_text=str(response.content or ""),
                tool_calls_this_turn=call_count,
                turn_outcomes=turn_outcomes,
                stage="tool_turn_complete",
            )

        # Unreachable in practice (handled above), but kept as safety net
        answer = await self._synthesize(messages, goal, playbook_name, reason="max_iterations")
        _append_quality_scorecard(
            iteration_idx=max_iterations,
            response_text=answer,
            stage="fallback_synthesis",
            final_gate_reason="loop_fallback",
        )
        return _result(answer=answer, iterations=max_iterations)
