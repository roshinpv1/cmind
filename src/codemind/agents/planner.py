"""
Autonomous Planner Agent — LangGraph + LangChain powered.

The planner:
1. Interprets user goals
2. Uses LLM with bound tools to select playbooks/tools
3. LangGraph ToolNode auto-dispatches tool calls
4. Observes results and iterates until goal satisfied
5. Synthesizes final answer grounded in codebase data

Replaces custom _parse_action / _CHANNEL_TO_TOOL parsing with
LangGraph's native tool calling via CmindChatModel.bind_tools().
"""

from typing import Literal, Optional
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage
)
import asyncio
import json
import uuid

from .planner_state import PlannerState


def create_playbook_meta_tools(registry, executor, allowed_playbooks=None):
    """Create LangChain tools that wrap playbook execution.
    
    Each playbook becomes a callable tool that the planner LLM can invoke.
    This replaces the old PLAYBOOK:/TOOL: format parsing.
    """
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field
    
    meta_tools = []
    
    for name in registry.list_playbooks():
        if allowed_playbooks and name not in allowed_playbooks:
            continue
        
        playbook = registry.get_playbook(name)
        if not playbook:
            continue
        
        # Create a tool for each playbook dynamically
        pb_name = name
        pb_desc = playbook.when_to_use[:500] if playbook.when_to_use else f"Execute {name} playbook"
        
        class PlaybookInput(BaseModel):
            """Input for playbook execution."""
            query: str = Field(description="Search query or goal for the playbook")
            repo_id: Optional[str | list[str]] = Field(default=None, description="Repository ID or list of IDs (optional)")
        
        # We need a factory to capture pb_name in closure
        def make_pb_tool(pb_name_inner, pb_desc_inner):
            @tool(f"playbook_{pb_name_inner}", args_schema=PlaybookInput)
            async def run_playbook(query: str, repo_id: Optional[str | list[str]] = None) -> str:
                f"""Execute the {pb_name_inner} playbook. {pb_desc_inner}"""
                user_input = {"query": query, "goal": query}
                if repo_id:
                    user_input["repo_id"] = repo_id
                
                try:
                    result = await executor.execute(pb_name_inner, user_input)
                    if result.get("success"):
                        outputs = result.get("outputs", {})
                        # If a tool was executed (e.g. save_catalog_entry),
                        # return the human-readable result so the planner
                        # can detect completion and auto-finish.
                        if outputs.get("tool_executed"):
                            return outputs.get("result", "Tool executed successfully.")
                        # Return the structured data if available so _finish parses it natively
                        if outputs.get("data"):
                            # Pydantic dicts might have non-serializable objects (like dates), default=str
                            return json.dumps(outputs["data"], default=str)
                        elif outputs.get("result"):
                            truncated = str(outputs["result"])[:3800]
                            return json.dumps({"result": truncated})
                        return json.dumps(outputs, default=str)[:4000]
                    else:
                        return json.dumps({"error": result.get("error", "Playbook failed")})
                except Exception as e:
                    return json.dumps({"error": str(e)})
            
            # Override the docstring and description after creation
            run_playbook.description = f"Execute the {pb_name_inner} playbook. {pb_desc_inner}"
            return run_playbook
        
        meta_tools.append(make_pb_tool(pb_name, pb_desc))
    
    return meta_tools


class PlannerAgent:
    """
    Autonomous planner that uses LangGraph ToolNode for action dispatch.
    
    The LLM decides what to do by making tool calls. Each tool call is 
    automatically dispatched by ToolNode. Playbooks are exposed as 
    "meta-tools" (playbook_search_catalogs, playbook_code_analyzer, etc.).
    
    This eliminates:
    - _parse_action() regex parsing
    - _CHANNEL_TO_TOOL / _CHANNEL_TO_PLAYBOOK mappings
    - _format_tools_for_prompt() manual schema generation
    - Model-native format handling
    """
    
    def __init__(self, registry, executor, llm_client):
        self.registry = registry
        self.executor = executor
        self.llm = llm_client  # Legacy LLMDriver (kept for _finish synthesis)
        
        # CmindChatModel for tool calling — created lazily per execute() call
        # because allowed_playbooks changes which tools are available
        self._chat_model = None
    
    def _get_chat_model(self):
        """Get or create the CmindChatModel wrapper."""
        if self._chat_model is None:
            from ..llm.chat_wrapper import CmindChatModel
            self._chat_model = CmindChatModel(driver=self.llm)
        return self._chat_model
    
    def _create_tools(self, allowed_playbooks=None):
        """Create all available tools (data tools + playbook meta-tools)."""
        from ..playbooks.langchain_tools import create_langchain_tools
        
        # Data tools (search_codebase, read_file, etc.)
        data_tools = create_langchain_tools(self.executor.tools)
        
        # Playbook meta-tools
        playbook_tools = create_playbook_meta_tools(
            self.registry, self.executor, allowed_playbooks
        )
        
        # If playbooks are constrained, only include relevant data tools
        if allowed_playbooks:
            relevant = set()
            PLAYBOOK_TOOLS = {
                "search_catalogs": {"search_catalogs"},
                "generate_catalog": {"search_codebase", "save_catalog_entry"},
                "code_analyzer": {"search_codebase", "read_file", "search_symbol",
                                 "get_callers", "get_callees", "get_dependencies", "list_files"},
                "explore_codebase": {"search_codebase", "read_file", "search_symbol",
                                 "get_callers", "get_callees", "get_dependencies", "list_files"},
                "design_solution": {"search_catalogs"},
                "evaluate_build_vs_reuse": {"search_catalogs"},
            }
            for pb in allowed_playbooks:
                relevant |= PLAYBOOK_TOOLS.get(pb, set())
            
            if relevant:
                data_tools = [t for t in data_tools if t.name in relevant]
        
        return data_tools + playbook_tools
    
    def _build_workflow(self, tools):
        """Build the LangGraph workflow with ToolNode."""
        chat_model = self._get_chat_model()
        self._llm_with_tools = chat_model.bind_tools(tools)
        
        graph = StateGraph(PlannerState)
        
        graph.add_node("think", self._think)
        graph.add_node("tools", ToolNode(tools))
        graph.add_node("finish", self._finish)
        
        graph.set_entry_point("think")
        
        graph.add_conditional_edges(
            "think",
            self._route,
            {"tools": "tools", "finish": "finish"}
        )
        
        # After tool execution → back to think
        graph.add_edge("tools", "think")
        graph.add_edge("finish", END)
        
        # Compile with MemorySaver for checkpointing
        self._checkpointer = MemorySaver()
        return graph.compile(checkpointer=self._checkpointer)
    
    def _route(self, state: PlannerState) -> Literal["tools", "finish"]:
        """Route based on LLM's decision."""
        if state.get("finished"):
            return "finish"
        
        # Enforce max_iterations
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", 10)
        if iteration >= max_iter:
            print(f"[PLANNER] Max iterations ({max_iter}) reached")
            state["finished"] = True
            state["final_result"] = "Maximum iterations reached"
            return "finish"
        
        # Check if the last message has tool calls
        messages = state.get("messages", [])
        if messages and isinstance(messages[-1], AIMessage):
            last = messages[-1]
            if hasattr(last, 'tool_calls') and last.tool_calls:
                return "tools"
        
        # No tool calls means the LLM wants to finish
        return "finish"
    
    async def _emit_update(self, state: PlannerState):
        """Emit state update if callback is registered."""
        if hasattr(self, "on_update") and self.on_update:
            try:
                await self.on_update(state)
            except Exception as e:
                print(f"[PLANNER] Callback error: {e}")
    
    async def _think(self, state: PlannerState) -> dict:
        """
        Agent thinks about what to do next.
        
        Uses LLM with bound tools — the LLM's response will contain
        either tool_calls (to act) or plain text (to finish).
        """
        iteration = state.get("iteration", 0)
        print(f"\n[PLANNER] 🤔 Think (iteration {iteration})")
        await asyncio.sleep(0)
        await self._emit_update(state)
        
        # Count successful observations (legacy format)
        successful_runs = sum(
            1 for obs in state.get("observations", [])
            if obs.get("success") and obs.get("outputs")
        )
        
        # Also count ToolMessages in state.messages (new format from ToolNode)
        tool_messages = [
            m for m in state.get("messages", [])
            if isinstance(m, ToolMessage) and m.content and len(m.content) > 5
        ]
        successful_runs += len(tool_messages)
        has_data = successful_runs > 0
        
        # Auto-finish if last tool message indicates a terminal action
        terminal_signals = [
            "Catalog entry generated and saved",
            "catalog entry saved",
            '"tool_executed": true',
            '"tool_executed":true',
        ]
        # Also auto-finish when a playbook returns a large structured JSON result
        # (e.g. analyze_svp, analyze_tech_debt, evaluate_build_vs_reuse, etc.)
        json_completion_keys = [
            "report_markdown", "executive_summary", "product_name",
            "overall_health_score", "findings",
            "build_estimate", "reuse_estimate",
            "requirement_summary",
        ]
        if tool_messages:
            last_tool_content = tool_messages[-1].content or ""
            # Check string-based terminal signals
            if any(sig.lower() in last_tool_content.lower() for sig in terminal_signals):
                print(f"[PLANNER] Auto-finishing: terminal tool result detected")
                return {
                    "finished": True,
                    "final_result": last_tool_content,
                    "iteration": iteration + 1,
                }
            # Check if it's a large JSON result from a completed playbook
            if len(last_tool_content) > 500:
                try:
                    parsed_check = json.loads(last_tool_content)
                    if isinstance(parsed_check, dict) and any(k in parsed_check for k in json_completion_keys):
                        print(f"[PLANNER] Auto-finishing: large JSON playbook result detected ({len(last_tool_content)} chars)")
                        return {
                            "finished": True,
                            "final_result": last_tool_content,
                            "iteration": iteration + 1,
                        }
                except (json.JSONDecodeError, TypeError):
                    pass
        
        # Auto-finish after many successful runs
        if successful_runs >= 10:
            print(f"[PLANNER] Auto-finishing: {successful_runs} successful data retrievals")
            return {
                "finished": True,
                "final_result": "Auto-finish: sufficient data gathered.",
                "iteration": iteration + 1,
            }
        
        # Build the system prompt
        history_desc = self._format_history(state)
        
        if has_data:
            finish_instruction = (
                f"\nYou already have data from {successful_runs} successful queries. "
                "If you have enough information to answer the goal, respond with a final text answer "
                "(do NOT call any tool). If you need more data, call a tool.\n"
                "Do NOT repeat the same tool with similar queries."
            )
        else:
            finish_instruction = (
                "\nYou MUST call a tool or playbook first — you have no data yet. "
                "Do NOT respond with a text answer yet."
            )
        
        system_msg = SystemMessage(content=(
            "You are a code analysis agent. You have tools available to search code, "
            "analyze repositories, and retrieve information.\n\n"
            "Use the available tools to gather information needed to answer the user's goal.\n"
            + finish_instruction
        ))
        
        # Build the user message with goal + history
        user_content = f"Goal: {state['goal']}\n\n"
        if state.get("repo_id"):
            user_content += f"Repository ID: {state['repo_id']}\n\n"
        user_content += f"History:\n{history_desc}\n\nYour action:"
        
        user_msg = HumanMessage(content=user_content)
        
        # Collect messages: system + conversation history + current
        messages = [system_msg]
        
        # Add existing messages from state (previous tool calls/results)
        existing = state.get("messages", [])
        # Only add recent messages to avoid context overflow
        if existing:
            messages.extend(existing[-6:])  # Keep last 3 exchanges
        
        messages.append(user_msg)
        
        try:
            # Invoke LLM with tools
            config = getattr(self.llm, 'config', None)
            think_tokens = max(256, (config.max_tokens if config else 4096) // 20)
            
            response = await self._llm_with_tools.ainvoke(
                messages,
                max_tokens=think_tokens,
                temperature=0.1
            )
            
            # response is an AIMessage (possibly with tool_calls)
            content = response.content or ""
            has_tool_calls = hasattr(response, 'tool_calls') and response.tool_calls
            
            print(f"[PLANNER] Response: {content[:200]}...")
            if has_tool_calls:
                for tc in response.tool_calls:
                    print(f"[PLANNER] Tool call: {tc['name']}({json.dumps(tc.get('args', {}))[:100]})")
            
            # Track thoughts
            thoughts = state.get("thoughts", []) + [content[:500]]
            
            # Build return updates
            updates = {
                "messages": [response],  # MessagesState appends this
                "thoughts": [content[:500]],
                "iteration": iteration + 1,
            }
            
            # If no tool calls and no data yet, force a fallback
            if not has_tool_calls and not has_data:
                print("[PLANNER] No tool call but no data yet — forcing fallback")
                fallback_pb = self._get_fallback_playbook(state.get("allowed_playbooks"))
                # Create a synthetic tool call
                from langchain_core.messages import AIMessage as AI
                fallback_msg = AI(
                    content="I need to gather data first.",
                    tool_calls=[{
                        "name": f"playbook_{fallback_pb}",
                        "args": {"query": state["goal"], "repo_id": state.get("repo_id")},
                        "id": f"fallback_{iteration}",
                        "type": "tool_call",
                    }]
                )
                updates["messages"] = [fallback_msg]
            
            # If no tool calls and has data — agent wants to finish
            elif not has_tool_calls and has_data:
                print("[PLANNER] LLM chose to finish (no tool calls)")
                updates["finished"] = True
                updates["final_result"] = content
            
            return updates
            
        except Exception as e:
            print(f"[PLANNER] Think error: {e}")
            import traceback
            traceback.print_exc()
            
            if not has_data:
                fallback_pb = self._get_fallback_playbook(state.get("allowed_playbooks"))
                from langchain_core.messages import AIMessage as AI
                fallback_msg = AI(
                    content=f"Think error: {e}. Falling back.",
                    tool_calls=[{
                        "name": f"playbook_{fallback_pb}",
                        "args": {"query": state["goal"], "repo_id": state.get("repo_id")},
                        "id": f"error_fallback_{iteration}",
                        "type": "tool_call",
                    }]
                )
                return {
                    "messages": [fallback_msg],
                    "thoughts": [f"Think error: {e}. Falling back to {fallback_pb}."],
                    "iteration": iteration + 1,
                }
            else:
                return {
                    "finished": True,
                    "final_result": f"Error in planning: {e}",
                    "iteration": iteration + 1,
                }
    
    def _get_fallback_playbook(self, allowed_playbooks=None):
        """Get the best fallback playbook, respecting allowed_playbooks constraint."""
        if allowed_playbooks:
            return allowed_playbooks[0]
        available = self.registry.list_playbooks()
        for preferred in ["search_catalogs", "code_analyzer"]:
            if preferred in available:
                return preferred
        return available[0] if available else "search_catalogs"
    
    async def _finish(self, state: PlannerState) -> dict:
        """
        Synthesize final answer from execution history.
        Uses playbook output directly when available, otherwise synthesizes.
        """
        print(f"\n[PLANNER] Finish")
        await asyncio.sleep(0)
        await self._emit_update(state)
        
        # Collect successful outputs from observations AND tool messages
        playbook_output = None
        all_data = []
        
        # Check observations (legacy format)
        for obs in state.get("observations", []):
            if obs.get("success") and obs.get("outputs"):
                outputs = obs["outputs"]
                if isinstance(outputs, dict):
                    # Prefer structured "data" dict (Pydantic-validated) over raw "result" string
                    structured_data = outputs.get("data")
                    if isinstance(structured_data, dict) and any(
                        k in structured_data for k in ("catalog_matches", "summary", "comparison", "build_estimate", "reuse_estimate", "report_markdown", "product_name")
                    ):
                        playbook_output = structured_data
                    elif "result" in outputs:
                        # Try to parse result string as JSON to get structured data
                        result_val = outputs["result"]
                        if isinstance(result_val, str):
                            try:
                                parsed_result = json.loads(result_val)
                                if isinstance(parsed_result, dict):
                                    playbook_output = parsed_result
                                else:
                                    playbook_output = result_val
                            except (json.JSONDecodeError, TypeError):
                                playbook_output = result_val
                        else:
                            playbook_output = result_val
                    elif any(k in outputs for k in ("catalog_matches", "summary", "comparison", "build_estimate", "reuse_estimate", "report_markdown", "product_name")):
                        playbook_output = outputs
                    
                    for key, val in outputs.items():
                        if isinstance(val, str) and len(val) > 20:
                            all_data.append(val[:2000])
                        elif isinstance(val, list):
                            all_data.append(str(val[:10]))
        
        # Also collect data from tool messages in the message history
        for msg in state.get("messages", []):
            if isinstance(msg, ToolMessage):
                content = msg.content
                if isinstance(content, str) and len(content) > 20:
                    # Check if it's a playbook result (JSON with schema keys)
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            # Prefer structured "data" dict if present
                            struct_data = parsed.get("data")
                            if isinstance(struct_data, dict) and any(
                                k in struct_data for k in ("catalog_matches", "summary", "comparison", "build_estimate", "reuse_estimate", "report_markdown", "product_name")
                            ):
                                playbook_output = struct_data
                            elif "result" in parsed:
                                result_val = parsed["result"]
                                if isinstance(result_val, str):
                                    try:
                                        inner = json.loads(result_val)
                                        if isinstance(inner, dict):
                                            playbook_output = inner
                                        else:
                                            playbook_output = result_val
                                    except (json.JSONDecodeError, TypeError):
                                        playbook_output = result_val
                                else:
                                    playbook_output = result_val
                            elif any(k in parsed for k in ("catalog_matches", "summary", "comparison", "build_estimate", "reuse_estimate", "report_markdown", "product_name")):
                                playbook_output = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass
                    all_data.append(content[:2000])
        
        print(f"[PLANNER] Collected: playbook_output={'yes' if playbook_output else 'no'}, all_data={len(all_data)} items")
        
        # Extract action names
        actions_used = []
        for msg in state.get("messages", []):
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    actions_used.append(tc.get("name", "unknown"))
        # Also from legacy actions
        for a in state.get("actions", []):
            name = a.get("playbook") or a.get("tool") or "unknown"
            actions_used.append(name)
        
        if playbook_output:
            # Output can be dict or a string
            chars = len(str(playbook_output))
            print(f"[PLANNER] ✅ Using playbook output directly ({chars} chars)")
            if isinstance(playbook_output, dict):
                print(f"[PLANNER] Output keys: {list(playbook_output.keys())}")
                if "report_markdown" in playbook_output:
                    print(f"[PLANNER] report_markdown: {len(playbook_output['report_markdown'])} chars")
            return {
                "final_answer": {
                    "goal": state["goal"],
                    "answer": playbook_output,
                    "steps_taken": len(actions_used),
                    "iterations": state.get("iteration", 0),
                    "playbooks_used": actions_used,
                }
            }
        elif all_data:
            print(f"[PLANNER] 🔄 Synthesizing from {len(all_data)} data sources")
            
            data_context = "\n---\n".join(all_data[:5])
            
            synthesis_prompt = (
                "You are a code analysis assistant. Answer based ONLY on the data below.\n\n"
                "USER GOAL: " + state["goal"] + "\n\n"
                "GATHERED DATA:\n" + data_context[:8000] + "\n\n"
                "Provide a clear, detailed answer. Synthesize the findings completely based on the data.\n\n"
                "Your answer:"
            )
            
            try:
                config = getattr(self.llm, 'config', None)
                synth_tokens = max(512, (config.max_tokens if config else 4096) // 10)
                answer_text = await self.llm.generate(
                    synthesis_prompt,
                    system_prompt="You are a helpful code analysis assistant. Answer questions based only on the provided data.",
                    max_tokens=synth_tokens
                )
                print(f"[PLANNER] ✅ Synthesis complete: {len(answer_text)} chars")
                
                return {
                    "final_answer": {
                        "goal": state["goal"],
                        "answer": answer_text,
                        "steps_taken": len(actions_used),
                        "iterations": state.get("iteration", 0),
                        "playbooks_used": actions_used,
                    }
                }
            except Exception as e:
                print(f"[PLANNER] Synthesis error: {e}")
                return {
                    "final_answer": {
                        "goal": state["goal"],
                        "answer": state.get("final_result", "Unable to complete goal"),
                        "steps_taken": len(actions_used),
                        "iterations": state.get("iteration", 0),
                        "playbooks_used": actions_used,
                        "error": str(e),
                    }
                }
        else:
            print(f"[PLANNER] No data gathered, using final_result")
            return {
                "final_answer": {
                    "goal": state["goal"],
                    "answer": state.get("final_result", "Unable to gather information from the codebase."),
                    "steps_taken": len(actions_used),
                    "iterations": state.get("iteration", 0),
                    "playbooks_used": actions_used,
                }
            }
    
    def _format_history(self, state: PlannerState) -> str:
        """Format history from message list + legacy observations."""
        iteration = state.get("iteration", 0)
        
        if iteration == 0:
            return "No actions taken yet. You MUST use a tool or playbook first."
        
        history = []
        
        # Format from messages (new style)
        messages = state.get("messages", [])
        tool_call_idx = 0
        for msg in messages[-6:]:  # Last few messages
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_call_idx += 1
                    history.append(f"{tool_call_idx}. [TOOL CALL] {tc['name']}({json.dumps(tc.get('args', {}))[:200]})")
            elif isinstance(msg, ToolMessage):
                content = msg.content[:300] if msg.content else "empty"
                history.append(f"   Result: {content}...")
        
        # Also include legacy observations
        actions = state.get("actions", [])
        observations = state.get("observations", [])
        start_idx = max(0, len(actions) - 3)
        
        for i, (action, obs) in enumerate(zip(actions[start_idx:], observations[start_idx:]), start=start_idx + 1):
            name = action.get("playbook") or action.get("tool") or "unknown"
            action_type = "PLAYBOOK" if "playbook" in action else "TOOL"
            success = "OK" if obs.get("success") else "FAIL"
            
            line = f"{i + tool_call_idx}. [{action_type}] {name} ({success})"
            if obs.get("success") and obs.get("outputs"):
                outputs = obs["outputs"]
                if isinstance(outputs, dict) and outputs.get("result"):
                    line += f"\n   Preview: {outputs['result'][:300]}..."
            elif obs.get("error"):
                line += f"\n   Error: {obs['error'][:200]}"
            
            history.append(line)
        
        return "\n".join(history) if history else "No actions taken yet."
    
    async def execute(self, goal: str, repo_id: str | list[str] | None = None,
                      max_iterations: int = 10, on_update=None,
                      allowed_playbooks: list[str] = None,
                      thread_id: str | None = None) -> dict:
        """Execute planner for a goal.
        
        Same interface as before — drop-in replacement.
        """
        self.on_update = on_update
        
        print(f"\n{'='*60}")
        print(f"[PLANNER] Starting autonomous execution")
        print(f"[PLANNER] Goal: {goal}")
        print(f"[PLANNER] Repo: {repo_id or 'ALL'}")
        if allowed_playbooks:
            print(f"[PLANNER] Allowed Playbooks: {allowed_playbooks}")
        print(f"{'='*60}")
        
        # Create tools based on allowed_playbooks
        tools = self._create_tools(allowed_playbooks)
        print(f"[PLANNER] Available tools: {[t.name for t in tools]}")
        
        # Build workflow with these tools
        workflow = self._build_workflow(tools)
        
        initial_state: PlannerState = {
            "messages": [],  # MessagesState field
            "goal": goal,
            "repo_id": repo_id,
            "allowed_playbooks": allowed_playbooks,
            "plan": [],
            "current_step": 0,
            "thoughts": [],
            "actions": [],
            "observations": [],
            "iteration": 0,
            "max_iterations": max_iterations,
            "finished": False,
            "final_result": "",
            "final_answer": None,
        }
        
        try:
            # Generate thread_id for checkpointing if not provided
            if not thread_id:
                thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}}
            print(f"[PLANNER] Thread ID: {thread_id}")
            
            result = await workflow.ainvoke(initial_state, config=config)
            
            print(f"\n{'='*60}")
            print(f"[PLANNER] Execution complete")
            print(f"[PLANNER] Iterations: {result.get('iteration', 0)}")
            print(f"{'='*60}\n")
            
            return result.get("final_answer", {
                "goal": goal,
                "answer": result.get("final_result", "No result"),
                "steps_taken": 0,
                "iterations": result.get("iteration", 0),
            })
        
        except Exception as e:
            print(f"\n[PLANNER] Execution failed: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "goal": goal,
                "answer": f"Execution failed: {e}",
                "steps_taken": 0,
                "iterations": 0,
                "error": str(e),
            }
