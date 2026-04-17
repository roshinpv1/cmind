from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "codemind"
    / "playbooks"
    / "quality_guard.py"
)
_SPEC = spec_from_file_location("quality_guard_module", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MOD = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
_SPEC.loader.exec_module(_MOD)  # type: ignore[union-attr]
evaluate_quality_guard = _MOD.evaluate_quality_guard


def test_quality_guard_shadow_mode_never_blocks():
    result = evaluate_quality_guard(
        answer="Potential issue in `src/app.py`.",
        output_type="",
        evidence_files=set(),
        read_files=set(),
        expected_critical_files=[],
        enable_verifier=True,
        enable_grounding=True,
        enable_risk_coverage=False,
        enforce=False,
    )
    assert result.passed is True
    assert result.mode == "shadow"
    assert "ungrounded_file_claims" in result.reasons


def test_quality_guard_enforce_blocks_ungrounded_claims():
    result = evaluate_quality_guard(
        answer="Found bug in `src/app.py` and `src/db.py`.",
        output_type="",
        evidence_files={"src/app.py"},
        read_files={"src/app.py"},
        expected_critical_files=[],
        enable_verifier=False,
        enable_grounding=True,
        enable_risk_coverage=False,
        enforce=True,
    )
    assert result.passed is False
    assert "ungrounded_file_claims" in result.reasons


def test_quality_guard_enforce_blocks_low_risk_weighted_coverage():
    result = evaluate_quality_guard(
        answer="Final response.",
        output_type="",
        evidence_files={"src/auth/service.py"},
        read_files={"src/auth/service.py"},
        expected_critical_files=["src/auth/service.py", "src/security/policy.py", "src/api/handler.py"],
        enable_verifier=False,
        enable_grounding=False,
        enable_risk_coverage=True,
        enforce=True,
    )
    assert result.passed is False
    assert "risk_weighted_coverage_low" in result.reasons
