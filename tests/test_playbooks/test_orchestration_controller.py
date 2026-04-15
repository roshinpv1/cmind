import json
from dataclasses import dataclass

from langchain_core.messages import ToolMessage

from codemind.playbooks.orchestration_controller import (
    NextAction,
    OrchestrationController,
)


@dataclass
class _Spec:
    requires_repo: bool = False


class _Dispatcher:
    def __init__(self) -> None:
        self._specs = {
            "get_map": _Spec(requires_repo=True),
            "list_repo_directory": _Spec(requires_repo=True),
            "list_files": _Spec(requires_repo=True),
        }

    def get_tool_specs(self):
        return self._specs


def _tool_message(name: str, payload: dict, tool_call_id: str = "t1") -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload),
        name=name,
        tool_call_id=tool_call_id,
    )


def _controller() -> OrchestrationController:
    return OrchestrationController(
        repo_id="repo-1",
        dispatcher=_Dispatcher(),
        max_consecutive_no_evidence=2,
        max_consecutive_parse_failures=2,
        max_forced_recovery_steps=2,
    )


def test_decide_no_tool_iteration_bootstraps_get_map():
    ctl = _controller()

    decision = ctl.decide_no_tool_iteration(
        messages=[],
        iteration=0,
        tool_calls_made=0,
        response_text="",
        min_repo_tool_calls=3,
        should_force_continuation=lambda _text, _calls: False,
    )

    assert decision.action == NextAction.FORCE_TOOL
    assert decision.tool_call is not None
    assert decision.tool_call["name"] == "get_map"
    assert decision.tool_call["args"]["repo_id"] == "repo-1"
    assert ctl.state.repo_exploration_retry_used is True


def test_decide_no_tool_iteration_parse_breaker_forces_recovery():
    ctl = _controller()
    msgs = [_tool_message("get_map", {"_meta": {"evidence_score": 1.0}})]

    ctl.record_parse_attempt(repaired=False, looks_like_tool_json=True)
    ctl.record_parse_attempt(repaired=False, looks_like_tool_json=True)

    decision = ctl.decide_no_tool_iteration(
        messages=msgs,
        iteration=1,
        tool_calls_made=1,
        response_text='{"tool_name":"search_code"}',
        min_repo_tool_calls=3,
        should_force_continuation=lambda _text, _calls: False,
    )

    assert decision.action == NextAction.FORCE_TOOL
    assert decision.tool_call is not None
    assert decision.tool_call["name"] in {"list_repo_directory", "list_files"}
    assert ctl.parse_failure_streak == 0
    assert ctl.forced_recovery_steps == 1


def test_decide_no_tool_iteration_returns_continue_prompt_when_recovery_exhausted():
    ctl = _controller()
    msgs = [_tool_message("get_map", {"_meta": {"evidence_score": 0.0}})]

    ctl.state.shallow_analysis_retry_used = False
    ctl.state.forced_recovery_steps = ctl.max_forced_recovery_steps

    decision = ctl.decide_no_tool_iteration(
        messages=msgs,
        iteration=2,
        tool_calls_made=1,
        response_text="No results were returned.",
        min_repo_tool_calls=3,
        should_force_continuation=lambda text, _calls: "no results" in text.lower(),
    )

    assert decision.action == NextAction.CONTINUE_PROMPT
    assert decision.prompt is not None


def test_decide_no_tool_iteration_synthesizes_when_evidence_exhausted():
    ctl = _controller()
    msgs = [_tool_message("get_map", {"_meta": {"evidence_score": 0.0}})]

    ctl.state.consecutive_no_evidence_turns = ctl.max_consecutive_no_evidence
    ctl.state.forced_recovery_steps = ctl.max_forced_recovery_steps

    decision = ctl.decide_no_tool_iteration(
        messages=msgs,
        iteration=3,
        tool_calls_made=4,
        response_text="",
        min_repo_tool_calls=3,
        should_force_continuation=lambda _text, _calls: False,
    )

    assert decision.action == NextAction.SYNTHESIZE
    assert decision.synth_reason == "insufficient_evidence"


def test_decide_after_tool_turn_forces_recovery_on_no_evidence():
    ctl = _controller()
    msgs = [_tool_message("get_map", {"_meta": {"evidence_score": 0.0}})]
    turn_outcomes = [{"tool": "search_codebase", "evidence_score": 0.0, "fallback_tools": []}]

    # First weak turn increments streak only.
    first = ctl.decide_after_tool_turn(
        messages=msgs,
        iteration=0,
        turn_outcomes=turn_outcomes,
    )
    assert first.action == NextAction.NONE

    # Second weak turn reaches threshold and should force a fallback tool.
    second = ctl.decide_after_tool_turn(
        messages=msgs,
        iteration=1,
        turn_outcomes=turn_outcomes,
    )
    assert second.action == NextAction.FORCE_TOOL
    assert second.tool_call is not None
    assert second.tool_call["name"] in {"list_repo_directory", "list_files"}
