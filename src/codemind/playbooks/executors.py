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
            print(f"[EXECUTOR] Playbook execution failed: {e}")
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
            Handles both simple queries and phased search strategies.
            """
            state["logs"].append(f"Searching code for: {playbook.name}")
            strategy = playbook.search_strategy
            user_input = state["user_input"]
            repo_id = user_input.get("repo_id")
            
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
                "limit": getattr(strategy, 'limit', 10),
                "mode": getattr(strategy, 'mode', 'semantic'),
                "file_types": getattr(strategy, 'file_types', []),
                "graph_filters": getattr(strategy, 'graph_filters', {})
            }
            
            state["logs"].append(f"  Extracted {len(queries)} queries from search strategy")
            state["logs"].append(f"  Search mode: {search_params['mode']}, limit: {search_params['limit']}")
            
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
                    
                    user_msg = (
                        "USER REQUEST:\n" + user_goal + "\n\n"
                        "RETRIEVED CODE:\n" + code_context + "\n\n"
                        "Generate your output based on the instructions and code:"
                    )
                    
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
                    
                    batches = split_into_chunks(code_chunks, max_tokens_per_chunk=batch_chunk_tokens)
                    batch_results = []
                    
                    for i, batch in enumerate(batches[:5]):  # Cap at 5 batches
                        batch_code = format_code_chunks_for_llm(batch, max_tokens=batch_chunk_tokens)
                        
                        map_msg = (
                            "USER REQUEST:\n" + user_goal + "\n\n"
                            "CODE BATCH " + str(i+1) + "/" + str(min(len(batches), 5)) + ":\n"
                            + batch_code + "\n\n"
                            "Analyze this batch for the user's request:"
                        )
                        
                        batch_output = await self.llm.generate(
                            map_msg,
                            system_prompt=sys_prompt,
                            max_tokens=batch_output_tokens
                        )
                        batch_results.append(batch_output)
                        state["logs"].append(f"  Processed batch {i+1}/{min(len(batches), 5)}")
                    
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
        
        # Node 3: Format output
        async def format_output(state: PlaybookExecutionState):
            """Format LLM output into structured result."""
            if state["error"]:
                return state
            
            state["logs"] = [f"Success: {playbook.name}"]
            
            state["outputs"] = {
                "result": state["llm_output"],
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
