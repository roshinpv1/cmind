from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class QualityGuardResult:
    passed: bool
    mode: str
    reasons: list[str]
    details: dict


_FILE_TOKEN_RE = re.compile(
    r"(?:`|\")?([A-Za-z0-9_\-./]+\.[A-Za-z0-9]{1,10})(?:`|\")?"
)


def _extract_file_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for match in _FILE_TOKEN_RE.findall(text or ""):
        token = (match or "").strip()
        if "/" in token or "." in token:
            out.add(token)
    return out


def _safe_json_load(text: str) -> dict | list | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    fenced = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            return None
    return None


def _risk_weight(path: str) -> float:
    p = (path or "").lower()
    score = 1.0
    if any(k in p for k in ("auth", "security", "policy", "permission", "acl", "rbac")):
        score += 2.0
    if any(k in p for k in ("handler", "controller", "route", "endpoint", "api")):
        score += 1.5
    if any(k in p for k in ("config", ".env", "secret", "key", "credential", "token")):
        score += 2.0
    if any(k in p for k in ("sql", "query", "migration", "db", "repository", "dao")):
        score += 1.5
    if any(k in p for k in ("deserialize", "pickle", "yaml", "xml", "template")):
        score += 1.0
    return score


def evaluate_quality_guard(
    *,
    answer: str,
    output_type: str,
    evidence_files: set[str],
    read_files: set[str],
    expected_critical_files: list[str] | None,
    enable_verifier: bool,
    enable_grounding: bool,
    enable_risk_coverage: bool,
    enforce: bool,
) -> QualityGuardResult:
    reasons: list[str] = []
    details: dict = {}

    mode = "enforce" if enforce else "shadow"
    text = (answer or "").strip()
    parsed = _safe_json_load(text)

    if enable_verifier:
        if not text:
            reasons.append("empty_answer")
        if (output_type or "").lower() == "json_response" and parsed is None:
            reasons.append("json_not_parseable")
        details["verifier_checked"] = True

    if enable_grounding:
        claimed_files = _extract_file_tokens(text)
        unknown_claims = sorted(
            f for f in claimed_files
            if f not in evidence_files and f not in read_files
        )
        # Ignore obviously non-repo URI-ish fragments.
        unknown_claims = [
            x for x in unknown_claims
            if not x.startswith(("http://", "https://", "catalog://"))
        ]
        details["claimed_files"] = sorted(claimed_files)
        details["unknown_claimed_files"] = unknown_claims[:20]
        if unknown_claims:
            reasons.append("ungrounded_file_claims")

    if enable_risk_coverage:
        expected = [str(p).strip() for p in (expected_critical_files or []) if str(p).strip()]
        if expected:
            expected_set = set(expected)
            total_weight = sum(_risk_weight(p) for p in expected_set)
            covered_weight = sum(_risk_weight(p) for p in expected_set if p in read_files)
            ratio = (covered_weight / total_weight) if total_weight > 0 else 1.0
            details["risk_coverage_ratio"] = round(ratio, 3)
            details["risk_expected_count"] = len(expected_set)
            details["risk_read_count"] = len(expected_set & read_files)
            if ratio < 0.45:
                reasons.append("risk_weighted_coverage_low")
        else:
            details["risk_coverage_ratio"] = 1.0

    passed = len(reasons) == 0
    if not enforce:
        # Shadow mode: never block finalization.
        passed = True

    return QualityGuardResult(
        passed=passed,
        mode=mode,
        reasons=reasons,
        details=details,
    )
