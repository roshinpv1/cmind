"""
Playbook Executor - Prompt-based playbook execution.

New architecture:
1. Search for code using playbook's search_strategy
2. Build prompt: system_prompt + retrieved code
3. LLM generates output based on playbook behavior

All playbooks use same flow, different prompts.
"""

from typing import TypedDict, Annotated, Optional, Any
from langgraph.graph import StateGraph, END
import operator
import traceback


class PlaybookExecutionState(TypedDict):
    """State for playbook execution workflow."""
    playbook_name: str
    user_input: dict
    code_chunks: list[dict]
    llm_output: str
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
    Execute prompt-based playbooks via LangGraph workflows.
    
    Flow:
    1. Search code (using playbook's search_strategy)
    2. Build prompt (system_prompt + code)
    3. LLM generates output
    """
    
    def __init__(self, registry, tools, llm_client):
        """
        Initialize playbook executor.
        
        Args:
            registry: PlaybookRegistry instance
            tools: PlaybookTools instance (just search_codebase)
            llm_client: LLM for generation
        """
        self.registry = registry
        self.tools = tools
        self.llm = llm_client
    
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
        
        # Build workflow for this playbook
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
                "limit": getattr(strategy, "limit", 100 if playbook.name == "code_analyzer" else 10),
                "mode": getattr(strategy, 'mode', 'semantic'),
                "file_types": getattr(strategy, 'file_types', []),
                "graph_filters": getattr(strategy, 'graph_filters', {}),
                "min_score": getattr(strategy, 'min_score', 0.0)
            }
            
            state["logs"].append(f"  Extracted {len(queries)} queries: {queries}")
            state["logs"].append(f"  Search mode: {search_params['mode']}, limit: {search_params['limit']}, min_score: {search_params['min_score']}")
            
            try:
                result = await self.tools.search_codebase(search_params)
                
                if result.get("success"):
                    state["code_chunks"] = result.get("results", [])
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
                    
                    # Force JSON for catalog_generator
                    # Force JSON for catalog_generator and catalog_search

                    if playbook.name == "catalog_generator":
                        prompt_suffix += "\n\nIMPORTANT: You MUST output a JSON block invoking 'save_catalog_entry'. Do not output any other text."
                    elif playbook.name == "catalog_search":
                        schema_hint = """
{
  "requirement_summary": "string",
  "capabilities": {"functional": [], "non_functional": []},
  "decomposition": {"core_modules": [], "supporting_modules": [], "cross_cutting": []},
  "catalog_matches": [{
    "capability": "string",
    "component_name": "string",
    "match_type": "Full Match | Partial Match | No Match",
    "confidence_score": 0-100,
    "reasoning": "string",
    "catalog_entry": {
      "repo_name": "string",
      "repo_url": "string",
      "description": "string",
      "topics": [],
      "tech_stack": "string",
      "architecture": "string",
      "category": "string",
      "quality_score": 0-100,
      "pros": [],
      "cons": []
    }
  }],
  "architecture_composition": "string",
  "gaps": [],
  "risks": [],
  "overall_confidence_score": 0-100
}
"""
                        prompt_suffix += (
                            f"\n\nIMPORTANT: You MUST return your analysis as a valid JSON object matching the following schema exactly:\n{schema_hint}\n"
                            "Wrap it in a markdown code block (```json ... ```).\n"
                            "Do NOT use keys 'project' or 'recommendation'.\n"
                            "IMPORTANT: Do NOT generate a generic project plan. If NO catalog matches found, return empty 'catalog_matches' list in the JSON.\n"
                            "IMPORTANT: Each match in `catalog_matches` MUST include `capability`, `match_type`, `confidence_score` and `reasoning`, NOT JUST `catalog_entry`.\n"
                            "CRITICAL: The `catalog_entry` data MUST come ONLY from the RETRIEVED CODE context above. "
                            "Copy the actual repo_name, repo_url, description, architecture, tech_stack, topics, category, quality_score, pros, and cons from each CATALOG ENTRY in the context. "
                            "Do NOT invent or hallucinate component names, URLs, or details. If a field is not in the context, set it to empty string or empty list."
                        )
                    elif playbook.name == "code_analyzer":
                        schema_hint = """
{
  "summary": "string",
  "analysis": "string",
  "key_insights": [],
  "strategic_implications": [],
  "suggestions": []
}
"""
                        prompt_suffix += f"\n\nIMPORTANT: You MUST return your analysis as a valid JSON object matching the following schema exactly:\n{schema_hint}\n\nWrap it in a markdown code block (```json ... ```)."


                    user_msg = (
                        "USER REQUEST:\n" + user_goal + "\n\n"
                        "RETRIEVED CODE:\n" + code_context + "\n\n"
                        + prompt_suffix
                    )
                    # print(f"[EXECUTOR] Full User Message:\n{user_msg}")
                    # print(f"[DEBUG] User Msg: {user_msg[:500]}...")
                    
                    output = await self.llm.generate(
                        user_msg,
                        system_prompt=sys_prompt,
                        max_tokens=single_pass_output
                    )
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
                        data = json.loads(json_str)
                        
                        # Fix for catalog_search structure issues:
                        # If catalog_matches elements are missing wrapper fields but have catalog_entry, wrap them.
                        if playbook.name == "catalog_search" and "catalog_matches" in data:
                            matches = data["catalog_matches"]
                            fixed_matches = []
                            for m in matches:
                                if "catalog_entry" in m and "match_type" not in m:
                                    # Malformed: missing wrapper fields. detailed in issue #75
                                    fixed_match = {
                                        "capability": "inferred from catalog",
                                        "component_name": m["catalog_entry"].get("repo_name", "Unknown"),
                                        "match_type": "Partial Match", # Defaulting to partial
                                        "confidence_score": 0, # Default to 0 if missing
                                        "reasoning": "Result structure was malformed by LLM; inferred from catalog entry.",
                                        "catalog_entry": m["catalog_entry"]
                                    }
                                    # Attempt to extract fields if they exist in the flat structure
                                    for k in ["capability", "component_name", "match_type", "confidence_score", "reasoning"]:
                                        if k in m:
                                            fixed_match[k] = m[k]
                                            
                                    fixed_matches.append(fixed_match)
                                else:
                                    fixed_matches.append(m)
                            data["catalog_matches"] = fixed_matches

                        parsed_data = data # Capture for output
                        
                        # Case 1: Standard Wrapper
                        if "tool" in data and "params" in data:
                            tool_name = data["tool"]
                            params = data["params"]
                        # Case 2: Loose Params (Model skipped wrapper) - Only for catalog_generator
                        elif playbook.name == "catalog_generator" and (
                            "description" in data or "summary_detailed" in data
                            or "purpose" in data or "name" in data
                        ):
                            # Assume save_catalog_entry if we see catalog fields (flat or nested)
                            tool_name = "save_catalog_entry"
                            params = data
                        else:
                            tool_name = None
                            params = None

                        if tool_name:
                            # Execute tool
                            if tool_name == "save_catalog_entry":
                                state["logs"].append(f"Executing tool: {tool_name}")
                                
                                # inject repo_id if missing or template
                                if params.get("repo_id") == "{{repo_id}}" or not params.get("repo_id"):
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
            
            state["outputs"] = {
                "result": output_text,
                "data": parsed_data if 'parsed_data' in locals() else None,  # Store structured JSON
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
