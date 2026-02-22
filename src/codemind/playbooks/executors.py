"""
Playbook Executor - Prompt-based playbook execution.

New architecture:
1. Linear mode (default): search → LLM → format → END
2. ReAct mode (code_explorer): agent ↔ tools loop until done

All playbooks use same executor, different modes.
"""

from typing import TypedDict, Annotated, Optional, Any
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import operator
import json
import traceback
import re as _re

from .structured_schemas import get_schema_for_playbook


def _repair_json(raw: str) -> dict | None:
    """Attempt to fix common LLM JSON errors and return parsed dict, or None."""
    s = raw
    # 1. Remove single-line // comments
    s = _re.sub(r'//.*?$', '', s, flags=_re.MULTILINE)
    # 2. Fix arrays where items have embedded unquoted parens like:
    #    "/path" (description)",  ->  "/path (description)",
    s = _re.sub(r'"\s*\(', ' (', s)
    # 3. Fix broken array items: VALUE"  -> "VALUE"
    #    Pattern: a quote, comma/newline, then text without opening quote
    # 4. Remove trailing commas before } or ]
    s = _re.sub(r',\s*([}\]])', r'\1', s)
    # 5. Try to balance braces - find the outermost matching pair
    depth = 0
    start = None
    end = None
    for i, c in enumerate(s):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if start is not None and end is not None:
        s = s[start:end]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 6. More aggressive: strip all non-ASCII and retry
    s_clean = ''.join(c for c in s if ord(c) < 128)
    try:
        return json.loads(s_clean)
    except json.JSONDecodeError:
        pass
    return None


class PlaybookExecutionState(TypedDict):
    """State for playbook execution workflow."""
    playbook_name: str
    user_input: dict
    code_chunks: list[dict]
    llm_output: str
    outputs: dict
    error: Optional[str]
    logs: Annotated[list[str], operator.add]


class ReactExecutionState(TypedDict):
    """State for ReAct-style playbook execution (message-based)."""
    messages: Annotated[list, add_messages]
    playbook_name: str
    user_input: dict
    iteration: int
    max_iterations: int
    outputs: dict
    error: Optional[str]
    logs: Annotated[list[str], operator.add]


class ContextPacker:
    """Intelligently packs code chunks into LLM context window.
    
    Prioritizes by relevance score, deduplicates, and prevents overflow.
    """

    def __init__(self, max_chars: int = 60_000):
        """
        Initialize context packer.

        Args:
            max_chars: Maximum characters for code context
        """
        self.max_chars = max_chars

    def pack(self, chunks: list[dict], query: str = "") -> str:
        """
        Pack code chunks into a context string within budget.

        Args:
            chunks: Code chunks with score, file_path, chunk_text
            query: Optional query for relevance context

        Returns:
            Formatted context string within max_chars
        """
        if not chunks:
            return ""

        # Sort by score (highest first)
        sorted_chunks = sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)

        # Deduplicate by content hash
        seen = set()
        unique_chunks = []
        for chunk in sorted_chunks:
            content_key = chunk.get("chunk_text", "")[:100]
            if content_key not in seen:
                seen.add(content_key)
                unique_chunks.append(chunk)

        # Pack within budget
        context_parts = []
        char_count = 0

        for chunk in unique_chunks:
            text = chunk.get("chunk_text", "")
            file_path = chunk.get("file_path", "unknown")
            start_line = chunk.get("start_line", "?")
            end_line = chunk.get("end_line", "?")

            header = f"\n--- {file_path} (lines {start_line}-{end_line}) ---\n"
            entry = header + text

            if char_count + len(entry) > self.max_chars:
                # Budget exceeded
                remaining = self.max_chars - char_count
                if remaining > 100:
                    context_parts.append(entry[:remaining] + "\n... [truncated]")
                break

            context_parts.append(entry)
            char_count += len(entry)

        return "\n".join(context_parts)

    def summarize_overflow(self, chunks: list[dict], packed_count: int) -> str:
        """Generate a summary of chunks that didn't fit."""
        overflow = chunks[packed_count:]
        if not overflow:
            return ""

        files = set(c.get("file_path", "") for c in overflow)
        return f"\n[{len(overflow)} additional chunks from {len(files)} files were not included due to context limits]"


class PlaybookExecutor:
    """
    Execute playbooks via LangGraph workflows.
    
    Supports two modes:
    1. Linear (default): search → LLM → format → END
    2. ReAct (code_explorer): agent ↔ tools cyclic loop
    """
    
    def __init__(self, registry, tools, llm_client):
        """
        Initialize playbook executor.
        
        Args:
            registry: PlaybookRegistry instance
            tools: PlaybookTools instance
            llm_client: LLM driver for generation
        """
        self.registry = registry
        self.tools = tools
        self.llm = llm_client
        self._chat_model = None  # Lazy init for ReAct mode
    
    def _get_chat_model(self):
        """Get or create CmindChatModel wrapper for ReAct mode."""
        if self._chat_model is None:
            from ..llm.chat_wrapper import CmindChatModel
            self._chat_model = CmindChatModel(driver=self.llm)
        return self._chat_model
    
    async def execute(self, playbook_name: str, user_input: dict) -> dict:
        """
        Execute a playbook.
        
        Args:
            playbook_name: Name of playbook to execute
            user_input: User's input (e.g., {"goal": "explain auth.py", "repo_id": "123"})
            
        Returns:
            {
                success: bool,
                outputs: dict,
                error: str (if failed),
                logs: list[str]
            }
        """
        # Get playbook definition
        playbook = self.registry.get_playbook(playbook_name)
        if not playbook:
            return {
                "success": False,
                "outputs": {},
                "error": f"Playbook not found: {playbook_name}",
                "logs": [f"Playbook not found: {playbook_name}"]
            }
        
        # Enrich user_input with repo metadata from SQLite if not already present
        if not user_input.get("context") and user_input.get("repo_id"):
            repo_id = user_input["repo_id"]
            try:
                db = getattr(self.tools, 'db', None)
                if db:
                    from ..storage.models import RepositoryManifest
                    with db.get_session() as session:
                        repo = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()
                        if repo:
                            # Extract repo name from path (e.g., "/path/to/org/repo" → "repo")
                            repo_name = repo.repo_path.rstrip('/').split('/')[-1] if repo.repo_path else "unknown"
                            user_input["context"] = {
                                "repo_id": repo.repo_id,
                                "name": repo_name,
                                "repo_url": repo.repo_url or "",
                                "branch": repo.branch or "main",
                                "path": repo.repo_path or "",
                                "first_author": repo.first_author or "",
                                "total_commits": repo.total_commits or 0,
                                "last_pr_title": repo.last_pr_title or "",
                                "last_pr_user": repo.last_pr_user or "",
                                "last_pr_merged_at": repo.last_pr_merged_at or "",
                                "last_indexed": repo.last_indexed_at.isoformat() if repo.last_indexed_at else "",
                            }
                            print(f"[EXECUTOR] Enriched with repo metadata: name={repo_name}, url={repo.repo_url}, branch={repo.branch}")
                        else:
                            print(f"[EXECUTOR] ⚠️ No manifest found for repo_id={repo_id}")
            except Exception as e:
                print(f"[EXECUTOR] ⚠️ Failed to fetch repo metadata: {e}")
        
        # Route: ReAct mode or Linear mode
        is_react = getattr(playbook.search_strategy, 'mode', '') == 'react'
        
        if is_react:
            return await self._execute_react(playbook, playbook_name, user_input)
        
        # --- Linear mode (existing pipeline) ---
        workflow = self._build_workflow(playbook)
        
        # Execute
        initial_state: PlaybookExecutionState = {
            "playbook_name": playbook_name,
            "user_input": user_input,
            "code_chunks": [],
            "llm_output": "",
            "outputs": {},
            "error": None,
            "logs": []
        }
        
        try:
            result = await workflow.ainvoke(initial_state)
            
            return {
                "success": result["error"] is None,
                "outputs": result["outputs"],
                "error": result["error"],
                "logs": result["logs"]
            }
        
        except Exception as e:
            print(f"[EXECUTOR] Playbook execution failed: {e}", flush=True)
            traceback.print_exc()
            return {
                "success": False,
                "outputs": {},
                "error": str(e),
                "logs": [f"Execution error: {str(e)}"]
            }
    
    def _build_workflow(self, playbook):
        """
        Build prompt-based workflow.
        
        Flow:
        search_code -> llm_generate -> format_output
        """
        graph = StateGraph(PlaybookExecutionState)
        
            # Node 1: Search for code
        async def search_code(state: PlaybookExecutionState):
            """
            Search for code using playbook's search strategy.
            Handles both simple queries and phases.
            """
            state["logs"].append(f"Running playbook: {playbook.name}")

            strategy = playbook.search_strategy
            user_input = state["user_input"]
            repo_id = user_input.get("repo_id")
            print(f"[EXECUTOR] Playbook '{playbook.name}' - search params: repo_id={repo_id}")
            
            # Extract queries - support both direct queries and phases
            queries = []
            
            # Check if strategy has phases (new format)
            if hasattr(strategy, 'phases') and strategy.phases:
                for phase in strategy.phases:
                    if isinstance(phase, dict):
                        phase_queries = phase.get('queries', [])
                        queries.extend(phase_queries)
            # Fallback to direct queries (old format)
            elif hasattr(strategy, 'queries') and strategy.queries:
                queries = strategy.queries
            
            # If no queries extracted, use goal as query
            if not queries:
                goal = user_input.get("goal", user_input.get("query", ""))
                if goal:
                    queries = [goal]
                else:
                    state["error"] = "No queries provided in search strategy or user input"
                    state["code_chunks"] = []
                    state["logs"].append(f"Search failed: {state['error']}")
                    return state
            
            # Build search params
            search_params = {
                "queries": queries,
                "repo_id": repo_id,
                "limit": getattr(strategy, "limit", 10),
                "mode": getattr(strategy, 'mode', 'semantic'),
                "file_types": getattr(strategy, 'file_types', []),
                "graph_filters": getattr(strategy, 'graph_filters', {}),
                "min_score": getattr(strategy, 'min_score', 0.0)
            }
            
            state["logs"].append(f"  Extracted {len(queries)} queries: {queries}")
            state["logs"].append(f"  Search mode: {search_params['mode']}, limit: {search_params['limit']}, min_score: {search_params['min_score']}")
            
            try:
                if search_params.get("mode") == "catalog":
                    result = await self.tools.search_catalogs(search_params)
                else:
                    result = await self.tools.search_codebase(search_params)
                
                print(f"[EXECUTOR] Search result: success={result.get('success')}, "
                      f"count={result.get('count', 0)}, "
                      f"results_len={len(result.get('results', []))}, "
                      f"repo_id={repo_id}")
                
                if result.get("success"):
                    chunks = result.get("results", [])
                    
                    if not chunks:
                        print(f"[EXECUTOR] ⚠️ Search returned 0 chunks for repo_id={repo_id} "
                              f"with queries={queries[:3]}... "
                              f"(mode={search_params['mode']}, limit={search_params['limit']})")
                    
                    # Data-driven: filter test files if playbook requests it
                    if playbook.exclude_test_files:
                        import re
                        test_pattern = re.compile(
                            r'(^|/)tests?/|/test_[^/]+$|/_test\.py$|/conftest\.py$|'
                            r'/testing/|\.test\.(js|ts|jsx|tsx)$|\.spec\.(js|ts|jsx|tsx)$|'
                            r'__tests__/',
                            re.IGNORECASE
                        )
                        before = len(chunks)
                        chunks = [
                            c for c in chunks
                            if not test_pattern.search(c.get("file_path", ""))
                        ]
                        filtered = before - len(chunks)
                        if filtered:
                            state["logs"].append(f"  Excluded {filtered} test file chunks (exclude_test_files=true)")
                    
                    state["code_chunks"] = chunks
                    state["logs"].append(f"  Found {len(state['code_chunks'])} code chunks")
                else:
                    error_msg = result.get("error", "Unknown search error")
                    state["error"] = error_msg
                    state["code_chunks"] = []
                    state["logs"].append(f"Search failed: {error_msg}")
                    print(f"[EXECUTOR] Search error details: {result}")
            
            except Exception as e:
                state["error"] = f"Search exception: {str(e)}"
                state["logs"].append(f"Search exception: {str(e)}")
                print(f"[EXECUTOR] Search exception: {e}")
                import traceback
                traceback.print_exc()
            
            return state
        
            # Node 2: LLM generates output
        async def llm_generate(state: PlaybookExecutionState):
            """
            Generate output using LLM with system/user message split.
            Uses map-reduce if context is too large.
            """
            from .token_utils import estimate_tokens, split_into_chunks, format_code_chunks_for_llm
            
            state["logs"].append(f"Generating with LLM")
            
            if state.get("error"):
                state["llm_output"] = ""
                return state
            
            code_chunks = state.get("code_chunks", [])
            
            # Soft warning if no code chunks found — log it but let the LLM handle it
            if not code_chunks:
                repo_id = state["user_input"].get("repo_id", "unknown")
                state["logs"].append(
                    f"  ⚠️ No code chunks returned for repo '{repo_id}' — "
                    f"LLM will generate based on available context only"
                )
            sys_prompt = playbook.system_prompt
            user_goal = state["user_input"].get("goal", state["user_input"].get("query", ""))
            
            # Estimate total tokens
            code_text = "\n".join([c.get('chunk_text', '') for c in code_chunks])
            total_tokens = estimate_tokens(sys_prompt) + estimate_tokens(code_text) + estimate_tokens(user_goal)
            
            cfg_max = getattr(self.llm, 'config', None)
            cfg_max = cfg_max.max_tokens if cfg_max else 4096
            
            MAX_CONTEXT = int(cfg_max * 0.7)  # 70% of max for prompt, 30% for response
            
            # Derive all token budgets from config
            code_context_tokens = int(cfg_max * 0.5)       # 50% for code in single-pass
            single_pass_output = int(cfg_max * 0.3)         # 30% for single-pass output
            batch_chunk_tokens = int(cfg_max * 0.15)        # 15% per batch chunk
            batch_output_tokens = int(cfg_max * 0.1)        # 10% per batch output
            reduce_output_tokens = int(cfg_max * 0.3)       # 30% for final reduce output
            reduce_context_chars = int(cfg_max * 2.5)       # ~2.5 chars/token for reduce context
            
            try:
                if total_tokens <= MAX_CONTEXT:
                    code_context = format_code_chunks_for_llm(code_chunks, max_tokens=code_context_tokens)
                    
                    prompt_suffix = "Generate your output based on the instructions and code:"
                    
                    # Use structured schema for prompt generation (data-driven)
                    output_schema = get_schema_for_playbook(playbook.name, playbook_def=playbook)
                    
                    if playbook.output_type == "tool_call" and playbook.tool_name:
                        # Tool-call playbooks: instruct LLM to output JSON invoking the tool
                        prompt_suffix += (
                            f"\n\nIMPORTANT: You MUST output a JSON block invoking '{playbook.tool_name}'. "
                            f"Do not output any other text."
                        )
                    elif output_schema:
                        # JSON-response playbooks: provide a concrete example 
                        # Local LLMs need to see actual populated values, not just field names.
                        if playbook.name == "build_vs_buy":
                            prompt_suffix += (
                                '\n\nIMPORTANT: You MUST return a JSON object. Here is an EXAMPLE of the expected format '
                                '(use real data from RETRIEVED CODE above, not these example values):\n'
                                '```json\n'
                                '{\n'
                                '  "requirement_summary": "A payment processing system with...",\n'
                                '  "functional_blocks": [\n'
                                '    {"name": "Payment Gateway", "description": "...", "complexity": "high", "architecture_layer": "Business Logic"}\n'
                                '  ],\n'
                                '  "build_estimate": {\n'
                                '    "total_cost_usd": 54000,\n'
                                '    "dev_months": 6,\n'
                                '    "team_size": 2,\n'
                                '    "timeline_weeks": 16,\n'
                                '    "complexity": "medium",\n'
                                '    "required_skills": ["Python", "REST APIs"],\n'
                                '    "key_risks": ["Tight timeline"]\n'
                                '  },\n'
                                '  "reuse_estimate": {\n'
                                '    "components": [\n'
                                '      {\n'
                                '        "name": "ExampleApp",\n'
                                '        "functional_block": "Payment Gateway",\n'
                                '        "match_quality": "Partial Match",\n'
                                '        "confidence_score": 55,\n'
                                '        "integration_effort_days": 5,\n'
                                '        "customization_effort_days": 10,\n'
                                '        "annual_maintenance_usd": 3000,\n'
                                '        "reasoning": "Has payment logic that can be adapted.",\n'
                                '        "architecture_layer": "Business Logic",\n'
                                '        "catalog_entry": {"repo_name": "ExampleApp", "description": "..."}\n'
                                '      }\n'
                                '    ],\n'
                                '    "gaps": [\n'
                                '      {"name": "Custom UI", "description": "...", "build_cost_usd": 18000, "dev_weeks": 4, "architecture_layer": "Presentation"}\n'
                                '    ],\n'
                                '    "total_integration_cost_usd": 25000,\n'
                                '    "total_timeline_weeks": 6,\n'
                                '    "annual_maintenance_total_usd": 3000\n'
                                '  },\n'
                                '  "comparison": {\n'
                                '    "build_total_usd": 54000,\n'
                                '    "reuse_total_usd": 25000,\n'
                                '    "savings_usd": 29000,\n'
                                '    "savings_pct": 54,\n'
                                '    "build_timeline_weeks": 16,\n'
                                '    "reuse_timeline_weeks": 6,\n'
                                '    "time_saved_weeks": 10,\n'
                                '    "recommendation": "REUSE",\n'
                                '    "confidence_score": 65,\n'
                                '    "reasoning": "Reusing ExampleApp saves 54% cost and 10 weeks..."\n'
                                '  }\n'
                                '}\n'
                                '```\n\n'
                                'Now generate YOUR response using the RETRIEVED CODE entries above. '
                                'Fill in ALL fields with REAL data. Do NOT return empty objects or zero values.\n'
                                'Even if catalog matches are weak, still estimate BUILD costs based on the requirement.\n'
                                'Wrap your response in ```json ... ```.\n'
                            )
                        else:
                            prompt_suffix += (
                                '\n\nIMPORTANT: You MUST return a JSON object. Here is an EXAMPLE of the expected format '
                                '(use real data from RETRIEVED CODE above, not these example values):\n'
                                '```json\n'
                                '{\n'
                                '  "requirement_summary": "Build an e-commerce platform",\n'
                                '  "capabilities": ["User authentication", "Product catalog", "Payment processing"],\n'
                                '  "decomposition": ["Auth Service", "Catalog Service", "Payment Gateway"],\n'
                                '  "catalog_matches": [\n'
                                '    {\n'
                                '      "capability": "User authentication",\n'
                                '      "component_name": "ExampleApp",\n'
                                '      "match_type": "Partial Match",\n'
                                '      "confidence_score": 65,\n'
                                '      "reasoning": "ExampleApp has a login system that can be adapted for this use case.",\n'
                                '      "catalog_entry": {\n'
                                '        "repo_name": "ExampleApp",\n'
                                '        "repo_url": "https://github.com/example/app",\n'
                                '        "description": "An example application",\n'
                                '        "topics": ["Web", "Auth"],\n'
                                '        "tech_stack": "Python, React",\n'
                                '        "architecture": "Microservices",\n'
                                '        "category": "Web App",\n'
                                '        "quality_score": 70,\n'
                                '        "pros": ["Good structure", "Modern stack"],\n'
                                '        "cons": ["No tests"]\n'
                                '      }\n'
                                '    }\n'
                                '  ],\n'
                                '  "architecture_composition": "The proposed architecture uses ExampleApp as foundation...",\n'
                                '  "gaps": ["Payment processing service needs custom development"],\n'
                                '  "risks": ["ExampleApp is incomplete and may need extra work"],\n'
                                '  "overall_confidence_score": 60\n'
                                '}\n'
                                '```\n\n'
                                'Now generate YOUR response using the RETRIEVED CODE entries above. '
                                'Fill in ALL fields with REAL data. Do NOT return empty arrays or strings.\n'
                                'Wrap your response in ```json ... ```.\n'
                            )

                    # Data-driven: prepend repo metadata if playbook requests it
                    repo_metadata_section = ""
                    context = state["user_input"].get("context", {})
                    if context and playbook.inject_repo_metadata:
                        meta_lines = ["REPOSITORY METADATA (use these exact values):"]
                        if context.get("name"):
                            meta_lines.append(f"  repo_name: {context['name']}")
                        if context.get("repo_url"):
                            meta_lines.append(f"  repo_url: {context['repo_url']}")
                        if context.get("branch"):
                            meta_lines.append(f"  branch: {context['branch']}")
                        if context.get("first_author"):
                            meta_lines.append(f"  first_author: {context['first_author']}")
                        if context.get("total_commits"):
                            meta_lines.append(f"  total_commits: {context['total_commits']}")
                        if context.get("last_pr_title"):
                            meta_lines.append(f"  last_pr_title: {context['last_pr_title']}")
                        repo_metadata_section = "\n".join(meta_lines) + "\n\n"
                    
                    # Data-driven: use grounding fence if playbook requests it
                    if playbook.grounding_fence:
                        user_msg = (
                            "USER REQUEST:\n" + user_goal + "\n\n"
                            "=== BEGIN RETRIEVED CONTEXT (THIS IS YOUR ONLY SOURCE OF TRUTH) ===\n"
                            + code_context + "\n"
                            "=== END RETRIEVED CONTEXT ===\n\n"
                            "GROUNDING RULE: You may ONLY reference data, names, URLs, "
                            "and details that appear between the BEGIN/END markers above. "
                            "If no entries are shown, return empty results. "
                            "Do NOT invent, guess, or recall any data from your training data.\n\n"
                            + prompt_suffix
                        )
                    else:
                        user_msg = (
                            repo_metadata_section
                            + "USER REQUEST:\n" + user_goal + "\n\n"
                            "RETRIEVED CODE:\n" + code_context + "\n\n"
                            + prompt_suffix
                        )
                    

                    # Temporary debug dump
                    with open("/tmp/llm_prompt_debug.txt", "w") as f:
                        f.write("=== SYSTEM PROMPT ===\n")
                        f.write(sys_prompt)
                        f.write(f"\n\n=== USER MSG ({len(user_msg)} chars) ===\n")
                        f.write(user_msg)
                    
                    output = await self.llm.generate(
                        user_msg,
                        system_prompt=sys_prompt,
                        max_tokens=single_pass_output
                    )
                    print(f"[EXECUTOR] Raw LLM output ({len(output)} chars):")
                    print("-" * 40)
                    print(output)
                    print("-" * 40)
                    
                    # Retry logic: if LLM returns suspiciously short output for json_response
                    # playbooks, it likely echoed empty defaults. Retry with higher temperature
                    # to break the deterministic pattern.
                    if (playbook.output_type == "json_response" 
                            and len(output) < 300 
                            and code_context.strip()):
                        for retry in range(2):
                            print(f"[EXECUTOR] ⚠️ Output too short ({len(output)} chars), retry {retry + 1}/2 with temp=0.7")
                            if playbook.name == "build_vs_buy":
                                nudge = (
                                    "IMPORTANT: Your previous response had empty fields. This is WRONG.\n"
                                    "The RETRIEVED CODE section above contains catalog entries that you MUST reference.\n"
                                    "You MUST fill in build_estimate (total_cost_usd, dev_months, team_size, timeline_weeks), "
                                    "reuse_estimate (components, gaps, total_integration_cost_usd), "
                                    "and comparison (build_total_usd, reuse_total_usd, recommendation).\n"
                                    "Do NOT return empty objects or zero values.\n\n"
                                )
                            else:
                                nudge = (
                                    "IMPORTANT: Your previous response had empty fields. This is WRONG.\n"
                                    "The RETRIEVED CODE section above contains catalog entries that you MUST use.\n"
                                    "You MUST populate catalog_matches with entries from the RETRIEVED CODE.\n"
                                    "You MUST fill in requirement_summary, capabilities, decomposition, "
                                    "architecture_composition, gaps, risks, and overall_confidence_score.\n"
                                    "Do NOT return empty arrays or empty strings.\n\n"
                                )
                            retry_output = await self.llm.generate(
                                nudge + user_msg,
                                system_prompt=sys_prompt,
                                max_tokens=single_pass_output,
                                temperature=0.7
                            )
                            print(f"[EXECUTOR] Retry {retry + 1} output: {len(retry_output)} chars")
                            if len(retry_output) > len(output):
                                output = retry_output
                            if len(output) >= 300:
                                break
                    
                    state["llm_output"] = output
                    state["logs"].append(f"  Generated {len(output)} chars in single pass (max_tokens={single_pass_output})")
                
                else:
                    # Context too large - use map-reduce
                    state["logs"].append(f"  Context too large ({total_tokens} tokens), using map-reduce")
                    
                    max_batches = getattr(playbook.search_strategy, 'max_batches', 5)
                    batches = split_into_chunks(code_chunks, max_tokens_per_chunk=batch_chunk_tokens)
                    batch_results = []
                    
                    # Cap batches to configured limit
                    batches_to_process = batches[:max_batches]
                    
                    for i, batch in enumerate(batches_to_process):
                        batch_code = format_code_chunks_for_llm(batch, max_tokens=batch_chunk_tokens)
                        
                        map_msg = (
                            "USER REQUEST:\n" + user_goal + "\n\n"
                            "CODE BATCH " + str(i+1) + "/" + str(len(batches_to_process)) + ":\n"
                            + batch_code + "\n\n"
                            "Analyze this batch for the user's request:"
                        )
                        
                        batch_output = await self.llm.generate(
                            map_msg,
                            system_prompt=sys_prompt,
                            max_tokens=batch_output_tokens
                        )
                        batch_results.append(batch_output)
                        state["logs"].append(f"  Processed batch {i+1}/{len(batches_to_process)}")
                    
                    # Reduce: Merge all batch results
                    partial = "\n\n---\n\n".join(
                        ["Batch " + str(i+1) + ":\n" + result for i, result in enumerate(batch_results)]
                    )
                    reduce_msg = (
                        "USER REQUEST:\n" + user_goal + "\n\n"
                        "PARTIAL ANALYSES FROM CODE BATCHES:\n" + partial[:reduce_context_chars] + "\n\n"
                        "Synthesize these into ONE comprehensive, cohesive final output:"
                    )
                    
                    final_output = await self.llm.generate(
                        reduce_msg,
                        system_prompt=sys_prompt,
                        max_tokens=reduce_output_tokens
                    )
                    state["llm_output"] = final_output
                    state["logs"].append(f"  Merged {len(batch_results)} batches into final output")
            
            except Exception as e:
                state["error"] = f"LLM generation error: {str(e)}"
                state["llm_output"] = ""
                state["logs"].append(f"Generation failed: {str(e)}")
            
            return state
        
        # Node 3: Format output & Execute Tools
        async def format_output(state: PlaybookExecutionState):
            """Format LLM output and execute any embedded tool calls."""
            if state["error"]:
                return state
            
            output_text = state["llm_output"]
            
            # Check for JSON tool call block
            import re
            import json
            
            tool_executed = False
            tool_result = None
            
            # Try Pydantic schema validation first
            output_schema = get_schema_for_playbook(playbook.name)
            validated_data = None
            if output_schema and playbook.name != "catalog_generator":
                try:
                    from ..llm.chat_wrapper import _parse_structured_output
                    validated_data = _parse_structured_output(output_text, output_schema)
                    state["logs"].append(f"  ✅ Output validated against {output_schema.__name__}")
                except (ValueError, Exception) as e:
                    state["logs"].append(f"  ⚠️ Schema validation failed, falling back to raw parsing: {e}")
            
            # Pattern to extract JSON block: ```json ... ``` or just {...}
            # We look for the specific structure: "tool": "name"
            try:
                # Find JSON block
                json_match = re.search(r'```json\s*({.*?})\s*```', output_text, re.DOTALL)
                if not json_match:
                     # Try finding raw JSON object if no markdown code block
                     # We accept any object starting with { and having "tool" or "description" (for loose params)
                     json_match = re.search(r'({[\s\S]*})', output_text)

                if json_match:
                    json_str = json_match.group(1)
                    # Use a library to find the first valid json object if simple regex captures too much
                    # But for now assume regex is okay. 
                    # Actually regex '({[\s\S]*})' is greedy. 
                    
                    try:
                        print(f"[EXECUTOR] Extracted JSON string: {json_str[:200]}...", flush=True)
                        try:
                            data = json.loads(json_str)
                        except json.JSONDecodeError as je:
                            print(f"[EXECUTOR] Standard JSON parse failed: {je}, attempting repair...", flush=True)
                            state["logs"].append(f"JSON parse failed, attempting repair...")
                            data = _repair_json(json_str)
                            if data is None:
                                raise je  # Re-raise if repair also failed
                            state["logs"].append(f"JSON repaired successfully")
                        print(f"[EXECUTOR] Parsed JSON keys: {list(data.keys())}", flush=True)
                        
                        # Data-driven: if playbook uses grounding fence, validate against retrieved context
                        if playbook.grounding_fence and "catalog_matches" in data:
                            matches = data["catalog_matches"]
                            fixed_matches = []
                            
                            # Build set of known repo names from retrieved context
                            known_repos = set()
                            for chunk in state.get("code_chunks", []):
                                chunk_text = chunk.get("chunk_text", "")
                                # Extract repo names from "CATALOG ENTRY: <name>" lines
                                for line in chunk_text.split("\n"):
                                    if line.startswith("CATALOG ENTRY:"):
                                        known_repos.add(line.replace("CATALOG ENTRY:", "").strip().lower())
                                # Also capture repo_name from chunk metadata
                                rn = chunk.get("repo_name", "")
                                if rn:
                                    known_repos.add(rn.lower())
                            
                            for m in matches:
                                # Fix malformed entries (missing wrapper fields)
                                if "catalog_entry" in m and "match_type" not in m:
                                    fixed_match = {
                                        "capability": "inferred from catalog",
                                        "component_name": m["catalog_entry"].get("repo_name", "Unknown"),
                                        "match_type": "Partial Match",
                                        "confidence_score": 0,
                                        "reasoning": "Result structure was malformed by LLM; inferred from catalog entry.",
                                        "catalog_entry": m["catalog_entry"]
                                    }
                                    for k in ["capability", "component_name", "match_type", "confidence_score", "reasoning"]:
                                        if k in m:
                                            fixed_match[k] = m[k]
                                    m = fixed_match
                                
                                # Validate: strip hallucinated entries not in retrieved context
                                if known_repos:
                                    entry_name = ""
                                    if "catalog_entry" in m:
                                        entry_name = m["catalog_entry"].get("repo_name", "").lower()
                                    elif "component_name" in m:
                                        entry_name = m.get("component_name", "").lower()
                                    
                                    if entry_name and entry_name not in known_repos:
                                        state["logs"].append(
                                            f"  ⛔ Stripped hallucinated match: '{entry_name}' "
                                            f"(not in retrieved context: {known_repos})"
                                        )
                                        continue  # Skip this hallucinated entry
                                
                                fixed_matches.append(m)
                            data["catalog_matches"] = fixed_matches

                        parsed_data = data # Capture for output
                        
                        # Pydantic validation + coercion (Phase 3: Schema Compliance)
                        schema_class = get_schema_for_playbook(playbook.name, playbook_def=playbook)
                        if schema_class and parsed_data and playbook.output_type == "json_response":
                            try:
                                validated = schema_class.model_validate(parsed_data)
                                parsed_data = validated.model_dump()
                                validated_data = validated
                                state["logs"].append(f"  ✓ Schema validation passed ({schema_class.__name__})")
                            except Exception as val_err:
                                state["logs"].append(f"  ⚠ Schema validation warning: {val_err}")
                                # Don't fail — the LLM output is still usable, just not perfectly shaped
                        
                        # Case 1: Standard Wrapper (tool/params)
                        if ("tool" in data or "tool_name" in data) and ("params" in data or "data" in data):
                            tool_name = data.get("tool") or data.get("tool_name")
                            params = data.get("params") or data.get("data")
                        # Case 2: Data-driven tool_call playbooks — LLM skipped wrapper
                        elif playbook.output_type == "tool_call" and playbook.tool_name:
                            # LLM output flat params without tool wrapper — infer tool name
                            tool_name = playbook.tool_name
                            params = data
                        else:
                            tool_name = None
                            params = None

                        if tool_name:
                             # Ensure case-insensitive match for common tools
                             if tool_name.lower() in ["save_catalog_entry", "savecatalogentry"]:
                                 tool_name = "save_catalog_entry"
                             
                             if tool_name == "save_catalog_entry":
                                 state["logs"].append(f"Executing tool: {tool_name}")
                                 
                                 # inject repo_id if missing or template
                                 if not params.get("repo_id") or params.get("repo_id") == "{{repo_id}}":
                                     params["repo_id"] = state["user_input"].get("repo_id")
                                 
                                 # Inject metadata from context if available
                                 context = state["user_input"].get("context", {})
                                 if context:
                                     # Fields to inject if missing or template or empty
                                     fields_to_inject = ["repo_name", "repo_url", "branch"]
                                     for field in fields_to_inject:
                                         # Map 'name' from context to 'repo_name' in params
                                         ctx_key = "name" if field == "repo_name" else field
                                         
                                         if not params.get(field) or params.get(field) == "{{" + field + "}}":
                                             if ctx_key in context:
                                                 params[field] = context[ctx_key]
                                     
                                     # Also inject rich metadata into the 'metadata' JSON string if possible
                                     # The tool expects 'metadata' as a string or dict. 
                                     # If it's a string, we parse, update, stringify.
                                     # If it's a dict, we update.
                                     meta_param = params.get("metadata", {})
                                     if isinstance(meta_param, str):
                                         try:
                                             meta_dict = json.loads(meta_param)
                                         except:
                                             meta_dict = {}
                                     else:
                                         meta_dict = meta_param
                                     
                                     # Merge context into metadata
                                     for k, v in context.items():
                                         if k not in meta_dict:
                                             meta_dict[k] = v
                                     
                                     params["metadata"] = json.dumps(meta_dict)

                                 tool_result = await self.tools.save_catalog_entry(params)
                                 state["logs"].append(f"Tool result: {tool_result}")
                                 tool_executed = True
                                 
                                 # Clean up result text to be user friendly
                                 output_text = f"Catalog entry generated and saved for {params.get('repo_name', params['repo_id'])}."
                             else:
                                 state["logs"].append(f"Unknown tool: {tool_name}")
                                
                    except json.JSONDecodeError:
                        state["logs"].append("Failed to decode JSON tool block")
            except Exception as e:
                state["logs"].append(f"Tool execution failed: {e}")
                traceback.print_exc()

            state["logs"].append(f"Success: {playbook.name}")
            
            # If we parsed valid JSON but didn't execute a tool (e.g. catalog_search report),
            # ensure the output result is the clean JSON string.
            if not tool_executed and 'parsed_data' in locals() and parsed_data:
                import json
                output_text = json.dumps(parsed_data)
            
            # Determine best structured data to use
            final_data = None
            if validated_data is not None:
                # Pydantic-validated data is available — use it
                final_data = validated_data.model_dump() if hasattr(validated_data, 'model_dump') else validated_data
            elif 'parsed_data' in locals() and parsed_data:
                final_data = parsed_data
            
            state["outputs"] = {
                "result": output_text,
                "data": final_data,
                "tool_executed": tool_executed,
                "tool_result": tool_result,
                "code_chunks_used": len(state["code_chunks"]),
                "playbook": playbook.name
            }
            
            return state
        
        # Build graph
        graph.add_node("search", search_code)
        graph.add_node("generate", llm_generate)
        graph.add_node("format", format_output)
        
        graph.set_entry_point("search")
        graph.add_edge("search", "generate")
        graph.add_edge("generate", "format")
        graph.add_edge("format", END)
        
        return graph.compile()
    
    def _format_code_chunks(self, chunks: list[dict]) -> str:
        """Format code chunks for LLM prompt."""
        if not chunks:
            return "No code found."
        
        formatted = []
        for i, chunk in enumerate(chunks[:10], 1):
            file_path = chunk.get('file_path', 'unknown')
            code = chunk.get('chunk_text', '')
            score = chunk.get('score', 0)
            
            formatted.append(
                f"---\nChunk {i} (relevance: {score:.2f})\n"
                f"File: {file_path}\n```\n{code[:1000]}\n```\n"
            )
        
        return "\n".join(formatted)
    
    # ─── ReAct Execution Path ────────────────────────────────────────────────
    
    async def _execute_react(self, playbook, playbook_name: str, user_input: dict) -> dict:
        """
        Execute a playbook using the ReAct (Reasoning + Acting) loop.
        
        The LLM decides which tools to call, observes results, and loops
        until it has enough information to answer.
        """
        print(f"[EXECUTOR] ⚡ ReAct mode for playbook '{playbook_name}'")
        
        try:
            workflow = self._build_react_workflow(playbook)
            
            # Build initial message with goal + context
            goal = user_input.get("goal", user_input.get("query", ""))
            repo_id = user_input.get("repo_id")
            
            user_content = f"{goal}"
            if repo_id:
                if isinstance(repo_id, list):
                    user_content += f"\n\nRepository IDs: {', '.join(repo_id)}"
                else:
                    user_content += f"\n\nRepository ID: {repo_id}"
            
            # Inject repo_id context so tools can use it
            context = user_input.get("context", {})
            if context:
                user_content += f"\n\nRepository Info: {json.dumps(context, default=str)}"
            
            initial_state: ReactExecutionState = {
                "messages": [HumanMessage(content=user_content)],
                "playbook_name": playbook_name,
                "user_input": user_input,
                "iteration": 0,
                "max_iterations": 5,
                "outputs": {},
                "error": None,
                "logs": [f"Running playbook: {playbook_name} (ReAct mode)"]
            }
            
            result = await workflow.ainvoke(initial_state)
            
            # Extract the final answer from the last AI message
            answer = ""
            for msg in reversed(result.get("messages", [])):
                if isinstance(msg, AIMessage) and msg.content:
                    # Skip messages that are just tool calls with no text
                    if not (hasattr(msg, 'tool_calls') and msg.tool_calls and not msg.content.strip()):
                        answer = msg.content
                        break
            
            iterations = result.get("iteration", 0)
            logs = result.get("logs", [])
            logs.append(f"ReAct completed in {iterations} iterations")
            logs.append(f"Success: {playbook_name}")
            
            return {
                "success": True,
                "outputs": {
                    "result": answer,
                    "data": None,
                    "tool_executed": False,
                    "tool_result": None,
                    "iterations": iterations,
                    "playbook": playbook_name,
                },
                "error": None,
                "logs": logs
            }
        
        except Exception as e:
            print(f"[EXECUTOR] ReAct execution failed: {e}", flush=True)
            traceback.print_exc()
            return {
                "success": False,
                "outputs": {},
                "error": str(e),
                "logs": [f"ReAct execution error: {str(e)}"]
            }
    
    def _build_react_workflow(self, playbook):
        """
        Build a standard LangGraph ReAct workflow.
        
        Graph:
            agent (LLM with tools) ←→ tools (ToolNode)
                                   ↘ END (when no tool calls)
        """
        from .langchain_tools import create_langchain_tools
        
        # Create LangChain tools from PlaybookTools
        tools = create_langchain_tools(self.tools)
        
        # Bind tools to chat model
        chat_model = self._get_chat_model()
        llm_with_tools = chat_model.bind_tools(tools)
        
        # System prompt from the playbook definition
        system_prompt = playbook.system_prompt
        
        # ── Agent Node ──
        async def agent_node(state: ReactExecutionState) -> dict:
            """LLM thinks and optionally calls tools."""
            iteration = state.get("iteration", 0)
            max_iter = state.get("max_iterations", 5)
            
            print(f"[EXECUTOR] ReAct agent — iteration {iteration + 1}/{max_iter}")
            
            # Check iteration limit
            if iteration >= max_iter:
                print(f"[EXECUTOR] ReAct max iterations reached ({max_iter})")
                return {
                    "messages": [AIMessage(content="I've reached the maximum number of exploration steps. Let me synthesize my findings from the data gathered so far.")],
                    "iteration": iteration + 1,
                    "logs": [f"  Max iterations reached ({max_iter})"]
                }
            
            # Build messages: system + conversation history
            messages = [SystemMessage(content=system_prompt)]
            messages.extend(state.get("messages", []))
            
            # Get LLM config for token budgets
            config = getattr(self.llm, 'config', None)
            max_tokens = max(512, (config.max_tokens if config else 4096) // 4)
            
            try:
                response = await llm_with_tools.ainvoke(
                    messages,
                    max_tokens=max_tokens,
                    temperature=0.1
                )
                
                # Log what happened
                has_tool_calls = hasattr(response, 'tool_calls') and response.tool_calls
                if has_tool_calls:
                    tool_names = [tc['name'] for tc in response.tool_calls]
                    log_entry = f"  Iteration {iteration + 1}: called tools [{', '.join(tool_names)}]"
                else:
                    log_entry = f"  Iteration {iteration + 1}: final answer ({len(response.content)} chars)"
                
                print(f"[EXECUTOR] {log_entry}")
                
                return {
                    "messages": [response],
                    "iteration": iteration + 1,
                    "logs": [log_entry]
                }
            
            except Exception as e:
                print(f"[EXECUTOR] ReAct agent error: {e}")
                return {
                    "messages": [AIMessage(content=f"Error during analysis: {e}. Providing best answer from data gathered.")],
                    "iteration": iteration + 1,
                    "logs": [f"  Agent error: {e}"]
                }
        
        # ── Build Graph ──
        graph = StateGraph(ReactExecutionState)
        
        graph.add_node("agent", agent_node)
        graph.add_node("tools", ToolNode(tools))
        
        graph.set_entry_point("agent")
        
        # Conditional: if tool calls → tools node, else → END
        graph.add_conditional_edges(
            "agent",
            tools_condition,
        )
        
        # After tools execute → back to agent
        graph.add_edge("tools", "agent")
        
        return graph.compile()
