"""
Autonomous Planner Agent - LLM-powered playbook selection and execution.

The planner:
1. Interprets user goals
2. Selects appropriate playbooks or tools
3. Executes via executor (playbooks) or direct dispatch (tools)
4. Observes results
5. Iterates until goal satisfied (requires at least 1 data retrieval step)
6. Returns final answer grounded in codebase data

This is the "brain" of the autonomous agent system.
"""

from typing import Literal
from langgraph.graph import StateGraph, END
import asyncio
import json
import re

from .planner_state import PlannerState


class PlannerAgent:
    """
    Autonomous planner that selects and executes playbooks to achieve goals.
    
    Uses LLM for reasoning and playbook selection.
    Uses PlaybookExecutor for deterministic execution.
    """
    
    def __init__(self, registry, executor, llm_client):
        self.registry = registry
        self.executor = executor
        self.llm = llm_client
        self.workflow = self._build_workflow()
    
    def _build_workflow(self) -> StateGraph:
        """Build the think-act-observe workflow."""
        graph = StateGraph(PlannerState)
        
        graph.add_node("think", self._think)
        graph.add_node("act", self._act)
        graph.add_node("observe", self._observe)
        graph.add_node("finish", self._finish)
        
        graph.set_entry_point("think")
        
        graph.add_conditional_edges(
            "think",
            self._should_continue,
            {"act": "act", "finish": "finish"}
        )
        
        graph.add_edge("act", "observe")
        graph.add_edge("observe", "think")
        graph.add_edge("finish", END)
        
        return graph.compile()
    
    async def _emit_update(self, state: PlannerState):
        """Emit state update if callback is registered."""
        if hasattr(self, "on_update") and self.on_update:
            try:
                await self.on_update(state)
            except Exception as e:
                print(f"[PLANNER] Callback error: {e}")
    
    async def _think(self, state: PlannerState) -> PlannerState:
        """
        Agent thinks about what to do next.
        Auto-finishes after 3 successful data retrievals.
        """
        print(f"\n[PLANNER] 🤔 Think (iteration {state['iteration']})")
        await asyncio.sleep(0)  # Yield to event loop
        await self._emit_update(state)

        # Count successful data retrievals
        successful_runs = sum(
            1 for obs in state.get("observations", [])
            if obs.get("success") and obs.get("outputs")
        )
        has_data = successful_runs > 0
        
        # Auto-finish after 10 successful playbook/tool runs — no need to ask LLM
        if successful_runs >= 10:
            print(f"[PLANNER] Auto-finishing: {successful_runs} successful data retrievals")
            state["finished"] = True
            state["final_result"] = "Auto-finish: sufficient data gathered."
            state["iteration"] += 1
            return state
        
        playbooks_desc = self._format_playbooks_for_prompt()
        tools_desc = self._format_tools_for_prompt()
        history_desc = self._format_history(state)
        
        if has_data:
            finish_opt = (
                "FINISH: <your comprehensive answer based on gathered data>\n\n"
                "IMPORTANT: You already have data from " + str(successful_runs) + " successful queries. "
                "You SHOULD finish now unless you need fundamentally different data. "
                "Do NOT repeat the same playbook with similar queries."
            )
        else:
            finish_opt = "FINISH is NOT allowed yet. You MUST use a PLAYBOOK or TOOL first."
        
        # System prompt sent as a system message
        system_prompt = (
            "You are a code analysis agent. Respond with EXACTLY one action.\n\n"
            "PLAYBOOKS (LLM analysis):\n" + playbooks_desc + "\n\n"
            "TOOLS (direct data lookup):\n" + tools_desc + "\n\n"
            "RESPONSE FORMAT - reply with ONLY one of these:\n\n"
            "PLAYBOOK: <name>\n"
            'PARAMS: {"query": "<search query>"}\n\n'
            "TOOL: <name>\n"
            'PARAMS: {"<param>": "<value>"}\n\n'
            + finish_opt
        )

        # User prompt - just goal + history
        user_prompt = (
            "Goal: " + state["goal"] + "\n\n"
            "History:\n" + history_desc + "\n\n"
            "Your action:"
        )
        
        try:
            # Use config max_tokens: ~5% for thinking (short action output)
            think_tokens = max(256, self.llm.config.max_tokens // 20)
            thought = await self.llm.generate(
                user_prompt,
                system_prompt=system_prompt,
                max_tokens=think_tokens,
                temperature=0.1
            )
            print(f"[PLANNER] Thought: {thought[:200]}...")
            
            state["thoughts"] = state.get("thoughts", []) + [thought]
            state["iteration"] += 1
            
            # Parse LLM output — try standard format first, then model-specific
            action = self._parse_action(thought, has_data, state)
            
            if action["type"] == "tool":
                print(f"[PLANNER] Selected tool: {action['name']}")
                state["plan"] = [{"tool": action["name"], "params": action["params"]}]
            
            elif action["type"] == "playbook":
                if self.registry.get_playbook(action["name"]):
                    print(f"[PLANNER] Selected playbook: {action['name']}")
                    state["plan"] = [{"playbook": action["name"], "params": action["params"]}]
                else:
                    available = self.registry.list_playbooks()
                    print(f"[PLANNER] Playbook '{action['name']}' not found. Available: {available}")
                    # Auto-correct to code_analyzer playbook
                    state["plan"] = [{"playbook": "code_analyzer", "params": action["params"]}]
            
            elif action["type"] == "finish":
                state["finished"] = True
                state["final_result"] = action.get("result", "")
                print(f"[PLANNER] Agent decided to finish")
            
            elif action["type"] == "fallback":
                print(f"[PLANNER] Using fallback: qa playbook with extracted query")
                state["plan"] = [{"playbook": "qa", "params": action["params"]}]
        
        except Exception as e:
            print(f"[PLANNER] Think error: {e}")
            if not has_data:
                state["plan"] = [{"playbook": "qa", "params": {"query": state["goal"]}}]
                state["thoughts"] = state.get("thoughts", []) + [f"Think error: {e}. Falling back to qa."]
            else:
                state["finished"] = True
                state["final_result"] = f"Error in planning: {e}"
        
        return state
    
    def _parse_params(self, text: str) -> dict:
        """Parse PARAMS JSON from LLM output."""
        params_match = re.search(r'PARAMS:\s*(\{.*?\})', text, re.DOTALL)
        if params_match:
            try:
                return json.loads(params_match.group(1).strip())
            except json.JSONDecodeError:
                print("[PLANNER] Failed to parse PARAMS json")
        return {}

    def _parse_action(self, thought: str, has_data: bool, state: dict) -> dict:
        """
        Parse LLM output into an action dict.
        
        Tries formats in order:
        1. Standard PLAYBOOK:/TOOL:/FINISH: format
        2. Model-native <|channel|>/<|message|> format (openai/gpt-oss-20b)
        3. Raw JSON extraction
        4. Fallback to qa playbook with the user's goal
        """
        # 1. Try standard format: TOOL: / PLAYBOOK: / FINISH:
        tool_match = re.search(r'TOOL:\s*(.+)', thought)
        playbook_match = re.search(r'PLAYBOOK:\s*(.+)', thought)
        finish_match = re.search(r'FINISH:\s*(.+)', thought, re.DOTALL)
        
        if tool_match and not finish_match:
            tool_name = tool_match.group(1).strip().strip('"\'')
            return {"type": "tool", "name": tool_name, "params": self._parse_params(thought)}
        
        if playbook_match and not finish_match:
            playbook_name = playbook_match.group(1).strip().strip('"\'')
            return {"type": "playbook", "name": playbook_name, "params": self._parse_params(thought)}
        
        if finish_match and has_data:
            return {"type": "finish", "result": finish_match.group(1).strip()}
        
        if finish_match and not has_data:
            return {"type": "fallback", "params": {"query": state["goal"]}}
        
        # 2. Try model-native format: <|message|>{"query":"..."} or <|channel|>...
        msg_match = re.search(r'<\|message\|>\s*(\{[^}]+\})', thought)
        if msg_match:
            try:
                params = json.loads(msg_match.group(1))
                query = params.get("query", params.get("search", state["goal"]))
                print(f"[PLANNER] Parsed model-native format, query: {query[:100]}")
                return {"type": "fallback", "params": {"query": query}}
            except json.JSONDecodeError:
                pass
        
        # 3. Try to extract any JSON with a "query" key
        json_match = re.search(r'\{[^{}]*"query"\s*:\s*"([^"]+)"[^{}]*\}', thought)
        if json_match:
            query = json_match.group(1)
            print(f"[PLANNER] Extracted query from JSON: {query[:100]}")
            return {"type": "fallback", "params": {"query": query}}
        
        # 4. If we already have data and format is unrecognized, finish
        if has_data:
            print(f"[PLANNER] Data gathered + unrecognized format -> finishing")
            return {"type": "finish", "result": "Analysis complete based on gathered data."}
        
        # 5. Last resort: use goal as query for qa playbook
        return {"type": "fallback", "params": {"query": state["goal"]}}

    def _has_gathered_data(self, state: PlannerState) -> bool:
        """Check if agent has gathered any codebase data via tools/playbooks."""
        for obs in state.get("observations", []):
            if obs.get("success"):
                return True
        return False
    
    async def _act(self, state: PlannerState) -> PlannerState:
        """Execute the selected playbook or tool."""
        print(f"\n[PLANNER] Act")
        await asyncio.sleep(0)  # Yield to event loop
        await self._emit_update(state)
        
        if not state["plan"]:
            print(f"[PLANNER] No plan to execute")
            state["observations"] = state.get("observations", []) + [{"error": "No action selected"}]
            return state
        
        action = state["plan"][0]
        
        if "tool" in action:
            # Direct tool call (no LLM, fast)
            tool_name = action["tool"]
            params = action.get("params", {})
            if "repo_id" not in params:
                params["repo_id"] = state["repo_id"]
            
            print(f"[PLANNER] Executing tool: {tool_name}")
            try:
                tools = self.executor.tools
                result = await tools.execute_tool(tool_name, params)
                
                state["actions"] = state.get("actions", []) + [action]
                state["observations"] = state.get("observations", []) + [{
                    "success": "error" not in result,
                    "outputs": result,
                    "source": "tool",
                }]
                print(f"[PLANNER] Tool returned: {list(result.keys())}")
            except Exception as e:
                print(f"[PLANNER] Tool error: {e}")
                state["actions"] = state.get("actions", []) + [action]
                state["observations"] = state.get("observations", []) + [{
                    "success": False, "error": str(e), "outputs": {}
                }]
        else:
            # Playbook call (LLM-powered)
            playbook_name = action["playbook"]
            user_input = action.get("params", {})
            user_input["goal"] = state["goal"]
            if "repo_id" not in user_input:
                user_input["repo_id"] = state["repo_id"]
            
            print(f"[PLANNER] Executing playbook: {playbook_name}")
            try:
                result = await self.executor.execute(playbook_name, user_input)
                state["actions"] = state.get("actions", []) + [action]
                state["observations"] = state.get("observations", []) + [result]
                
                if result["success"]:
                    print(f"[PLANNER] Playbook succeeded")
                else:
                    print(f"[PLANNER] Playbook failed: {result.get('error')}")
            except Exception as e:
                print(f"[PLANNER] Execution error: {e}")
                state["actions"] = state.get("actions", []) + [action]
                state["observations"] = state.get("observations", []) + [{
                    "success": False, "error": str(e), "outputs": {}
                }]
        
        return state
    
    async def _observe(self, state: PlannerState) -> PlannerState:
        """Process observation from playbook/tool execution."""
        print(f"\n[PLANNER] Observe")
        await asyncio.sleep(0)  # Yield to event loop
        await self._emit_update(state)
        
        if state["observations"]:
            obs = state["observations"][-1]
            success = obs.get("success", False)
            
            if success:
                outputs = obs.get("outputs", {})
                if isinstance(outputs, dict):
                    for key, val in outputs.items():
                        if isinstance(val, str):
                            print(f"[PLANNER] [{key}]: {len(val)} chars")
                        elif isinstance(val, list):
                            print(f"[PLANNER] [{key}]: {len(val)} items")
                        else:
                            print(f"[PLANNER] [{key}]: {type(val).__name__}")
                else:
                    print(f"[PLANNER] Observed: {type(outputs).__name__}")
            else:
                print(f"[PLANNER] Error: {obs.get('error')}")
        
        return state
    
    async def _finish(self, state: PlannerState) -> PlannerState:
        """
        Synthesize final answer from execution history.
        Uses playbook output directly when available, otherwise synthesizes.
        """
        print(f"\n[PLANNER] Finish")
        await asyncio.sleep(0)  # Yield to event loop
        await self._emit_update(state)
        
        # Collect all successful outputs
        playbook_output = None
        all_data = []
        
        for obs in state.get("observations", []):
            if obs.get("success") and obs.get("outputs"):
                outputs = obs["outputs"]
                
                if isinstance(outputs, dict) and outputs.get("result"):
                    playbook_output = outputs["result"]
                
                if isinstance(outputs, dict):
                    for key, val in outputs.items():
                        if isinstance(val, str) and len(val) > 20:
                            all_data.append(val[:2000])
                        elif isinstance(val, list):
                            all_data.append(str(val[:10]))
        
        print(f"[PLANNER] Collected: playbook_output={'yes' if playbook_output else 'no'} ({len(playbook_output) if playbook_output else 0} chars), all_data={len(all_data)} items")
        
        # Extract action names (handle both "playbook" and "tool" keys)
        actions_used = []
        for a in state.get("actions", []):
            name = a.get("playbook") or a.get("tool") or "unknown"
            actions_used.append(name)
        
        if playbook_output:
            print(f"[PLANNER] ✅ Using playbook output directly ({len(playbook_output)} chars) — no LLM call needed")
            state["final_answer"] = {
                "goal": state["goal"],
                "answer": playbook_output,
                "steps_taken": len(state["actions"]),
                "iterations": state["iteration"],
                "playbooks_used": actions_used
            }
        elif all_data:
            print(f"[PLANNER] 🔄 Synthesizing from {len(all_data)} data sources — calling LLM...")
            
            data_context = "\n---\n".join(all_data[:5])
            
            synthesis_prompt = (
                "You are a code analysis assistant. Answer based ONLY on the data below.\n\n"
                "USER GOAL: " + state["goal"] + "\n\n"
                "GATHERED DATA:\n" + data_context[:8000] + "\n\n"
                "Provide a clear, detailed answer. Include file paths and code references.\n\n"
                "Your answer:"
            )
            
            try:
                # Use config max_tokens: ~10% for synthesis
                synth_tokens = max(512, self.llm.config.max_tokens // 10)
                print(f"[PLANNER] LLM synthesis call: prompt={len(synthesis_prompt)} chars, max_tokens={synth_tokens}")
                answer_text = await self.llm.generate(
                    synthesis_prompt,
                    system_prompt="You are a helpful code analysis assistant. Answer questions based only on the provided data.",
                    max_tokens=synth_tokens
                )
                print(f"[PLANNER] ✅ LLM synthesis complete: {len(answer_text)} chars")
                
                state["final_answer"] = {
                    "goal": state["goal"],
                    "answer": answer_text,
                    "steps_taken": len(state["actions"]),
                    "iterations": state["iteration"],
                    "playbooks_used": actions_used
                }
            except Exception as e:
                print(f"[PLANNER] Synthesis error: {e}")
                state["final_answer"] = {
                    "goal": state["goal"],
                    "answer": state.get("final_result", "Unable to complete goal"),
                    "steps_taken": len(state["actions"]),
                    "iterations": state["iteration"],
                    "playbooks_used": actions_used,
                    "error": str(e)
                }
        else:
            print(f"[PLANNER] No data gathered, using final_result")
            state["final_answer"] = {
                "goal": state["goal"],
                "answer": state.get("final_result", "Unable to gather information from the codebase."),
                "steps_taken": len(state["actions"]),
                "iterations": state["iteration"],
                "playbooks_used": actions_used
            }
        
        return state
    
    def _should_continue(self, state: PlannerState) -> Literal["act", "finish"]:
        """Decide whether to continue executing or finish."""
        if state["finished"]:
            return "finish"
        
        if state["iteration"] >= state["max_iterations"]:
            print(f"[PLANNER] Max iterations ({state['max_iterations']}) reached")
            state["finished"] = True
            state["final_result"] = "Maximum iterations reached"
            return "finish"
        
        if not state["plan"]:
            if not self._has_gathered_data(state):
                print(f"[PLANNER] No plan but no data yet. Forcing qa playbook.")
                state["plan"] = [{"playbook": "qa", "params": {"query": state["goal"]}}]
                return "act"
            
            print(f"[PLANNER] No plan, finishing")
            state["finished"] = True
            state["final_result"] = "No plan available"
            return "finish"
        
        return "act"
    
    def _format_history(self, state: PlannerState) -> str:
        """Format history with action names AND result summaries."""
        iteration = state["iteration"]
        
        if iteration == 0:
            return "No actions taken yet. You MUST use a PLAYBOOK or TOOL first."
        
        actions = state.get("actions", [])
        observations = state.get("observations", [])
        
        start_idx = max(0, len(actions) - 3)
        recent_actions = actions[start_idx:]
        recent_obs = observations[start_idx:]
        
        history = []
        for i, (action, obs) in enumerate(zip(recent_actions, recent_obs), start=start_idx + 1):
            name = action.get("playbook") or action.get("tool") or "unknown"
            action_type = "PLAYBOOK" if "playbook" in action else "TOOL"
            success = "OK" if obs.get("success") else "FAIL"
            
            line = f"{i}. [{action_type}] {name} ({success})"
            
            if obs.get("success") and obs.get("outputs"):
                outputs = obs["outputs"]
                if isinstance(outputs, dict):
                    result_text = outputs.get("result", "")
                    if result_text:
                        line += f"\n   Preview: {result_text[:300]}..."
                    else:
                        summaries = []
                        for key, val in outputs.items():
                            if isinstance(val, str):
                                summaries.append(f"{key}: {len(val)} chars")
                            elif isinstance(val, list):
                                summaries.append(f"{key}: {len(val)} items")
                        if summaries:
                            line += f"\n   Data: {', '.join(summaries)}"
            elif obs.get("error"):
                line += f"\n   Error: {obs['error'][:200]}"
            
            history.append(line)
        
        return "\n".join(history) if history else "No actions taken yet."

    def _format_playbooks_for_prompt(self) -> str:
        """Format playbooks concisely."""
        playbooks = []
        for name in self.registry.list_playbooks():
            playbook = self.registry.get_playbook(name)
            playbooks.append(f"- {name}: {playbook.when_to_use[:200]}")
        return "\n".join(playbooks)

    def _format_tools_for_prompt(self) -> str:
        """Format tool descriptions for the thinking prompt."""
        from codemind.playbooks.tools import PlaybookTools
        tools = PlaybookTools.get_tool_descriptions()
        lines = []
        for t in tools:
            lines.append(f"- {t['name']}: {t['description']}")
        return "\n".join(lines)

    async def execute(self, goal: str, repo_id: str, max_iterations: int = 10, on_update=None) -> dict:
        """Execute planner for a goal."""
        self.on_update = on_update  # Register callback
        
        print(f"\n{'='*60}")
        print(f"[PLANNER] Starting autonomous execution")
        print(f"[PLANNER] Goal: {goal}")
        print(f"[PLANNER] Repo: {repo_id}")
        print(f"{'='*60}")
        
        initial_state: PlannerState = {
            "goal": goal,
            "repo_id": repo_id,
            "plan": [],
            "current_step": 0,
            "thoughts": [],
            "actions": [],
            "observations": [],
            "iteration": 0,
            "max_iterations": max_iterations,
            "finished": False,
            "final_result": "",
            "final_answer": None
        }
        
        try:
            result = await self.workflow.ainvoke(initial_state)
            
            print(f"\n{'='*60}")
            print(f"[PLANNER] Execution complete")
            print(f"[PLANNER] Steps: {len(result['actions'])}")
            print(f"[PLANNER] Iterations: {result['iteration']}")
            print(f"{'='*60}\n")
            
            return result["final_answer"]
        
        except Exception as e:
            print(f"\n[PLANNER] Execution failed: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "goal": goal,
                "answer": f"Execution failed: {e}",
                "steps_taken": 0,
                "iterations": 0,
                "error": str(e)
            }
