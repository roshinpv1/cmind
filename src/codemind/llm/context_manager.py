"""
Goose-style context management and compaction for long-running LLM sessions.

Provides utilities to keep LLM context within token limits by proactively
summarizing history and truncating large tool outputs.
"""

from typing import List, Optional, Any
import copy
import json
import logging
import os
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from codemind.llm.token_counter import count_tokens, get_context_window

logger = logging.getLogger(__name__)

# Tools that return compact structural data (Graphify / graph queries) — allow larger budgets than raw search dumps.
_GRAPHIFY_STRUCTURE_TOOLS = frozenset({
    "get_map",
    "trace_path",
    "get_file_outline",
    "search_symbol",
    "get_callers",
    "get_callees",
    "get_dependencies",
    "list_files",
    "list_repo_directory",
})

_TEXT_SEARCH_TOOLS = frozenset({"search_code", "grep_search", "search_codebase"})

_LIST_SHRINK_KEYS = (
    "results",
    "files",
    "symbols",
    "callers",
    "callees",
    "dependencies",
    "entries",
    "top_nodes",
    "path",
    "catalog_matches",
)


def tool_output_budget_chars(tool_name: Optional[str]) -> int:
    """Max serialized tool payload size before truncation (Claude Code–style tiering)."""
    if not tool_name:
        return int(os.getenv("CODEMIND_TOOL_OUTPUT_DEFAULT", "10000"))
    t = tool_name.lower()
    if t in _GRAPHIFY_STRUCTURE_TOOLS:
        return int(os.getenv("CODEMIND_TOOL_OUTPUT_GRAPH", "18000"))
    if t in _TEXT_SEARCH_TOOLS:
        return int(os.getenv("CODEMIND_TOOL_OUTPUT_SEARCH", "14000"))
    if t == "read_file":
        return int(os.getenv("CODEMIND_TOOL_OUTPUT_READ", "32000"))
    return int(os.getenv("CODEMIND_TOOL_OUTPUT_DEFAULT", "10000"))


def _shrink_dict_lists(data: dict[str, Any], per_list_cap: int) -> dict[str, Any]:
    out = copy.deepcopy(data)
    for key in _LIST_SHRINK_KEYS:
        val = out.get(key)
        if isinstance(val, list) and len(val) > per_list_cap:
            out[key] = val[:per_list_cap]
            out[f"_{key}_truncated"] = len(val) - per_list_cap
    return out


def sanitize_tool_output_for_tool(
    tool_name: Optional[str],
    content: str,
    max_chars: Optional[int] = None,
) -> str:
    """
    Truncate or structurally shrink tool JSON/text so ReAct/planner context stays usable.

    Graphify-first tools get a higher char budget; text search (grep_search / search_code) gets a separate tier.
    """
    budget = max_chars if max_chars is not None else tool_output_budget_chars(tool_name)
    if not content or len(content) <= budget:
        return content

    logger.debug(
        "Sanitizing tool output tool=%s size=%s budget=%s",
        tool_name,
        len(content),
        budget,
    )

    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if "results" in data and isinstance(data["results"], list):
                count = len(data["results"])
                truncated_data = {
                    "count": count,
                    "results": data["results"][: min(5, count)],
                    "note": f"Full result was {len(content)} chars; showing first {min(5, count)} result(s).",
                }
                blob = json.dumps(truncated_data, indent=2)
                if len(blob) <= budget:
                    return blob

            for cap in (40, 20, 10, 5):
                shrunk = _shrink_dict_lists(data, cap)
                blob = json.dumps(shrunk, default=str, indent=2)
                if len(blob) <= budget:
                    return blob
            blob = json.dumps(shrunk, default=str, indent=2)
            if len(blob) > budget:
                return blob[:budget] + "\n... [TRUNCATED JSON]"
            return blob
    except json.JSONDecodeError:
        pass

    return (
        f"--- LARGE OUTPUT TRUNCATED ({len(content)} chars) ---\n"
        f"{content[:budget]}\n"
        f"... [TRUNCATED] ..."
    )

class ContextCompactor:
    """Proactively manages LLM message history to prevent token overflow."""
    
    def __init__(self, llm_driver=None, threshold_ratio: float = 0.6):
        """
        Initialize the compactor.
        
        Args:
            llm_driver: The LLM model used for summarization (usually the same as the main agent).
            threshold_ratio: Token ratio (relative to context window) that triggers compaction.
        """
        self.llm = llm_driver
        self.threshold_ratio = threshold_ratio

    async def compact(self, messages: List[BaseMessage], window_size: Optional[int] = None) -> List[BaseMessage]:
        """
        Proactively compact history if it exceeds the token threshold.

        Strategy:
          - Always sanitize individual tool payloads first (no LLM needed).
          - If context fits after sanitization → return as-is.
          - If context is still too large AND window is big enough (>= 16K tokens)
            → call LLM to summarize the middle section.
          - Otherwise → pure truncation (keep first human + last N messages).
            This avoids burning LLM calls for summarization on small local models.
        """
        if not messages:
            return messages

        # ── step 1: sanitize individual tool payloads ──────────────────────
        sanitized: List[BaseMessage] = []
        for m in messages:
            if isinstance(m, ToolMessage):
                name = getattr(m, "name", None)
                if name and m.content is not None:
                    new_content = sanitize_tool_output_for_tool(name, str(m.content))
                    if new_content != m.content:
                        sanitized.append(
                            ToolMessage(
                                content=new_content,
                                tool_call_id=m.tool_call_id,
                                name=name,
                            )
                        )
                        continue
            sanitized.append(m)
        messages = sanitized

        window_size = window_size or get_context_window()
        threshold_tokens = int(window_size * self.threshold_ratio)

        total_text   = "".join(str(m.content) for m in messages)
        total_tokens = count_tokens(total_text)

        if total_tokens <= threshold_tokens:
            return messages

        logger.info(
            "[COMPACTOR] Context threshold hit (%d/%d tokens). Compacting...",
            total_tokens, window_size,
        )

        # ── step 2: always try truncation first — zero extra LLM calls ─────
        system_msg  = messages[0] if isinstance(messages[0], SystemMessage) else None
        first_human = next((m for m in messages if isinstance(m, HumanMessage)), None)
        recent      = messages[-8:]

        truncated = []
        if system_msg:
            truncated.append(system_msg)
        if first_human and first_human not in truncated:
            truncated.append(first_human)
        for m in recent:
            if m not in truncated:
                truncated.append(m)

        truncated_text   = "".join(str(m.content) for m in truncated)
        truncated_tokens = count_tokens(truncated_text)

        # If truncation got us under budget OR window is small (< 16K), use it directly
        _LLM_SUMMARY_MIN_WINDOW = int(os.getenv("CODEMIND_SUMMARY_MIN_WINDOW", "16000"))
        if truncated_tokens <= threshold_tokens or window_size < _LLM_SUMMARY_MIN_WINDOW:
            logger.info(
                "[COMPACTOR] Truncation: %d → %d messages (%d tokens).",
                len(messages), len(truncated), truncated_tokens,
            )
            return truncated

        # ── step 3: LLM summarization only for large-context models ─────────
        if not self.llm:
            return truncated

        to_summarize = messages[1:-8] if system_msg else messages[:-8]
        if not to_summarize:
            return truncated

        conv_text = "".join(
            f"{'User' if isinstance(m, HumanMessage) else 'Assistant' if isinstance(m, AIMessage) else 'Tool'}: "
            f"{str(m.content)[:2000]}\n"
            for m in to_summarize
        )
        summary_prompt = (
            "Summarize the conversation history. Focus on tools called, data found, "
            "and the current hypothesis. Be concise (2-3 paragraphs).\n\n"
            "HISTORY:\n" + conv_text
        )
        try:
            summary_content = await self.llm.generate(
                summary_prompt,
                system_prompt="You are a session management assistant. Summarize accurately.",
                max_tokens=512,
            )
            result = []
            if system_msg:
                result.append(system_msg)
            if first_human:
                result.append(first_human)
            result.append(HumanMessage(
                content=f"\n### SUMMARY OF PREVIOUS STEPS ###\n{summary_content}\n"
            ))
            result.extend(recent)
            logger.info(
                "[COMPACTOR] LLM summary: %d → %d messages.", len(messages), len(result)
            )
            return result
        except Exception as exc:
            logger.warning("[COMPACTOR] LLM summarization failed (%s). Using truncation.", exc)
            return truncated

def sanitize_tool_output(content: str, max_chars: int = 4000) -> str:
    """
    Prevents massive tool outputs from bloating context upfront.
    
    If content exceeds limit, returns a truncated version with a metadata summary.
    """
    return sanitize_tool_output_for_tool(None, content, max_chars=max_chars)
