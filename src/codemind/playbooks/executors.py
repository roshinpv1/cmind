"""
PlaybookExecutor — single, unified execution path for every playbook.

All playbooks run through the same ReAct loop regardless of their
search_strategy.mode.  The mode only influences two things:

  react          The agent starts with an empty context and discovers
                 code via graph/search tools.  The agent stops when it
                 has enough evidence — max_iterations is just a safety
                 ceiling, not a behavioral target.

  catalog /      The executor pre-seeds the system prompt with vector-
  semantic /     search results so the agent has immediate context and
  hybrid         typically finishes in fewer turns.

Stopping behaviour
──────────────────
The agent exits the loop naturally when it produces a response with no
new tool calls.  max_iterations is a safety net against infinite loops
and runaway API cost — it is NOT a measure of how thorough the agent
should be.  Playbooks do NOT need to specify max_iterations; the global
default (CODEMIND_REACT_MAX_ITERATIONS, default 50) is intentionally
generous so the agent always has room to explore fully.

Parallel execution safety
─────────────────────────
Every execution creates its own isolated objects:
  • ToolDispatcher   (one per execute() call)
  • ReActAgent       (one per execute() call)
  • ContextCompactor (one per execute() call)
  • LangChain tools  (new bind_tools() call → new CmindChatModelWithTools)

The only shared state is:
  • self.registry / self.tools / self.llm  — all read-only
  • self._chat_model                       — lazy-init, structurally
                                             thread-safe (see note below)
"""

from __future__ import annotations

import json
import logging
import os
import re
import traceback
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .structured_schemas import get_schema_for_playbook, generate_example_json
from .privacy import privacy_filter
from .code_data_fetcher import CodeDataFetcher
from .runtime_core import PlaybookResultMapper
from .agent_loop import ReActAgent, AgentResult
from .tool_dispatch import ToolDispatcher

logger = logging.getLogger(__name__)

# ── Tuning ────────────────────────────────────────────────────────────────────
# Safety ceiling — agent exits naturally before this in normal operation.
# Raised to 50 so deep-analysis playbooks are never cut off prematurely.
# Override per-deployment with the env var; playbooks should NOT hardcode this.
_REACT_DEFAULT_ITERS   = int(os.getenv("CODEMIND_REACT_MAX_ITERATIONS", "50"))
# Seeded playbooks already have context injected — they typically finish in 2-4
# turns, but give them a bit more room so they can still call tools if needed.
_SEEDED_DEFAULT_ITERS  = int(os.getenv("CODEMIND_SEEDED_MAX_ITERATIONS", "50"))
# max chars to inject from pre-seeded vector search
_SEED_MAX_CHARS        = int(os.getenv("CODEMIND_SEED_MAX_CHARS", "40000"))

_EXPLORATION_MODES = frozenset({"react"})
_SEED_MODES        = frozenset({"catalog", "semantic", "hybrid"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log_llm_error(
    error: Exception,
    *,
    playbook_name: str = "",
    context: str = "",
) -> None:
    """Write a failed LLM request to /tmp/llm_errors/ for post-mortem."""
    import datetime

    error_dir = "/tmp/llm_errors"
    os.makedirs(error_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        with open(f"{error_dir}/llm_error_{ts}.json", "w") as fh:
            json.dump(
                {
                    "timestamp":   ts,
                    "error_type":  type(error).__name__,
                    "error":       str(error),
                    "playbook":    playbook_name,
                    "context":     context,
                    "traceback":   traceback.format_exc(),
                },
                fh, indent=2, default=str,
            )
        logger.warning("LLM error logged to %s/llm_error_%s.json", error_dir, ts)
    except Exception as log_err:
        logger.warning("Failed to log LLM error: %s", log_err)




def _extract_changed_files_claim(answer: str) -> list[str]:
    """Best-effort parse of claimed changed files from final JSON-style answer."""
    text = (answer or "").strip()
    if not text:
        return []

    candidates: list[str] = []
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)

    for raw in candidates:
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict):
            changed = obj.get("changed_files")
            if isinstance(changed, list):
                return [str(p) for p in changed if str(p).strip()]
    return []


# ── PlaybookExecutor ──────────────────────────────────────────────────────────

class PlaybookExecutor:
    """
    Execute any playbook against a codebase.

    All playbooks run through *one* code path: _execute().
    Isolation between concurrent runs is guaranteed because every call
    creates fresh ToolDispatcher / ReActAgent / ContextCompactor instances.
    """

    def __init__(self, registry, tools, llm_client) -> None:
        self.registry       = registry
        self.tools          = tools
        self.llm            = llm_client
        # CmindChatModel is a stateless wrapper — safe to share.
        # Two concurrent calls that both see None will each create an
        # instance; one will be discarded.  No mutation after creation.
        self._chat_model    = None
        self._data_fetcher  = CodeDataFetcher(self.tools)
        self._result_mapper = PlaybookResultMapper()

    # ── lazy chat model (stateless wrapper, safe to share) ───────────────────

    def _get_chat_model(self):
        if self._chat_model is None:
            from ..llm.chat_wrapper import CmindChatModel
            self._chat_model = CmindChatModel(driver=self.llm)
        return self._chat_model

    # ── public entry point ────────────────────────────────────────────────────

    async def execute(self, playbook_name: str, user_input: dict) -> dict:
        """
        Execute *playbook_name* and return a stable result envelope:
          {success, outputs: {result, data, playbook, context, ...}, error, logs}
        """
        playbook = self.registry.get_playbook(playbook_name)
        if not playbook:
            return self._result_mapper.map_result(
                playbook_name=playbook_name,
                success=False,
                outputs={},
                error=f"Playbook not found: {playbook_name}",
                logs=[f"Playbook not found: {playbook_name}"],
            )

        user_input = await self._enrich_repo_metadata(user_input)

        try:
            raw = await self._execute(playbook, playbook_name, user_input)
        except Exception as exc:
            traceback.print_exc()
            _log_llm_error(exc, playbook_name=playbook_name, context="outer execute")
            raw = {
                "success": False,
                "outputs": {},
                "error":   str(exc),
                "logs":    [f"Execution error: {exc}"],
            }

        return self._result_mapper.map_result(
            playbook_name=playbook_name,
            success=bool(raw.get("success")),
            outputs=raw.get("outputs"),
            error=raw.get("error"),
            logs=raw.get("logs"),
        )

    # ── unified execution ─────────────────────────────────────────────────────

    async def _execute(self, playbook, playbook_name: str, user_input: dict) -> dict:
        """
        Single execution path for ALL playbooks.

        Steps:
          1. Determine max_iterations from playbook config / mode.
          2. Pre-seed vector search context for non-exploration playbooks.
          3. Build the per-execution isolated tool set + agent.
          4. Run the ReAct loop and return a raw result dict.
        """
        mode     = getattr(playbook.search_strategy, "mode", "") or ""
        is_seeded = mode in _SEED_MODES

        # ── 1. max_iterations ───────────────────────────────────────────────
        # The agent exits naturally (no-tool-call response) in normal operation.
        # max_iterations is only a safety ceiling — playbooks don't need to set it.
        # If a playbook does set it, honour it; otherwise use the global default.
        _pb_max = int(getattr(playbook, "max_iterations", 0) or 0)
        if _pb_max:
            max_iter = _pb_max
            logger.debug("max_iterations from playbook: %d", max_iter)
        else:
            max_iter = _SEEDED_DEFAULT_ITERS if is_seeded else _REACT_DEFAULT_ITERS
            logger.debug("max_iterations from default (%s): %d",
                         "seeded" if is_seeded else "react", max_iter)

        # ── 2. pre-seed context (catalog / semantic / hybrid playbooks) ──────
        seed_context = ""
        if is_seeded:
            seed_context = await self._build_seed_context(playbook, user_input)

        # ── 3. system prompt ─────────────────────────────────────────────────
        repo_id_for_prompt = user_input.get("repo_id")
        if repo_id_for_prompt in ("latest", ["latest"]):
            repo_id_for_prompt = None
        if isinstance(repo_id_for_prompt, list):
            repo_id_for_prompt = repo_id_for_prompt[0] if repo_id_for_prompt else None
        output_schema_model = get_schema_for_playbook(
            playbook.name, playbook_def=playbook
        )
        sys_prompt = self._build_system_prompt(
            playbook, seed_context, repo_id=repo_id_for_prompt
        )

        # ── 4. per-call isolated objects ─────────────────────────────────────
        from .langchain_tools import create_langchain_tools
        from codemind.llm.context_manager import ContextCompactor

        enforced_repo_id = user_input.get("repo_id")
        if enforced_repo_id in ("latest", ["latest"]):
            enforced_repo_id = None
        execution_context = user_input.get("execution_context") if isinstance(user_input, dict) else None
        enforced_mirror_root = None
        prefer_mirror_reads = False
        if isinstance(execution_context, dict):
            enforced_mirror_root = execution_context.get("mirror_root")
            prefer_mirror_reads = bool(
                execution_context.get("prefer_mirror_reads", bool(enforced_mirror_root))
            )

        lc_tools       = create_langchain_tools(self.tools, enforced_repo_id=enforced_repo_id)
        tool_names     = {t.name for t in lc_tools}
        llm_with_tools = self._get_chat_model().bind_tools(lc_tools)  # new instance per call
        compactor      = ContextCompactor(llm_driver=self.llm, threshold_ratio=0.6)
        dispatcher     = ToolDispatcher(
            self.tools,
            tool_names,
            enforced_repo_id=enforced_repo_id,
            enforced_mirror_root=enforced_mirror_root,
            prefer_mirror_reads=prefer_mirror_reads,
        )
        agent          = ReActAgent(
            llm_driver=self.llm,
            llm_with_tools=llm_with_tools,
            tool_dispatcher=dispatcher,
            compactor=compactor,
            repo_id=enforced_repo_id,
        )

        # ── 5. goal message ──────────────────────────────────────────────────
        goal = user_input.get("goal") or user_input.get("query") or ""

        repo_id_str: str | None = (
            enforced_repo_id if isinstance(enforced_repo_id, str)
            else (enforced_repo_id[0] if enforced_repo_id else None)
        )
        if repo_id_str:
            goal += f"\n\nRepository ID: {repo_id_str}"
        ctx = user_input.get("context")
        if ctx:
            goal += f"\n\nRepository info: {json.dumps(ctx, default=str)}"
        goal = privacy_filter.mask(goal)

        # ── 6. graph prefetch (ALL repo-scoped playbooks) ────────────────────
        # Run for both exploration (react) and seeded (catalog/semantic/hybrid)
        # playbooks whenever a repo_id is available, so every repo analysis
        # starts with concrete graph data rather than a cold start.
        prefetch_block = ""
        if repo_id_str:
            goal_hint = user_input.get("goal") or user_input.get("query") or ""
            try:
                prefetch = self._data_fetcher.build_graph_prefetch(
                    repo_id=repo_id_str, goal=goal_hint, limit=12
                )
                prefetch_block = self._data_fetcher.to_prompt_block(prefetch)
            except Exception as exc:
                prefetch_block = (
                    f"\n### GRAPHIFY PREFLIGHT\n"
                    f"Prefetch unavailable ({exc}). Use get_map manually.\n"
                )

        # ── 7. run ───────────────────────────────────────────────────────────
        logger.info(
            "Executing playbook '%s' | mode=%s | max_iter=%d | seeded=%s",
            playbook_name, mode, max_iter, is_seeded,
        )
        result: AgentResult = await agent.run(
            goal=goal,
            system_prompt=sys_prompt,
            prefetch_block=prefetch_block,
            max_iterations=max_iter,
            playbook_name=playbook_name,
            output_type=getattr(playbook, "output_type", "") or "",
            output_schema_model=output_schema_model,
        )

        logs = list(result.logs)
        logs.append(
            f"Completed: iterations={result.iterations} tool_calls={result.tool_calls_made}"
        )

        generated_files = list(dict.fromkeys(result.generated_files))
        effective_error = result.error
        claimed_changed_files = _extract_changed_files_claim(result.answer)

        # Integrity check: only flag if the model explicitly claims file writes in its
        # answer but none actually landed on disk (i.e. false success narrative).
        # This preserves agent autonomy — we do NOT force writes for non-write tasks.
        if claimed_changed_files and not generated_files and not effective_error:
            logs.append(
                f"Note: model claimed {len(claimed_changed_files)} changed file(s) "
                "but no write tool calls were observed. "
                "Files may not have been persisted to mirror workspace."
            )

        return {
            "success": effective_error is None,
            "outputs": {
                "result":        result.answer,
                "data":          None,
                "tool_executed": result.tool_calls_made > 0,
                "tool_result":   None,
                "iterations":    result.iterations,
                "playbook":      playbook_name,
                "generated_files": generated_files,
                "claimed_changed_files": claimed_changed_files,
            },
            "error": effective_error,
            "logs":  logs,
        }

    # ── seed context builder ──────────────────────────────────────────────────

    async def _build_seed_context(self, playbook, user_input: dict) -> str:
        """
        Pre-run vector / catalog search for seeded playbooks.
        Formats results as a compact text block to inject into the system prompt.
        Returns empty string if nothing was found.
        """
        strategy   = playbook.search_strategy
        repo_id    = user_input.get("repo_id")
        mode       = getattr(strategy, "mode", "semantic")

        queries: list[str] = []
        if hasattr(strategy, "phases") and strategy.phases:
            for phase in strategy.phases:
                if isinstance(phase, dict):
                    queries.extend(phase.get("queries", []))
        elif hasattr(strategy, "queries") and strategy.queries:
            queries = list(strategy.queries)
        if not queries:
            q = user_input.get("goal") or user_input.get("query") or ""
            if q:
                queries = [q]
        if not queries:
            return ""

        params = {
            "queries":    queries,
            "repo_id":    repo_id,
            "limit":      getattr(strategy, "limit", 15),
            "mode":       "hybrid" if mode != "catalog" else "catalog",
            "file_types": getattr(strategy, "file_types", []),
            "min_score":  getattr(strategy, "min_score", 0.0),
        }

        try:
            if mode == "catalog":
                result = await self.tools.search_catalogs(params)
            else:
                result = await self.tools.search_codebase(params)

            if not result.get("success"):
                return ""

            chunks = result.get("results", [])
            if not chunks:
                return ""

            # Filter test files if requested
            if getattr(playbook, "exclude_test_files", False):
                pat = re.compile(
                    r"(^|/)tests?/|/test_[^/]+$|/_test\.py$|/conftest\.py$|"
                    r"/testing/|\.test\.(js|ts|jsx|tsx)$|\.spec\.(js|ts|jsx|tsx)$|"
                    r"__tests__/",
                    re.IGNORECASE,
                )
                chunks = [c for c in chunks if not pat.search(c.get("file_path", ""))]

            from .token_utils import format_code_chunks_for_llm
            block = format_code_chunks_for_llm(chunks, max_tokens=_SEED_MAX_CHARS // 4)
            if not block.strip():
                return ""

            return (
                "\n\n### PRE-RETRIEVED CONTEXT\n"
                "The following code was retrieved from the repository using semantic search. "
                "Use it as your primary evidence. Call tools only if you need additional "
                "information beyond what is shown here.\n\n"
                + block
            )

        except Exception as exc:
            logger.warning("Seed context retrieval failed: %s", exc)
            return ""

    # ── system prompt builder ─────────────────────────────────────────────────

    # Grouped descriptions of available repository analysis tools.
    _REPO_TOOLS_HINT = """
### AVAILABLE REPOSITORY ANALYSIS TOOLS
When a repo_id is provided you have access to the following tools. Use them to
gather real evidence before drawing conclusions.

**Architecture & Graph**
- `get_map` — high-level architecture map: top-connected nodes, entry points, clusters.
  Start here for any repo-scoped analysis.
- `get_file_outline` — class/function outline of a file without reading the full source.
- `get_dependencies` — imports and reverse-imports for a file (`direction=imports|imported_by`).
- `get_callers` — all callers of a function (who calls it).
- `get_callees` — all callees of a function (what it calls).
- `trace_path` — shortest call/import path between two symbols.

**Code Search**
- `search_code` — semantic + keyword hybrid search across the indexed codebase.
  Use targeted queries; batch multiple patterns in one call.
- `search_symbol` — find a class or function by exact name.
- `grep_search` — regex search across the raw source files.

**File Reading**
- `read_file` — read a file by path and repo_id (supports line ranges).
  Call `get_file_outline` first on large files to pick the right range.
- `list_files` — list files in the repo matching a glob pattern.

**Tip:** build a mental map with `get_map` → identify suspects → confirm with
`read_file` + `get_callers`/`get_callees` → synthesise findings.
"""

    def _build_system_prompt(
        self,
        playbook,
        seed_context: str = "",
        repo_id: str | None = None,
    ) -> str:
        """
        Assemble the base system prompt.
        Applies to ALL playbooks through the unified path.
        """
        prompt = playbook.system_prompt or ""

        if playbook.anti_patterns:
            prompt += "\n\n### ANTI-PATTERNS (NEVER DO THESE)\n"
            for ap in playbook.anti_patterns:
                prompt += f"- {ap}\n"

        if playbook.quality_rubric:
            prompt += "\n### QUALITY CRITERIA\n"
            for r in playbook.quality_rubric:
                prompt += (
                    f"- **{r.get('criterion', '')}** ({r.get('weight', '')}): "
                    f"{r.get('pass_condition', '')}\n"
                )

        if playbook.examples:
            ex = playbook.examples[0]
            prompt += "\n### EXAMPLE OUTPUT FORMAT\n"
            prompt += f"Query: \"{ex.get('input', '')}\"\n"
            prompt += f"Output:\n```json\n{ex.get('output', '{}')}\n```\n"
            prompt += "(Match this format but use REAL data from the codebase.)\n"

        # JSON output schema injection
        output_schema = get_schema_for_playbook(playbook.name, playbook_def=playbook)
        if output_schema and getattr(playbook, "output_type", "") == "json_response":
            example = generate_example_json(output_schema)
            if example:
                prompt += (
                    "\n\n### REQUIRED OUTPUT FORMAT\n"
                    "Your final response MUST be a JSON object matching this structure "
                    "(use REAL values from the code — never return placeholder/empty fields):\n"
                    f"```json\n{example}\n```\n"
                )

        # Tool-call output: tell the agent to call the target tool when done
        if getattr(playbook, "output_type", "") == "tool_call" and getattr(playbook, "tool_name", ""):
            prompt += (
                f"\n\n### EXECUTION REQUIREMENT\n"
                f"When your analysis is complete, you MUST call the `{playbook.tool_name}` "
                "tool with the structured result. Do not stop without calling it.\n"
            )

        # Repo metadata injection
        if getattr(playbook, "inject_repo_metadata", False):
            prompt += (
                "\n\n### REPOSITORY METADATA\n"
                "If repository metadata (name, URL, branch, author) is provided in the "
                "user message, use those exact values in your output.\n"
            )

        # Grounding rule
        if getattr(playbook, "grounding_fence", False):
            prompt += (
                "\n\n### GROUNDING RULE\n"
                "You MAY ONLY use information that was directly observed via tools or "
                "provided in the pre-retrieved context below. Do NOT invent, guess, or "
                "recall anything from training data.\n"
            )

        # Repo analysis tools hint — tell the agent what tools are available
        # and how to use them effectively for deep codebase exploration.
        if repo_id:
            prompt += self._REPO_TOOLS_HINT

        # Pre-seeded context (catalog / semantic / hybrid playbooks)
        if seed_context:
            prompt += seed_context

        return prompt

    # ── metadata enrichment ───────────────────────────────────────────────────

    async def _enrich_repo_metadata(self, user_input: dict) -> dict:
        """Attach repository metadata from the manifest DB when not already present."""
        if user_input.get("context") or not user_input.get("repo_id"):
            return user_input
        repo_id = user_input["repo_id"]
        try:
            db = getattr(self.tools, "db", None)
            if not db:
                return user_input
            from ..storage.models import RepositoryManifest
            with db.get_session() as session:
                repo = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()
                if repo:
                    name = (repo.repo_path or "").rstrip("/").split("/")[-1] or "unknown"
                    user_input = dict(user_input)
                    user_input["context"] = {
                        "repo_id":         repo.repo_id,
                        "name":            name,
                        "repo_url":        repo.repo_url or "",
                        "branch":          repo.branch or "main",
                        "path":            repo.repo_path or "",
                        "first_author":    repo.first_author or "",
                        "total_commits":   repo.total_commits or 0,
                        "last_pr_title":   repo.last_pr_title or "",
                        "last_pr_user":    repo.last_pr_user or "",
                        "last_indexed":    (
                            repo.last_indexed_at.isoformat()
                            if repo.last_indexed_at else ""
                        ),
                    }
                    logger.info(
                        "Enriched repo metadata: name=%s url=%s branch=%s",
                        name, repo.repo_url, repo.branch,
                    )
        except Exception as exc:
            logger.warning("Failed to fetch repo metadata: %s", exc)
        return user_input
