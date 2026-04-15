from __future__ import annotations

# Prompt catalog for orchestration recovery actions.
# Keeping prompts centralized makes policy behavior easier to tune and test.

PROMPT_POST_GET_MAP_CONTINUE = (
    "The repository architecture map has been retrieved above.\n"
    "Now use `read_file`, `search_code`, `grep_search`, `get_callers`, "
    "`get_callees`, `trace_path` etc. to examine actual code.\n"
    "Do NOT produce a final answer until you have read real source files."
)

PROMPT_PARSE_RECOVERY_CONTINUE = (
    "Continue execution using tool results above. "
    "Do not conclude until concrete evidence is collected."
)

PROMPT_STRATEGY_RECOVERY_CONTINUE = (
    "Continue execution using a different tool strategy from previous attempts. "
    "Do not conclude until you have concrete code evidence from tool outputs."
)

PROMPT_INSUFFICIENT_EVIDENCE_CONTINUE = (
    "Your current evidence is insufficient. Continue by calling tools with a "
    "different strategy and collect concrete evidence before concluding."
)
