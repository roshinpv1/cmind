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


def test_rejects_repo_finalization_when_evidence_contract_not_met():
    decision = evaluate_final_state(
        response_text="Completed analysis.",
        repo_id="abc123",
        tool_calls_made=4,
        has_tool_history=True,
        output_type="",
        output_schema_model=None,
        evidence_stats={
            "unique_read_files": 0,
            "structural_calls": 0,
            "lexical_calls": 0,
            "evidence_messages": 0,
        },
    )
    assert not decision.is_final
    assert decision.reason == "evidence_contract_not_met"
    assert decision.continue_prompt is not None
    assert "Evidence coverage is incomplete" in decision.continue_prompt


def test_accepts_repo_finalization_when_evidence_contract_met():
    decision = evaluate_final_state(
        response_text="Final evidence-backed answer.",
        repo_id="abc123",
        tool_calls_made=4,
        has_tool_history=True,
        output_type="",
        output_schema_model=None,
        evidence_stats={
            "unique_read_files": 3,
            "structural_calls": 2,
            "lexical_calls": 2,
            "evidence_messages": 3,
        },
    )
    assert decision.is_final


def test_rejects_when_critical_candidates_not_covered():
    decision = evaluate_final_state(
        response_text="Final answer.",
        repo_id="abc123",
        tool_calls_made=6,
        has_tool_history=True,
        output_type="",
        output_schema_model=None,
        evidence_stats={
            "unique_read_files": 4,
            "structural_calls": 2,
            "lexical_calls": 2,
            "evidence_messages": 3,
            "critical_candidates_total": 10,
            "critical_candidates_read": 2,
            "critical_coverage_ratio": 0.2,
        },
    )
    assert not decision.is_final
    assert decision.reason == "evidence_contract_not_met"
    assert "critical ranked files" in (decision.continue_prompt or "")
