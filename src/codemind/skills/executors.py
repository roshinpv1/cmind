"""
Skill Executor - Prompt-based skill execution.

New architecture:
1. Search for code using skill's search_strategy
2. Build prompt: system_prompt + retrieved code
3. LLM generates output based on skill behavior

All skills use same flow, different prompts.
"""

from typing import TypedDict, Annotated, Optional, Any
from langgraph.graph import StateGraph, END
import operator
import traceback


class SkillExecutionState(TypedDict):
    """State for skill execution workflow."""
    skill_name: str
    user_input: dict
    code_chunks: list[dict]
    llm_output: str
    outputs: dict
    error: Optional[str]
    logs: Annotated[list[str], operator.add]


class SkillExecutor:
    """
    Execute prompt-based skills via LangGraph workflows.
    
    Flow:
    1. Search code (using skill's search_strategy)
    2. Build prompt (system_prompt + code)
    3. LLM generates output
    """
    
    def __init__(self, registry, tools, llm_client):
        """
        Initialize skill executor.
        
        Args:
            registry: SkillRegistry instance
            tools: SkillTools instance (just search_codebase)
            llm_client: LLM for generation
        """
        self.registry = registry
        self.tools = tools
        self.llm = llm_client
    
    async def execute(self, skill_name: str, user_input: dict) -> dict:
        """
        Execute a skill.
        
        Args:
            skill_name: Name of skill to execute
            user_input: User's input (e.g., {" goal": "explain auth.py", "repo_id": "123"})
            
        Returns:
            {
                success: bool,
                outputs: dict,
                error: str (if failed),
                logs: list[str]
            }
        """
        # Get skill definition
        skill = self.registry.get_skill(skill_name)
        if not skill:
            return {
                "success": False,
                "outputs": {},
                "error": f"Skill not found: {skill_name}",
                "logs": [f"✗ Skill not found: {skill_name}"]
            }
        
        # Build workflow for this skill
        workflow = self._build_workflow(skill)
        
        # Execute
        initial_state: SkillExecutionState = {
            "skill_name": skill_name,
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
            print(f"[EXECUTOR] Skill execution failed: {e}")
            traceback.print_exc()
            return {
                "success": False,
                "outputs": {},
                "error": str(e),
                "logs": [f"✗ Execution error: {str(e)}"]
            }
    
    def _build_workflow(self, skill):
        """
        Build prompt-based workflow.
        
        Flow:
        search_code → build_prompt → llm_generate → format_output
        """
        graph = StateGraph(SkillExecutionState)
        
        # Node 1: Search for code
        async def search_code(state: SkillExecutionState):
            """
            Search for code using skill's search strategy.
            Handles both simple queries and phased search strategies.
            """
            state["logs"].append(f"→ Searching code for: {skill.name}")
            strategy = skill.search_strategy
            user_input = state["user_input"]
            repo_id = user_input.get("repo_id")
            
            # Extract queries - support both direct queries and phases
            queries = []
            
            # Check if strategy has phases (new format)
            if hasattr(strategy, 'phases') and strategy.phases:
                # Extract queries from all phases
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
                    state["logs"].append(f"✗ Search failed: {state['error']}")
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
            
            # Debug logging
            state["logs"].append(f"  Extracted {len(queries)} queries from search strategy")
            state["logs"].append(f"  Search mode: {search_params['mode']}, limit: {search_params['limit']}")
            
            # Execute search
            try:
                result = await self.tools.search_codebase(search_params)
                
                if result.get("success"):
                    state["code_chunks"] = result.get("results", [])
                    state["logs"].append(f"  Found {len(state['code_chunks'])} code chunks")
                else:
                    error_msg = result.get("error", "Unknown search error")
                    state["error"] = error_msg
                    state["code_chunks"] = []
                    state["logs"].append(f"✗ Search failed: {error_msg}")
                    # Print detailed error for debugging
                    print(f"[EXECUTOR] Search error details: {result}")
            
            except Exception as e:
                state["error"] = f"Search exception: {str(e)}"
                state["logs"].append(f"✗ Search exception: {str(e)}")
                print(f"[EXECUTOR] Search exception: {e}")
                import traceback
                traceback.print_exc()
            
            return state
        
        # Node 2: LLM generates output
        async def llm_generate(state: SkillExecutionState):
            """
            Generate output using LLM with token-aware chunking.
            
            Automatically uses map-reduce pattern if context is too large:
            1. Split code chunks into token-sized batches
            2. Process each batch independently
            3. Merge results into final output
            """
            from .token_utils import estimate_tokens, split_into_chunks, format_code_chunks_for_llm
            
            state["logs"].append(f"→ Generating with LLM")
            
            if state.get("error"):
                state["llm_output"] = ""
                return state
            
            code_chunks = state.get("code_chunks", [])
            system_prompt = skill.system_prompt
            user_goal = state["user_input"].get("goal", state["user_input"].get("query", ""))
            
            # Estimate total tokens
            code_text = "\n".join([c.get('chunk_text', '') for c in code_chunks])
            total_tokens = estimate_tokens(system_prompt) + estimate_tokens(code_text) + estimate_tokens(user_goal)
            
            MAX_CONTEXT = 25000  # Safe limit for local LLMs
            
            try:
                # If context fits in one call, use simple approach
                if total_tokens <= MAX_CONTEXT:
                    code_context = format_code_chunks_for_llm(code_chunks, max_tokens=2000)
                    
                    prompt = f"""{system_prompt}

USER REQUEST:
{user_goal}

RETRIEVED CODE:
{code_context}

Generate your output based on the system prompt and code:"""
                    
                    output = await self.llm.generate(prompt, max_tokens=1500)
                    state["llm_output"] = output
                    state["logs"].append(f"  Generated {len(output)} chars in single pass")
                
                else:
                    # Context too large - use map-reduce
                    state["logs"].append(f"  Context too large ({total_tokens} tokens), using map-reduce")
                    
                    # Map: Process each batch
                    batches = split_into_chunks(code_chunks, max_tokens_per_chunk=1500)
                    batch_results = []
                    
                    for i, batch in enumerate(batches):
                        batch_code = format_code_chunks_for_llm(batch, max_tokens=1500)
                        
                        map_prompt = f"""{system_prompt}

USER REQUEST:
{user_goal}

CODE BATCH {i+1}/{len(batches)}:
{batch_code}

Analyze this batch for the user's request:"""
                        
                        batch_output = await self.llm.generate(map_prompt, max_tokens=800)
                        batch_results.append(batch_output)
                        state["logs"].append(f"  Processed batch {i+1}/{len(batches)}")
                    
                    # Reduce: Merge all batch results
                    reduce_prompt = f"""{system_prompt}

USER REQUEST:
{user_goal}

PARTIAL ANALYSES FROM CODE BATCHES:
""" + "\n\n---\n\n".join([f"Batch {i+1}:\n{result}" for i, result in enumerate(batch_results)]) + """

Synthesize these partial analyses into ONE comprehensive, cohesive final output:"""
                    
                    final_output = await self.llm.generate(reduce_prompt, max_tokens=1500)
                    state["llm_output"] = final_output
                    state["logs"].append(f"  Merged {len(batches)} batches into final output")
            
            except Exception as e:
                state["error"] = f"LLM generation error: {str(e)}"
                state["llm_output"] = ""
                state["logs"].append(f"✗ Generation failed: {str(e)}")
            
            return state
        
        # Node 3: Format output
        async def format_output(state: SkillExecutionState):
            """Format LLM output into structured result."""
            if state["error"]:
                return state  # Skip if previous steps failed
            
            state["logs"] = [f"✓ Success: {skill.name}"]
            
            # Generic output format
            state["outputs"] = {
                "result": state["llm_output"],
                "code_chunks_used": len(state["code_chunks"]),
                "skill": skill.name
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
        for i, chunk in enumerate(chunks[:10], 1):  # Max 10 chunks
            file_path = chunk.get('file_path', 'unknown')
            code = chunk.get('chunk_text', '')
            score = chunk.get('score', 0)
            
            formatted.append(f"""
---
Chunk {i} (relevance: {score:.2f})
File: {file_path}
```
{code[:1000]}  # Limit chunk size
```
""")
        
        return "\n".join(formatted)
