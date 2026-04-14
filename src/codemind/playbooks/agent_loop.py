"""
ReAct (Reason + Act) loop for code analysis playbooks.

Design:
  - Plain async loop — no LangGraph StateGraph, making the flow transparent and debuggable.
  - Three named phases:
      Phase A  Architecture map    First turn. Inject graph prefetch; force get_map when the
                                   model hasn't called any tool yet.
      Phase B  Targeted exploration LLM-driven tool calls. We dispatch and feed results back.
      Phase C  Synthesis           When the model stops calling tools, generate the final answer.
  - Stop conditions:
      * Model issues no tool calls and tool history exists  → natural finish (Phase C).
      * Model issues no tool calls and no tool history      → enforce get_map (Phase A guardrail).
      * iteration >= max_iterations                         → synthesise from collected evidence.
      * LLM returns empty response with no tool calls       → synthesise.
  - Tool-call repair: plain-text JSON plans are parsed and converted to proper tool_calls.
"""

from __future__ import annotations

import json
import logging
import os
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

_STRATEGIC_DIRECTIVE = """
### DISCOVERY PROTOCOL (follow A → B → C)

Phase A – ARCHITECTURE MAP (first turn always)
  Call `get_map` to get the repository's structural GPS: high-degree nodes, entry points.
  Read the map to build a prioritised reading list before opening any file.

Phase B – TARGETED EXPLORATION
  Use `trace_path`, `get_callers`, `get_callees`, `get_dependencies` to plan a concrete
  path (entry → sink). Call `get_file_outline` before `read_file` on large modules.
  Use `search_code` only for specific patterns found via Phase A/B — one call per turn
  with a combined regex or `queries` array.

Phase C – SYNTHESIS
  Once you have enough evidence, respond with your final analysis — no more tool calls.
  Base every claim on the tool data you observed. Do not hallucinate.

RESILIENCE: If a tool reports a feature is disabled, adapt using other tools. Never stop early.
"""


# ── Result contract ───────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Outcome of one ReAct run."""
    answer: str
    iterations: int
    tool_calls_made: int
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

    def _forced_get_map(self, iteration: int) -> AIMessage:
        args: dict[str, Any] = {}
        if self._repo_id:
            args["repo_id"] = self._repo_id
        return AIMessage(
            content="Collecting architecture map before proceeding.",
            tool_calls=[{
                "name": "get_map",
                "args": args,
                "id": f"forced_get_map_{iteration}",
                "type": "tool_call",
            }],
        )

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
    ) -> str:
        parts = [
            str(m.content)[:20_000]
            for m in messages
            if isinstance(m, ToolMessage) and m.content
        ]
        bundle = "\n\n---\n\n".join(parts[-16:])
        if not bundle.strip():
            return (
                "No tool data was collected before the step limit was reached. "
                "Try increasing max_iterations or narrowing the goal."
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

        Parameters
        ----------
        goal            User goal / question forwarded as the first HumanMessage.
        system_prompt   Base playbook system prompt (anti-patterns, rubric, etc.).
        prefetch_block  Pre-fetched graph context block injected into iteration-0 system prompt.
        max_iterations  Hard cap; on overflow the agent synthesises from collected evidence.
        playbook_name   Used for trace log filenames.
        """
        logs: list[str] = [
            f"ReAct start | playbook={playbook_name} | max_iter={max_iterations}"
        ]
        messages: list[BaseMessage] = [HumanMessage(content=goal)]
        tool_calls_made = 0

        for iteration in range(max_iterations + 1):

            # ── compact context if growing too large ───────────────────────
            messages = await self.compactor.compact(messages)

            # ── build per-turn system prompt ───────────────────────────────
            # Inject the full strategy only on the first two iterations.
            # Later turns get a short reminder to save context budget on small models.
            if iteration < 2:
                local_sys = system_prompt + _STRATEGIC_DIRECTIVE
            else:
                local_sys = system_prompt + "\n\n### REMINDER\nContinue Phase B→C. Call tools or synthesize."
            if iteration == 0 and prefetch_block:
                local_sys += prefetch_block
            if self._repo_id:
                local_sys += (
                    f"\n\n### STRICT ENFORCEMENT\n"
                    f"Always pass repo_id='{self._repo_id}' to every tool call. "
                    "Never search globally."
                )

            full_messages = [SystemMessage(content=local_sys)] + messages

            # ── max iterations: synthesise from collected evidence ─────────
            if iteration >= max_iterations:
                logs.append(f"Max iterations ({max_iterations}) reached — synthesising")
                answer = await self._synthesize(messages, goal, playbook_name)
                return AgentResult(
                    answer=answer,
                    iterations=iteration,
                    tool_calls_made=tool_calls_made,
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
                    logs=logs,
                    error=str(exc),
                )

            self._write_trace(playbook_name, iteration, response)

            # ── Phase A: repair plain-text tool plans ─────────────────────
            has_calls = bool(getattr(response, "tool_calls", None))

            if not has_calls and response.content:
                repaired = self.dispatcher.repair_tool_calls(str(response.content))
                if repaired:
                    response   = AIMessage(content="", tool_calls=repaired)
                    has_calls  = True
                    logs.append(
                        f"  Iter {iteration}: repaired {len(repaired)} tool call(s) from text"
                    )

            # ── Phase A guardrail: force get_map if no tool history yet ───
            if not has_calls and not self._has_tool_history(messages):
                response  = self._forced_get_map(iteration)
                has_calls = True
                logs.append(f"  Iter {iteration}: forced get_map (no tool history yet)")

            # ── Phase C: no tool calls → natural finish ───────────────────
            if not has_calls:
                answer = str(response.content or "")
                if not answer.strip():
                    logs.append(
                        f"  Iter {iteration}: empty response — synthesising from tool data"
                    )
                    answer = await self._synthesize(messages, goal, playbook_name)
                logs.append(f"ReAct finished naturally at iteration {iteration}")
                return AgentResult(
                    answer=answer,
                    iterations=iteration,
                    tool_calls_made=tool_calls_made,
                    logs=logs,
                )

            # ── Phase B: dispatch tool calls ───────────────────────────────
            messages.append(response)
            call_count  = len(response.tool_calls)
            call_names  = [tc.get("name") for tc in response.tool_calls]
            logs.append(
                f"  Iter {iteration}: dispatching {call_count} tool(s) {call_names}"
            )

            tool_messages = await self.dispatcher.dispatch(response.tool_calls)
            messages.extend(tool_messages)
            tool_calls_made += call_count

        # Unreachable in practice (handled above), but kept as safety net
        answer = await self._synthesize(messages, goal, playbook_name)
        return AgentResult(
            answer=answer,
            iterations=max_iterations,
            tool_calls_made=tool_calls_made,
            logs=logs,
        )
