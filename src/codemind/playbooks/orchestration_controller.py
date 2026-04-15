from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from langchain_core.messages import BaseMessage, ToolMessage

from .orchestration_prompts import (
    PROMPT_INSUFFICIENT_EVIDENCE_CONTINUE,
    PROMPT_PARSE_RECOVERY_CONTINUE,
    PROMPT_POST_GET_MAP_CONTINUE,
    PROMPT_STRATEGY_RECOVERY_CONTINUE,
)


@dataclass
class OrchestrationState:
    """Mutable run-state for loop stabilization and recovery."""

    repo_exploration_retry_used: bool = False
    shallow_analysis_retry_used: bool = False
    forced_recovery_steps: int = 0
    consecutive_no_evidence_turns: int = 0
    consecutive_parse_failures: int = 0


class NextAction(str, Enum):
    NONE = "none"
    FORCE_TOOL = "force_tool"
    CONTINUE_PROMPT = "continue_prompt"
    SYNTHESIZE = "synthesize"


@dataclass
class OrchestrationDecision:
    action: NextAction
    tool_call: dict | None = None
    prompt: str | None = None
    synth_reason: str | None = None


class OrchestrationController:
    """
    Centralized orchestration policy for the ReAct loop.

    Handles:
      - parse-failure streak tracking
      - no-evidence streak tracking
      - normalized outcome extraction from ToolMessage payloads
      - declarative fallback tool selection
    """

    def __init__(
        self,
        *,
        repo_id: str | None,
        dispatcher: Any,
        max_consecutive_no_evidence: int,
        max_consecutive_parse_failures: int,
        max_forced_recovery_steps: int,
    ) -> None:
        self.repo_id = repo_id
        self.dispatcher = dispatcher
        self.max_consecutive_no_evidence = max(1, int(max_consecutive_no_evidence))
        self.max_consecutive_parse_failures = max(1, int(max_consecutive_parse_failures))
        self.max_forced_recovery_steps = max(1, int(max_forced_recovery_steps))
        self.state = OrchestrationState()

    @staticmethod
    def has_tool_history(messages: list[BaseMessage]) -> bool:
        return any(isinstance(m, ToolMessage) for m in messages)

    @property
    def no_evidence_streak(self) -> int:
        return self.state.consecutive_no_evidence_turns

    @property
    def parse_failure_streak(self) -> int:
        return self.state.consecutive_parse_failures

    @property
    def forced_recovery_steps(self) -> int:
        return self.state.forced_recovery_steps

    @staticmethod
    def _tool_payload(msg: ToolMessage) -> dict:
        try:
            payload = json.loads(str(msg.content or "{}"))
            return payload if isinstance(payload, dict) else {"raw": str(msg.content or "")}
        except Exception:
            return {"raw": str(msg.content or "")}

    def tool_outcome_meta(self, msg: ToolMessage) -> dict:
        """
        Return normalized tool outcome metadata.

        Uses dispatcher-attached `_meta` when present; otherwise infers a fallback
        outcome so orchestration remains stable across legacy payloads.
        """
        payload = self._tool_payload(msg)
        meta = payload.get("_meta")
        if isinstance(meta, dict):
            return meta

        outcome = "success"
        evidence_score = 0.0
        if payload.get("error"):
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
            "tool": getattr(msg, "name", ""),
            "fallback_tools": [],
            "call_signature": "",
        }

    def recent_tool_outcomes(self, messages: list[BaseMessage], window: int = 8) -> list[dict]:
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        out: list[dict] = []
        for tm in tool_msgs[-window:]:
            meta = self.tool_outcome_meta(tm)
            if not meta.get("tool"):
                meta["tool"] = getattr(tm, "name", "")
            out.append(meta)
        return out

    @staticmethod
    def outcomes_have_evidence(outcomes: list[dict]) -> bool:
        return any(float(o.get("evidence_score", 0.0) or 0.0) > 0.0 for o in outcomes)

    def record_parse_attempt(self, *, repaired: bool, looks_like_tool_json: bool) -> None:
        if repaired:
            self.state.consecutive_parse_failures = 0
            return
        if looks_like_tool_json:
            self.state.consecutive_parse_failures += 1
        else:
            self.state.consecutive_parse_failures = 0

    def should_trigger_parse_breaker(self) -> bool:
        return (
            bool(self.repo_id)
            and self.state.consecutive_parse_failures >= self.max_consecutive_parse_failures
            and self.state.forced_recovery_steps < self.max_forced_recovery_steps
        )

    def on_forced_recovery(self) -> None:
        self.state.forced_recovery_steps += 1
        self.state.consecutive_parse_failures = 0

    def mark_shallow_retry_used(self) -> None:
        self.state.shallow_analysis_retry_used = True

    def can_use_shallow_retry(self) -> bool:
        return not self.state.shallow_analysis_retry_used

    def mark_repo_exploration_retry_used(self) -> None:
        self.state.repo_exploration_retry_used = True

    def can_use_repo_exploration_retry(self) -> bool:
        return not self.state.repo_exploration_retry_used

    def note_tool_turn(self, turn_outcomes: list[dict]) -> None:
        if not turn_outcomes:
            self.state.consecutive_no_evidence_turns += 1
            return
        if self.outcomes_have_evidence(turn_outcomes):
            self.state.consecutive_no_evidence_turns = 0
        else:
            self.state.consecutive_no_evidence_turns += 1

    def should_trigger_no_evidence_breaker(self) -> bool:
        return (
            bool(self.repo_id)
            and self.state.consecutive_no_evidence_turns >= self.max_consecutive_no_evidence
            and self.state.forced_recovery_steps < self.max_forced_recovery_steps
        )

    def is_recovery_exhausted(self) -> bool:
        return self.state.forced_recovery_steps >= self.max_forced_recovery_steps

    def build_forced_recovery_call(
        self,
        messages: list[BaseMessage],
        iteration: int,
        recent_outcomes: list[dict] | None = None,
    ) -> dict | None:
        """
        Pick the next recovery tool call using declarative fallback metadata.

        Priority:
          1) fallback_tools from latest failing outcome metadata
          2) generic discovery tools as final fallback
        """
        try:
            tool_specs = self.dispatcher.get_tool_specs() or {}
        except Exception:
            tool_specs = {}
        available = set(tool_specs.keys())
        used = {
            getattr(m, "name", "")
            for m in messages
            if isinstance(m, ToolMessage) and getattr(m, "name", "")
        }

        def _build_call(name: str) -> dict | None:
            spec = tool_specs.get(name)
            if not spec:
                return None
            if getattr(spec, "requires_repo", False) and not self.repo_id:
                return None
            if name == "get_map":
                args = {"limit": 20}
                if self.repo_id:
                    args["repo_id"] = self.repo_id
                return {
                    "name": name,
                    "args": args,
                    "id": f"forced_{name}_{iteration}",
                    "type": "tool_call",
                }
            if name == "list_repo_directory":
                args = {
                    "relative_path": ".",
                    "recursive": True,
                    "max_depth": 2,
                    "max_entries": 300,
                }
                if self.repo_id:
                    args["repo_id"] = self.repo_id
                return {
                    "name": name,
                    "args": args,
                    "id": f"forced_{name}_{iteration}",
                    "type": "tool_call",
                }
            if name == "list_files":
                if not self.repo_id:
                    return None
                return {
                    "name": name,
                    "args": {"repo_id": self.repo_id},
                    "id": f"forced_{name}_{iteration}",
                    "type": "tool_call",
                }
            return None

        if recent_outcomes:
            latest = recent_outcomes[-1]
            fallback_tools = latest.get("fallback_tools") or []
            if isinstance(fallback_tools, list):
                for name in fallback_tools:
                    if name in available and name not in used:
                        call = _build_call(name)
                        if call:
                            return call

        if self.repo_id:
            for name in ("get_map", "list_repo_directory", "list_files"):
                if name not in available or name in used:
                    continue
                call = _build_call(name)
                if call:
                    return call
        return None

    def decide_no_tool_iteration(
        self,
        *,
        messages: list[BaseMessage],
        iteration: int,
        tool_calls_made: int,
        response_text: str,
        min_repo_tool_calls: int,
        should_force_continuation: Callable[[str, int], bool],
    ) -> OrchestrationDecision:
        """
        Decide next action when the model returned no tool calls.
        """
        if (
            self.repo_id
            and not self.has_tool_history(messages)
            and self.can_use_repo_exploration_retry()
        ):
            self.mark_repo_exploration_retry_used()
            return OrchestrationDecision(
                action=NextAction.FORCE_TOOL,
                tool_call={
                    "name": "get_map",
                    "args": {"repo_id": self.repo_id},
                    "id": f"forced_get_map_{iteration}",
                    "type": "tool_call",
                },
                prompt=PROMPT_POST_GET_MAP_CONTINUE,
            )

        if self.should_trigger_parse_breaker():
            recent_outcomes = self.recent_tool_outcomes(messages, window=8)
            forced_call = self.build_forced_recovery_call(
                messages, iteration, recent_outcomes=recent_outcomes
            )
            if forced_call:
                self.on_forced_recovery()
                return OrchestrationDecision(
                    action=NextAction.FORCE_TOOL,
                    tool_call=forced_call,
                    prompt=PROMPT_PARSE_RECOVERY_CONTINUE,
                )

        recent_outcomes = self.recent_tool_outcomes(messages, window=8)
        has_recent_evidence = self.outcomes_have_evidence(recent_outcomes)
        if (
            self.repo_id
            and self.has_tool_history(messages)
            and tool_calls_made < min_repo_tool_calls
            and (
                should_force_continuation(response_text, tool_calls_made)
                or not has_recent_evidence
            )
            and self.can_use_shallow_retry()
        ):
            self.mark_shallow_retry_used()
            forced_call = None
            if not self.is_recovery_exhausted():
                forced_call = self.build_forced_recovery_call(
                    messages, iteration, recent_outcomes=recent_outcomes
                )
            if forced_call:
                self.on_forced_recovery()
                return OrchestrationDecision(
                    action=NextAction.FORCE_TOOL,
                    tool_call=forced_call,
                    prompt=PROMPT_STRATEGY_RECOVERY_CONTINUE,
                )
            return OrchestrationDecision(
                action=NextAction.CONTINUE_PROMPT,
                prompt=PROMPT_INSUFFICIENT_EVIDENCE_CONTINUE,
            )

        if (
            self.repo_id
            and self.has_tool_history(messages)
            and self.state.consecutive_no_evidence_turns >= self.max_consecutive_no_evidence
            and self.is_recovery_exhausted()
        ):
            return OrchestrationDecision(
                action=NextAction.SYNTHESIZE,
                synth_reason="insufficient_evidence",
            )

        return OrchestrationDecision(action=NextAction.NONE)

    def decide_after_tool_turn(
        self,
        *,
        messages: list[BaseMessage],
        iteration: int,
        turn_outcomes: list[dict],
    ) -> OrchestrationDecision:
        """
        Decide next action after a tool-dispatch turn.
        """
        self.note_tool_turn(turn_outcomes)
        if (
            turn_outcomes
            and self.should_trigger_no_evidence_breaker()
        ):
            forced_call = self.build_forced_recovery_call(
                messages, iteration, recent_outcomes=turn_outcomes
            )
            if forced_call:
                self.on_forced_recovery()
                return OrchestrationDecision(
                    action=NextAction.FORCE_TOOL,
                    tool_call=forced_call,
                )

        return OrchestrationDecision(action=NextAction.NONE)
