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

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_MAX_ITER_DEFAULT = int(os.getenv("CODEMIND_REACT_MAX_ITERATIONS", "12"))
_THINK_FRACTION   = float(os.getenv("CODEMIND_THINK_FRACTION", "0.25"))
_MAX_THINK_TOKENS = int(os.getenv("CODEMIND_MAX_THINK_TOKENS", "4096"))
_MIN_THINK_TOKENS = int(os.getenv("CODEMIND_MIN_THINK_TOKENS", "512"))
_SYNTH_MAX_TOKENS = int(os.getenv("CODEMIND_SYNTH_MAX_TOKENS", "8192"))
_TRACE_DIR        = os.getenv("CODEMIND_TRACE_DIR", "/tmp")

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

# Minimum tool calls before we accept a prose response as a final conclusion
# for repo-scoped analysis tasks.  Set via env var to allow tuning.
_MIN_REPO_TOOL_CALLS = int(os.getenv("CODEMIND_MIN_REPO_TOOL_CALLS", "3"))

# Phrases that indicate the LLM is writing a planning/transition paragraph
# rather than a final conclusion.  If we see these in a prose-only response
# after very few tool calls, we force the agent to keep executing.
_PLANNING_PHRASES = (
    "next phase",
    "next step",
    "next, i will",
    "next i will",
    "will now",
    "will explore",
    "will search",
    "will investigate",
    "will read",
    "will proceed",
    "will focus",
    "will examine",
    "phase 2",
    "phase 3",
    "phase two",
    "phase three",
    "plan to",
    "plan is to",
    "my plan",
    "continue by",
    "proceed to",
    "the following steps",
    "i'll now",
    "i'll proceed",
    "i'll explore",
    "initial step",
    "initial analysis",
    "has been initiated",
    "has been executed",
    "next steps",
    "moving on to",
    "in the next iteration",
    "the next phase",
    "the next step",
    "involves deep",
    "involves reading",
    "involves exploring",
    "focuses on",
    "phase involves",
)

# Phrases that indicate the LLM gave up after one or two failing tool calls
# instead of trying alternative approaches.  When tool_calls_made is very low
# we treat these the same as planning phrases — the agent must keep going.
_GIVING_UP_PHRASES = (
    "not possible to identify",
    "it is not possible",
    "cannot identify",
    "no results were returned",
    "no results",
    "no file content",
    "no endpoints",
    "no api",
    "could not find",
    "unable to find",
    "unable to determine",
    "cannot determine",
    "no data was",
    "not enough information",
    "insufficient data",
    "insufficient information",
    "without access",
    "no output was",
    "nothing was returned",
    "returned no",
    "did not return",
    "does not contain",
    "no matching",
    "could not be identified",
    "cannot be identified",
    "cannot be determined",
)


def _looks_like_planning(text: str) -> bool:
    """Return True if *text* reads as an interim plan rather than a final conclusion."""
    t = text.lower()
    return any(phrase in t for phrase in _PLANNING_PHRASES)


def _should_force_continuation(text: str, tool_calls_made: int) -> bool:
    """Return True if the agent should be forced to keep exploring.

    Two situations warrant a retry:
    1. Planning prose — the agent described future work instead of doing it.
    2. Premature give-up — the agent tried one tool, got no results, and stopped.
       Only applies when very few tools have been called (< 2), because a genuine
       conclusion after thorough exploration is fine.
    """
    if _looks_like_planning(text):
        return True
    if tool_calls_made < 2:
        t = text.lower()
        return any(phrase in t for phrase in _GIVING_UP_PHRASES)
    return False

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

    # ── internals ────────────────────────────────────────────────────────────

    def _think_tokens(self) -> int:
        cfg     = getattr(self.llm, "config", None)
        cfg_max = int(getattr(cfg, "max_tokens", 4096) or 4096)
        tokens  = max(_MIN_THINK_TOKENS, int(cfg_max * _THINK_FRACTION))
        return min(tokens, _MAX_THINK_TOKENS)

    @staticmethod
    def _has_tool_history(messages: list[BaseMessage]) -> bool:
        return any(isinstance(m, ToolMessage) for m in messages)

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
        repo_exploration_retry_used = False
        shallow_analysis_retry_used = False

        for iteration in range(max_iterations + 1):

            # ── compact context if growing too large ───────────────────────
            messages = await self.compactor.compact(messages)

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
                return AgentResult(
                    answer=answer,
                    iterations=iteration,
                    tool_calls_made=tool_calls_made,
                    generated_files=generated_files,
                    logs=logs,
                )

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
                return AgentResult(
                    answer="",
                    iterations=iteration,
                    tool_calls_made=tool_calls_made,
                    generated_files=generated_files,
                    logs=logs,
                    error=str(exc),
                )

            self._write_trace(playbook_name, iteration, response)

            tc_from_wrapper = getattr(response, "tool_calls", None) or []
            content_len = len(str(response.content or ""))
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
                    logs.append(
                        f"  Iter {iteration}: repaired {len(repaired)} tool call(s) from text"
                    )

            # ── no tool calls → natural finish (with safety-net write) ────
            if not has_calls:
                if response.content:
                    messages.append(AIMessage(content=str(response.content)))

                # Repo-exploration guard: agent has a repo but hasn't called ANY
                # tool yet.  Rather than just asking the LLM again (which often
                # gets ignored), automatically dispatch `get_map` so the agent
                # has real architecture data to work with on the next turn.
                if (
                    self._repo_id
                    and not self._has_tool_history(messages)
                    and not repo_exploration_retry_used
                ):
                    repo_exploration_retry_used = True
                    logs.append(
                        f"  Iter {iteration}: no tools called — "
                        f"auto-dispatching get_map for repo_id='{self._repo_id}'"
                    )
                    forced_call = [{
                        "name": "get_map",
                        "args": {"repo_id": self._repo_id},
                        "id": f"forced_get_map_{iteration}",
                        "type": "tool_call",
                    }]
                    forced_ai_msg = AIMessage(content="", tool_calls=forced_call)
                    messages.append(forced_ai_msg)
                    tool_messages = await self.dispatcher.dispatch(forced_call)
                    messages.extend(tool_messages)
                    tool_calls_made += 1
                    messages.append(HumanMessage(content=(
                        f"The repository architecture map has been retrieved above.\n"
                        f"Now use `read_file`, `search_code`, `grep_search`, `get_callers`, "
                        f"`get_callees`, `trace_path` etc. to examine actual code.\n"
                        f"Do NOT produce a final answer until you have read real source files."
                    )))
                    continue

                # Shallow-analysis / premature-give-up guard.
                #
                # Fires when:
                #  a) agent wrote a planning paragraph instead of calling tools, OR
                #  b) agent tried one approach, got no results, and immediately gave up
                #     (e.g. "No results were returned, it is not possible to identify...")
                #
                # In both cases, if we've made fewer than _MIN_REPO_TOOL_CALLS total
                # tool calls, push the agent to try alternative approaches.
                _response_text = str(response.content or "")
                if (
                    self._repo_id
                    and self._has_tool_history(messages)
                    and tool_calls_made < _MIN_REPO_TOOL_CALLS
                    and _should_force_continuation(_response_text, tool_calls_made)
                    and not shallow_analysis_retry_used
                ):
                    shallow_analysis_retry_used = True
                    is_giving_up = (
                        not _looks_like_planning(_response_text)
                        and tool_calls_made < 2
                    )
                    if is_giving_up:
                        retry_msg = (
                            f"You called {tool_calls_made} tool(s) and got no useful results, "
                            f"then gave up. That is NOT an acceptable conclusion.\n\n"
                            f"One tool returning empty results means **try a different approach**, "
                            f"not that the task is impossible.\n\n"
                            f"Try these alternatives in order:\n"
                            f"1. `get_map(repo_id='{self._repo_id}')` — get the full architecture and "
                            f"   file list to understand what types of files exist\n"
                            f"2. `grep_search(query='route\\|endpoint\\|handler\\|controller', "
                            f"   repo_id='{self._repo_id}')` — search raw source for routing patterns\n"
                            f"3. `search_code(queries=['router', 'handler', 'endpoint'], "
                            f"   repo_id='{self._repo_id}')` — semantic search for API code\n"
                            f"4. `list_repo_directory(repo_id='{self._repo_id}')` — walk the actual "
                            f"   filesystem instead of the graph index\n\n"
                            f"CALL TOOLS NOW — do not return a conclusion yet."
                        )
                    else:
                        retry_msg = (
                            f"You have only made {tool_calls_made} tool call(s). "
                            f"This is NOT enough for a thorough analysis.\n\n"
                            f"Your last response described what you plan to do — "
                            f"NOW ACTUALLY DO IT by calling tools.\n\n"
                            f"Use `read_file`, `search_code`, `grep_search`, `get_callers`, "
                            f"`get_callees`, `trace_path` etc. to read actual code before "
                            f"writing any conclusion.\n\n"
                            f"CONTINUE EXECUTING — call tools now."
                        )
                    logs.append(
                        f"  Iter {iteration}: {'premature give-up' if is_giving_up else 'shallow-analysis'} "
                        f"detected after {tool_calls_made} tool call(s) — forcing retry"
                    )
                    messages.append(HumanMessage(content=retry_msg))
                    continue

                # Safety net: if the model produced code as prose but never
                # called write_file_system, try to persist it.  This is a
                # model-limitation workaround, not playbook-specific.
                if not self._has_tool_history(messages):
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
                        return AgentResult(
                            answer=answer,
                            iterations=iteration,
                            tool_calls_made=tool_calls_made,
                            generated_files=generated_files,
                            logs=logs,
                        )

                # Normal finish — either the agent completed its analysis or
                # there genuinely was nothing to do.
                answer = str(response.content or "")
                if not answer.strip():
                    logs.append(
                        f"  Iter {iteration}: empty response — synthesising from tool data"
                    )
                    answer = await self._synthesize(messages, goal, playbook_name, reason="empty_response")
                logs.append(f"ReAct finished naturally at iteration {iteration}")
                return AgentResult(
                    answer=answer,
                    iterations=iteration,
                    tool_calls_made=tool_calls_made,
                    generated_files=generated_files,
                    logs=logs,
                )

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
            for idx, tm in enumerate(tool_messages):
                try:
                    raw = str(tm.content or "{}")
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        continue
                    fp = payload.get("file_path")
                    is_write = (
                        getattr(tm, "name", "") == "write_file_system"
                        or (fp and payload.get("success") and payload.get("bytes_written"))
                    )
                    if is_write and fp:
                        generated_files.append(str(fp))
                        print(f"[REACT] ✅ File written: {fp}")
                except Exception:
                    continue

        # Unreachable in practice (handled above), but kept as safety net
        answer = await self._synthesize(messages, goal, playbook_name, reason="max_iterations")
        return AgentResult(
            answer=answer,
            iterations=max_iterations,
            tool_calls_made=tool_calls_made,
            generated_files=generated_files,
            logs=logs,
        )
