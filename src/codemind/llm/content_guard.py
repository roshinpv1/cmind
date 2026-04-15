"""
Pre-LLM content guard: masks sensitive patterns in code before sending to the LLM.

Enterprise guardrails often flag legitimate code patterns (connection strings,
example credentials in comments/tests, base64 blobs, JWT samples, private keys
in docs) as real secrets — causing false-positive blocks.

This module scans text destined for the LLM, replaces flagged patterns with
deterministic placeholders, and logs every detection with file path / line
context so teams can audit what was masked.

Usage:
    from codemind.llm.content_guard import ContentGuard

    guard = ContentGuard()
    masked_text, report = guard.mask(text)
    # masked_text  → safe to send to LLM
    # report       → list of MaskedItem (pattern, location, original snippet)

Environment variables:
    CONTENT_GUARD_ENABLED   – "true" (default) or "false" to bypass entirely
    CONTENT_GUARD_LOG_FILE  – path to write JSON-lines audit log (default: /tmp/codemind_content_guard.jsonl)
    CONTENT_GUARD_EXTRA_PATTERNS – JSON file with additional {"name": "...", "regex": "..."} entries
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Detection patterns ────────────────────────────────────────────────────────
# Each tuple: (pattern_name, compiled_regex, replacement_template)
# Replacement uses a deterministic placeholder so the LLM sees consistent tokens
# and the response can still reference "the connection string at <MASKED_...>".

_PATTERNS: list[tuple[str, re.Pattern, str]] = []


def _p(name: str, pattern: str, replacement: str, flags: int = 0) -> None:
    _PATTERNS.append((name, re.compile(pattern, flags | re.MULTILINE), replacement))


# --- Credentials & secrets ---------------------------------------------------
_p("aws_access_key",       r'\b(AKIA[0-9A-Z]{16})\b',                                          '<MASKED_AWS_KEY>')
_p("aws_secret_key",       r'(?i)(aws[_-]?secret[_-]?access[_-]?key\s*[=:]\s*)["\']?([A-Za-z0-9/+=]{40})["\']?',
                                                                                                r'\1<MASKED_AWS_SECRET>')
_p("generic_api_key",      r'(?i)(api[_-]?key\s*[=:]\s*)["\']?([A-Za-z0-9_\-]{20,})["\']?',    r'\1<MASKED_API_KEY>')
_p("generic_secret",       r'(?i)(secret\s*[=:]\s*)["\']?([A-Za-z0-9_\-/+=]{16,})["\']?',       r'\1<MASKED_SECRET>')
_p("generic_token",        r'(?i)(token\s*[=:]\s*)["\']?([A-Za-z0-9_\-/.]{20,})["\']?',         r'\1<MASKED_TOKEN>')
_p("generic_password",     r'(?i)(password\s*[=:]\s*)["\']?([^\s"\']{8,})["\']?',                r'\1<MASKED_PASSWORD>')
_p("bearer_token",         r'(?i)(Bearer\s+)([A-Za-z0-9_\-/.+=]{20,})',                         r'\1<MASKED_BEARER>')
_p("basic_auth_header",    r'(?i)(Basic\s+)([A-Za-z0-9+/=]{10,})',                              r'\1<MASKED_BASIC_AUTH>')

# --- Connection strings -------------------------------------------------------
_p("jdbc_connection",      r'(?i)(jdbc:[a-z0-9]+://)[^\s"\'<>]+',                               r'\1<MASKED_JDBC_URL>')
_p("db_connection_string", r'(?i)((?:mysql|postgres|postgresql|mongodb|redis|amqp|mssql)://)[^\s"\'<>]+',
                                                                                                r'\1<MASKED_DB_URL>')
_p("odbc_connection",      r'(?i)((?:Server|Data Source)\s*=\s*)[^;"\n]+',                      r'\1<MASKED_SERVER>')

# --- Private keys & certificates ----------------------------------------------
_p("private_key_block",    r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
                                                                                                '<MASKED_PRIVATE_KEY_BLOCK>')
_p("certificate_block",    r'-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----',     '<MASKED_CERTIFICATE_BLOCK>')

# --- Tokens & JWTs ------------------------------------------------------------
_p("jwt_token",            r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b',
                                                                                                '<MASKED_JWT>')
_p("github_token",         r'\b(gh[ps]_[A-Za-z0-9]{36,})\b',                                   '<MASKED_GITHUB_TOKEN>')
_p("npm_token",            r'(?i)(//registry\.npmjs\.org/:_authToken=)\S+',                      r'\1<MASKED_NPM_TOKEN>')
_p("slack_token",          r'\b(xox[bporas]-[A-Za-z0-9\-]+)\b',                                 '<MASKED_SLACK_TOKEN>')

# --- IP addresses & internal hostnames ----------------------------------------
_p("ipv4_address",         r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b',                        '<MASKED_IP>')
_p("internal_hostname",    r'(?i)\b([a-z0-9\-]+\.(?:internal|corp|local|intranet)\.[a-z]{2,})\b',
                                                                                                '<MASKED_HOSTNAME>')

# --- Email addresses -----------------------------------------------------------
_p("email_address",        r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',            '<MASKED_EMAIL>')

# --- High-entropy strings (base64 blobs, hex secrets) -------------------------
_p("base64_blob",          r'(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])',   '<MASKED_BASE64>')
_p("hex_secret",           r'(?i)(?:0x)?[0-9a-f]{40,}\b',                                       '<MASKED_HEX>')


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class MaskedItem:
    """One detected & masked sensitive pattern."""
    pattern_name: str
    file_hint: str
    line_number: int
    original_snippet: str  # truncated to 80 chars for audit
    replacement: str

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern_name,
            "file": self.file_hint,
            "line": self.line_number,
            "snippet": self.original_snippet[:80],
            "replacement": self.replacement,
        }


@dataclass
class MaskReport:
    """Summary of all masking operations for one text blob."""
    items: list[MaskedItem] = field(default_factory=list)
    total_masked: int = 0

    @property
    def has_detections(self) -> bool:
        return self.total_masked > 0


# ── Allowlist for known safe patterns ─────────────────────────────────────────

_ALLOWLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r'(?i)example\.com'),
    re.compile(r'(?i)localhost'),
    re.compile(r'\b127\.0\.0\.1\b'),
    re.compile(r'\b0\.0\.0\.0\b'),
    re.compile(r'(?i)placeholder'),
    re.compile(r'(?i)your[_-]?api[_-]?key'),
    re.compile(r'(?i)xxx+'),
    re.compile(r'(?i)<your[_-]'),
    re.compile(r'(?i)changeme'),
    re.compile(r'(?i)password123'),
    re.compile(r'(?i)test[_-]?secret'),
    re.compile(r'(?i)dummy'),
    re.compile(r'(?i)fake[_-]?'),
]


def _is_allowlisted(match_text: str) -> bool:
    """Return True if the matched text is a known-safe placeholder/example."""
    for pat in _ALLOWLIST_PATTERNS:
        if pat.search(match_text):
            return True
    return False


# ── Core guard ────────────────────────────────────────────────────────────────

class ContentGuard:
    """Scans and masks sensitive patterns in text before LLM submission."""

    def __init__(self):
        self.enabled = os.getenv("CONTENT_GUARD_ENABLED", "true").lower() == "true"
        self._log_file = os.getenv(
            "CONTENT_GUARD_LOG_FILE", "/tmp/codemind_content_guard.jsonl"
        )
        self._extra_patterns: list[tuple[str, re.Pattern, str]] = []
        self._load_extra_patterns()

    def _load_extra_patterns(self) -> None:
        """Load additional patterns from a JSON file if configured."""
        extra_file = os.getenv("CONTENT_GUARD_EXTRA_PATTERNS")
        if not extra_file or not Path(extra_file).exists():
            return
        try:
            with open(extra_file, "r") as f:
                entries = json.load(f)
            for entry in entries:
                name = entry.get("name", "custom")
                regex = entry.get("regex", "")
                repl = entry.get("replacement", f"<MASKED_{name.upper()}>")
                if regex:
                    self._extra_patterns.append(
                        (name, re.compile(regex, re.MULTILINE), repl)
                    )
            if self._extra_patterns:
                logger.info("[CONTENT_GUARD] Loaded %d extra patterns from %s",
                            len(self._extra_patterns), extra_file)
        except Exception as e:
            logger.warning("[CONTENT_GUARD] Failed to load extra patterns: %s", e)

    @staticmethod
    def _extract_file_context(text: str, match_start: int) -> tuple[str, int]:
        """Best-effort extraction of file path and line number from surrounding text.

        Looks for common patterns like:
          "File: src/main.py" or "--- src/main.py ---" or "# src/main.py"
        in the text preceding the match, and counts newlines for line number.
        """
        preceding = text[:match_start]
        line_number = preceding.count("\n") + 1

        file_hint = ""
        file_patterns = [
            re.compile(r'(?:File|file|Path|path|Source):\s*(\S+)', re.IGNORECASE),
            re.compile(r'---\s*(\S+\.(?:py|js|ts|go|java|rs|cs|rb|yml|yaml|json|xml|toml|cfg|ini|env|properties))\s*---'),
            re.compile(r'#\s+(\S+\.(?:py|js|ts|go|java|rs|cs|rb))\b'),
            re.compile(r'Tool Result from (\S+)'),
        ]
        for fp in file_patterns:
            matches = list(fp.finditer(preceding))
            if matches:
                file_hint = matches[-1].group(1)
                break

        return file_hint, line_number

    def mask(self, text: str) -> tuple[str, MaskReport]:
        """Scan text, mask sensitive patterns, return (masked_text, report).

        If CONTENT_GUARD_ENABLED is false, returns (text, empty_report).
        """
        report = MaskReport()
        if not self.enabled or not text:
            return text, report

        all_patterns = _PATTERNS + self._extra_patterns
        masked = text

        for pattern_name, regex, replacement in all_patterns:
            for m in regex.finditer(masked):
                original = m.group(0)

                if _is_allowlisted(original):
                    continue

                file_hint, line_no = self._extract_file_context(text, m.start())

                report.items.append(MaskedItem(
                    pattern_name=pattern_name,
                    file_hint=file_hint,
                    line_number=line_no,
                    original_snippet=original[:80],
                    replacement=replacement if not replacement.startswith("\\") else "<MASKED>",
                ))

            masked = regex.sub(replacement, masked)

        report.total_masked = len(report.items)

        if report.has_detections:
            self._write_audit_log(report)
            logger.info("[CONTENT_GUARD] Masked %d sensitive patterns", report.total_masked)

        return masked, report

    def _write_audit_log(self, report: MaskReport) -> None:
        """Append detection details to a JSONL audit file."""
        try:
            log_path = Path(self._log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_masked": report.total_masked,
                    "detections": [item.to_dict() for item in report.items],
                }
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning("[CONTENT_GUARD] Failed to write audit log: %s", e)


# Module-level singleton for convenience
_guard: ContentGuard | None = None


def get_content_guard() -> ContentGuard:
    """Return the module-level ContentGuard singleton."""
    global _guard
    if _guard is None:
        _guard = ContentGuard()
    return _guard
