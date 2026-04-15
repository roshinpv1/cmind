from __future__ import annotations

from pydantic import BaseModel, Field

from codemind.playbooks.final_state import evaluate_final_state


class _Schema(BaseModel):
    summary: str = Field(...)
    findings: list[str] = Field(default_factory=list)


def test_rejects_intermediate_planning_language():
    decision = evaluate_final_state(
        response_text="Initial analysis completed. Next step is to inspect controllers.",
        repo_id="abc123",
        tool_calls_made=1,
        has_tool_history=True,
        output_type="",
        output_schema_model=None,
    )
    assert not decision.is_final
    assert decision.reason == "intermediate_or_giveup_language"


def test_rejects_tool_call_playbook_prose_finish():
    decision = evaluate_final_state(
        response_text="Here is the final answer in prose.",
        repo_id=None,
        tool_calls_made=0,
        has_tool_history=False,
        output_type="tool_call",
        output_schema_model=None,
    )
    assert not decision.is_final
    assert decision.reason == "expected_tool_call_output"


def test_rejects_json_response_when_required_fields_missing():
    decision = evaluate_final_state(
        response_text='{"summary": ""}',
        repo_id=None,
        tool_calls_made=0,
        has_tool_history=False,
        output_type="json_response",
        output_schema_model=_Schema,
    )
    assert not decision.is_final
    assert decision.reason == "json_missing_required_fields"
    assert decision.continue_prompt is not None
    assert "summary" in decision.continue_prompt


def test_accepts_valid_json_response():
    decision = evaluate_final_state(
        response_text='{"summary":"done","findings":["a"]}',
        repo_id=None,
        tool_calls_made=0,
        has_tool_history=False,
        output_type="json_response",
        output_schema_model=_Schema,
    )
    assert decision.is_final
