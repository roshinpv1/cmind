from __future__ import annotations

import os

# Minimum tool calls before we accept a prose response as a final conclusion
# for repo-scoped analysis tasks. Set via env var to allow tuning.
MIN_REPO_TOOL_CALLS = int(os.getenv("CODEMIND_MIN_REPO_TOOL_CALLS", "3"))

# Phrases that indicate the LLM is writing a planning/transition paragraph
# rather than a final conclusion. If we see these in a prose-only response
# after very few tool calls, we force the agent to keep executing.
PLANNING_PHRASES = (
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
    "methodology for future analysis",
    "does not contain the results",
    "it is not possible to provide a detailed analysis",
    "data does not contain any information",
)

# Phrases that indicate the LLM gave up after one or two failing tool calls
# instead of trying alternative approaches. When tool_calls_made is very low
# we treat these the same as planning phrases — the agent must keep going.
GIVING_UP_PHRASES = (
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


def looks_like_planning(text: str) -> bool:
    """Return True if *text* reads as an interim plan rather than a final conclusion."""
    t = text.lower()
    return any(phrase in t for phrase in PLANNING_PHRASES)


def should_force_continuation(text: str, tool_calls_made: int) -> bool:
    """Return True if the agent should be forced to keep exploring.

    Two situations warrant a retry:
    1. Planning prose — the agent described future work instead of doing it.
    2. Premature give-up — the agent tried one tool, got no results, and stopped.
       Only applies when very few tools have been called (< 2), because a genuine
       conclusion after thorough exploration is fine.
    """
    if looks_like_planning(text):
        return True
    if tool_calls_made < 2:
        t = text.lower()
        return any(phrase in t for phrase in GIVING_UP_PHRASES)
    return False
