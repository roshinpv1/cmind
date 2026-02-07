"""
Autonomous Planner Agent - LLM-powered skill selection and execution.

The planner:
1. Interprets user goals
2. Selects appropriate skills
3. Executes skills via executor
4. Observes results
5. Iterates until goal satisfied
6. Returns final answer

This is the "brain" of the autonomous agent system.
"""

from typing import Literal
from langgraph.graph import StateGraph, END
import json
import re

from .planner_state import PlannerState


class PlannerAgent:
    """
    Autonomous planner that selects and executes skills to achieve goals.
    
    Uses LLM for reasoning and skill selection.
    Uses SkillExecutor for deterministic execution.
    """
    
    def __init__(self, registry, executor, llm_client):
        """
        Initialize planner agent.
        
        Args:
            registry: SkillRegistry with available skills
            executor: SkillExecutor for running skills
            llm_client: LLM for reasoning and planning
        """
        self.registry = registry
        self.executor = executor
        self.llm = llm_client
        self.workflow = self._build_workflow()
    
    def _build_workflow(self) -> StateGraph:
        """Build the think-act-observe workflow."""
        graph = StateGraph(PlannerState)
        
        # Add nodes
        graph.add_node("think", self._think)
        graph.add_node("act", self._act)
        graph.add_node("observe", self._observe)
        graph.add_node("finish", self._finish)
        
        # Entry point
        graph.set_entry_point("think")
        
        # Conditional routing from think
        graph.add_conditional_edges(
            "think",
            self._should_continue,
            {
                "act": "act",
                "finish": "finish"
            }
        )
        
        # act → observe → think (loop)
        graph.add_edge("act", "observe")
        graph.add_edge("observe", "think")
        
        # finish → END
        graph.add_edge("finish", END)
        
        return graph.compile()
    
    async def _think(self, state: PlannerState) -> PlannerState:
        """
        Agent thinks about what to do next.
        
        Uses LLM to:
        - Analyze current situation
        - Decide if goal is satisfied
        - Select next skill if needed
        """
        print(f"\n[PLANNER] 🤔 Think (iteration {state['iteration']})")

        # Build thinking prompt
        skills_desc = self._format_skills_for_prompt()
        
        thinking_prompt = f"""Goal: {state['goal']}

## Available Skills (use EXACT name from this list)
{skills_desc}

## Response Format
Pick ONE skill or finish. Reply with ONLY one of these formats:

SKILL: <name>
PARAMS: {{"query": "<relevant search query>"}}

OR

FINISH: <summary of result>

Your response:
"""
        
        # Get LLM reasoning
        try:
            thought = await self.llm.generate(thinking_prompt, max_tokens=200)
            print(f"[PLANNER] Thought: {thought[:200]}...")
            
            state["thoughts"] = [thought]
            state["iteration"] += 1
            
            # Parse LLM output
            skill_match = re.search(r'SKILL:\s*(.+)', thought)
            if skill_match:
                skill_name = skill_match.group(1).strip().strip('"\'')
                
                # Phase-1 restriction: ONE skill only
                if len(state["actions"]) > 0:
                    print(f"[PLANNER] ⚠️ Phase-1: Already executed a skill. Forcing FINISH.")
                    state["finished"] = True
                    state["final_result"] = "Task completed after single skill execution."
                else:
                    # Extract params
                    params = {}
                    params_match = re.search(r'PARAMS:\s*({.*})', thought, re.DOTALL)
                    if params_match:
                        try:
                            params = json.loads(params_match.group(1).strip())
                        except:
                            print("[PLANNER] ⚠️ Failed to parse PARAMS json")
                    
                    # Let the LLM's choice go through — trust the LLM
                    if self.registry.get_skill(skill_name):
                        print(f"[PLANNER] ✓ Selected skill: {skill_name}")
                        state["plan"] = [{"skill": skill_name, "params": params}]
                    else:
                        # Skill not found — log and let agent know
                        available = self.registry.list_skills()
                        print(f"[PLANNER] ✗ Skill '{skill_name}' not in registry. Available: {available}")
                        state["thoughts"].append(
                            f"Skill '{skill_name}' does not exist. Available skills: {available}"
                        )
            
            elif "FINISH:" in thought:
                state["finished"] = True
                finish_match = re.search(r'FINISH:\s*(.+)', thought, re.DOTALL)
                state["final_result"] = finish_match.group(1).strip() if finish_match else "Goal completed"
                print(f"[PLANNER] ✓ Agent decided to finish")
            
            else:
                # LLM didn't follow format — log and let it retry
                print(f"[PLANNER] ⚠ LLM output didn't match expected format. Will retry.")
                state["thoughts"].append("Output did not match SKILL: or FINISH: format.")
        
        except Exception as e:
            print(f"[PLANNER] ✗ Think error: {e}")
            state["finished"] = True
            state["final_result"] = f"Error in planning: {e}"
        
        return state
    
    async def _act(self, state: PlannerState) -> PlannerState:
        """
        Execute the selected skill.
        """
        print(f"\n[PLANNER] ⚡ Act")
        
        if not state["plan"]:
            print(f"[PLANNER] ✗ No plan to execute")
            state["observations"] = [{"error": "No skill selected"}]
            return state
        
        action = state["plan"][0]
        skill_name = action["skill"]
        user_input = action["params"]  # Now called user_input
        
        # Add goal to user_input
        user_input["goal"] = state["goal"]
        if "repo_id" not in user_input:
            user_input["repo_id"] = state["repo_id"]
        
        print(f"[PLANNER] Executing: {skill_name}")
        print(f"[PLANNER] User input: {user_input}")
        
        try:
            # Execute skill via executor
            result = await self.executor.execute(skill_name, user_input)
            
            # Record action and observation
            state["actions"] = [action]
            state["observations"] = [result]
            
            if result["success"]:
                print(f"[PLANNER] ✓ Skill succeeded")
            else:
                print(f"[PLANNER] ✗ Skill failed: {result.get('error')}")
        
        except Exception as e:
            print(f"[PLANNER] ✗ Execution error: {e}")
            state["actions"] = [action]
            state["observations"] = [{
                "success": False,
                "error": str(e),
                "outputs": {}
            }]
        
        return state
    
    async def _observe(self, state: PlannerState) -> PlannerState:
        """
        Process observation from skill execution.
        
        This is mostly a pass-through, but could include:
        - Result validation
        - Metric tracking
        - Logging
        """
        print(f"\n[PLANNER] 👁️  Observe")
        
        if state["observations"]:
            obs = state["observations"][-1]
            success = obs.get("success", False)
            
            if success:
                outputs = obs.get("outputs", {})
                print(f"[PLANNER] Observed: {list(outputs.keys())}")
            else:
                print(f"[PLANNER] Observed error: {obs.get('error')}")
        
        return state
    
    async def _finish(self, state: PlannerState) -> PlannerState:
        """
        Synthesize final answer from execution history.
        
        Phase-1: Single skill execution — use skill output directly.
        """
        print(f"\n[PLANNER] 🏁 Finish")
        
        # Check if we have successful skill output — use it directly
        skill_output = None
        if state["observations"]:
            last_obs = state["observations"][-1]
            if last_obs.get("success") and last_obs.get("outputs"):
                outputs = last_obs["outputs"]
                # The skill executor puts the LLM-generated result in 'result'
                skill_output = outputs.get("result", "")
                if not skill_output:
                    # Fallback: try to get any string value from outputs
                    for v in outputs.values():
                        if isinstance(v, str) and len(v) > 50:
                            skill_output = v
                            break
        
        if skill_output:
            # Phase-1: Skill already generated a grounded answer via LLM — use it
            print(f"[PLANNER] Using skill output directly ({len(skill_output)} chars)")
            state["final_answer"] = {
                "goal": state["goal"],
                "answer": skill_output,
                "steps_taken": len(state["actions"]),
                "iterations": state["iteration"],
                "skills_used": [a["skill"] for a in state["actions"]]
            }
        else:
            # No skill output — synthesize from whatever we have
            print(f"[PLANNER] No skill output, synthesizing answer...")
            
            # Include observation details if available
            context = ""
            if state["observations"]:
                obs = state["observations"][-1]
                if obs.get("error"):
                    context = f"\nError encountered: {obs['error']}"
                if obs.get("logs"):
                    context += f"\nLogs: {obs['logs'][-3:]}"
            
            synthesis_prompt = f"""You are answering a user's question about a codebase.

USER GOAL: {state['goal']}

EXECUTION SUMMARY:
{self._format_history(state)}
{context}

REASONING: {state['final_result']}

Provide a clear, concise answer based on the information above.
If no useful data was gathered, explain what happened and suggest next steps.

Your answer:"""
            
            try:
                answer_text = await self.llm.generate(synthesis_prompt, max_tokens=500)
                print(f"[PLANNER] Generated answer: {answer_text[:150]}...")
                
                state["final_answer"] = {
                    "goal": state["goal"],
                    "answer": answer_text,
                    "steps_taken": len(state["actions"]),
                    "iterations": state["iteration"],
                    "skills_used": [a["skill"] for a in state["actions"]]
                }
            
            except Exception as e:
                print(f"[PLANNER] ✗ Synthesis error: {e}")
                state["final_answer"] = {
                    "goal": state["goal"],
                    "answer": state.get("final_result", "Unable to complete goal"),
                    "steps_taken": len(state["actions"]),
                    "iterations": state["iteration"],
                    "error": str(e)
                }
        
        return state
    
    def _should_continue(self, state: PlannerState) -> Literal["act", "finish"]:
        """
        Decide whether to continue executing or finish.
        """
        # Check if finished
        if state["finished"]:
            return "finish"
        
        # Check iteration limit
        if state["iteration"] >= state["max_iterations"]:
            print(f"[PLANNER] ⚠ Max iterations ({state['max_iterations']}) reached")
            state["finished"] = True
            state["final_result"] = "Maximum iterations reached"
            return "finish"
        
        # Check if we have a plan
        if not state["plan"]:
            print(f"[PLANNER] ⚠ No plan, finishing")
            state["finished"] = True
            state["final_result"] = "No plan available"
            return "finish"
        
        return "act"
    
    
    def _format_history(self, state: PlannerState) -> str:
        """Format history concisely - only last 3 iterations."""
        iteration = state["iteration"]
        
        if iteration == 0:
            return "None (iteration 0 - MUST pick a skill!)"
        
        # Only show last 3 iterations to keep prompt short
        actions = state["actions"]
        observations = state["observations"]
        
        # Get last 3 actions
        start_idx = max(0, len(actions) - 3)
        recent_actions = actions[start_idx:]
        recent_obs = observations[start_idx:]
        
        history = []
        for i, (action, obs) in enumerate(zip(recent_actions, recent_obs), start=start_idx + 1):
            skill = action.get("skill", "?")
            success = "✓" if obs.get("success") else "✗"
            history.append(f"{i}. {skill} {success}")
        
        return ", ".join(history)

    def _format_skills_for_prompt(self) -> str:
        """Format skills concisely."""
        skills = []
        for name in self.registry.list_skills():
            skill = self.registry.get_skill(name)
            skills.append(f"- {name}: {skill.when_to_use[:200]}")
        return "\n".join(skills)

    async def execute(self, goal: str, repo_id: str, max_iterations: int = 10) -> dict:
        """
        Execute planner for a goal.
        
        Args:
            goal: Natural language goal
            repo_id: Repository to work with
            max_iterations: Maximum iterations (safety limit)
            
        Returns:
            Final answer dict with goal, answer, steps, iterations
        """
        print(f"\n{'='*60}")
        print(f"[PLANNER] 🚀 Starting autonomous execution")
        print(f"[PLANNER] Goal: {goal}")
        print(f"[PLANNER] Repo: {repo_id}")
        print(f"{'='*60}")
        
        # Initialize state
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
        
        # Execute workflow
        try:
            result = await self.workflow.ainvoke(initial_state)
            
            print(f"\n{'='*60}")
            print(f"[PLANNER] ✅ Execution complete")
            print(f"[PLANNER] Steps: {len(result['actions'])}")
            print(f"[PLANNER] Iterations: {result['iteration']}")
            print(f"{'='*60}\n")
            
            return result["final_answer"]
        
        except Exception as e:
            print(f"\n[PLANNER] ✗ Execution failed: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "goal": goal,
                "answer": f"Execution failed: {e}",
                "steps_taken": 0,
                "iterations": 0,
                "error": str(e)
            }
