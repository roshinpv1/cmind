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
import os
import uuid

from .planner_state import PlannerState


def create_playbook_meta_tools(registry, executor, allowed_playbooks=None, enforced_repo_id=None):
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
        pb_desc = playbook.when_to_use if playbook.when_to_use else f"Execute {name} playbook"
        
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
                if enforced_repo_id:
                    user_input["repo_id"] = enforced_repo_id
                elif repo_id and repo_id != "latest":
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
                            return json.dumps({"result": str(outputs["result"])}, default=str)
                        return json.dumps(outputs, default=str)
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

    Parallel-execution safe: every call to execute() builds its own
    workflow with its own llm_with_tools binding captured in a closure.
    No per-execution state is stored on self.
    """

    def __init__(self, registry, executor, llm_client):
        self.registry = registry
        self.executor = executor
        self.llm = llm_client  # raw LLMDriver (kept for _finish synthesis)
        # CmindChatModel is a stateless wrapper — safe to share across calls.
        self._chat_model = None

    def _get_chat_model(self):
        if self._chat_model is None:
            from ..llm.chat_wrapper import CmindChatModel
            self._chat_model = CmindChatModel(driver=self.llm)
        return self._chat_model

    def _create_tools(self, allowed_playbooks=None, enforced_repo_id=None):
        """Return the tool list for this execution (pure, no side-effects)."""
        from ..playbooks.langchain_tools import create_langchain_tools

        data_tools = create_langchain_tools(
            self.executor.tools, enforced_repo_id=enforced_repo_id
        )
        playbook_tools = create_playbook_meta_tools(
            self.registry, self.executor, allowed_playbooks,
            enforced_repo_id=enforced_repo_id,
        )
        # When playbooks are constrained, strip generic data tools so the
        # planner cannot bypass the playbook-level context management.
        if allowed_playbooks:
            data_tools = []
        return data_tools + playbook_tools

    def _build_workflow(self, tools, llm_with_tools, on_update=None):
        """
        Build a LangGraph workflow for ONE execution.

        *llm_with_tools* and *on_update* are captured via closure so that
        two concurrent calls each use their own binding — never each other's.
        """
        graph = StateGraph(PlannerState)

        # ── think: captured per-call llm_with_tools, never touches self ──────
        async def think_node(state: PlannerState) -> dict:
            return await self._think(state, llm_with_tools, on_update=on_update)

        async def finish_node(state: PlannerState) -> dict:
            return await self._finish(state, on_update=on_update)

        graph.add_node("think", think_node)
        graph.add_node("tools", ToolNode(tools))
        graph.add_node("finish", finish_node)

        graph.set_entry_point("think")
        graph.add_conditional_edges(
            "think",
            self._route,
            {"tools": "tools", "finish": "finish"},
        )
        graph.add_edge("tools", "think")
        graph.add_edge("finish", END)

        return graph.compile(checkpointer=MemorySaver())
    
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
    
    @staticmethod
    async def _emit_update(state: PlannerState, on_update=None):
        """Emit state update if callback is registered (per-call closure, not self)."""
        if on_update:
            try:
                await on_update(state)
            except Exception as exc:
                print(f"[PLANNER] Callback error: {exc}")
    
    async def _think(self, state: PlannerState, llm_with_tools, on_update=None) -> dict:
        """
        Agent thinks about what to do next.

        *llm_with_tools* and *on_update* are passed explicitly (not from self)
        so concurrent executions each use their own binding.
        """
        iteration = state.get("iteration", 0)
        print(f"\n[PLANNER] 🤔 Think (iteration {iteration})")
        await asyncio.sleep(0)
        await self._emit_update(state, on_update=on_update)
        
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
            "catalog entry generated",
            '"tool_executed": true',
            '"tool_executed":true',
            "saved successfully",
            "entry saved for",
        ]
        # Also auto-finish when a playbook returns a large structured JSON result
        # (e.g. analyze_svp, analyze_tech_debt, evaluate_build_vs_reuse, etc.)
        json_completion_keys = [
            "report_markdown", "executive_summary", "product_name",
            "overall_health_score", "findings",
            "build_estimate", "reuse_estimate",
            "requirement_summary",
            # Catalog search & design_solution results
            "catalog_matches", "overall_confidence_score",
            "architecture_composition", "decomposition", "capabilities",
            # Security audit results (sentinel_mythos)
            "vulnerabilities", "security_findings", "audit_summary",
            "cve_findings", "risk_score", "severity",
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
        
        # Auto-finish if a playbook tool has already been executed
        # (prevents repeating the same playbook like generate_catalog)
        executed_playbooks = set()
        for msg in state.get("messages", []):
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.get("name", "").startswith("playbook_"):
                        executed_playbooks.add(tc["name"])
        if executed_playbooks and tool_messages:
            # A playbook has already run and returned results — auto-finish
            print(f"[PLANNER] Auto-finishing: playbook(s) already executed: {executed_playbooks}")
            last_content = tool_messages[-1].content or ""
            return {
                "finished": True,
                "final_result": last_content,
                "iteration": iteration + 1,
            }
        
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
        

        # Attach Graphify context to the System Message directly so it never slides out
        topo_hook = ""
        if state.get("topology_context"):
            topo_hook = f"\n\nGRAPHIFY PRE-FLIGHT REPORT (God Nodes):\n{state['topology_context']}\n"

        system_msg = SystemMessage(content=(
            "You are an intelligent code analysis agent with access to specialized playbooks and data tools.\n\n"
            "ROUTING RULES — always pick the MOST SPECIFIC playbook for the goal:\n"
            "- Security analysis / audit / vulnerabilities / CVE / exploit → use sentinel_mythos_security_audit\n"
            "- PII / data privacy / GDPR / personal data exposure → use detect_pii_exposure\n"
            "- Resiliency / chaos / circuit breaker / fault tolerance → use detect_resiliency_patterns\n"
            "- API endpoints / routes / REST / GraphQL discovery → use discover_api_endpoints\n"
            "- Catalog search / find a library / reuse → use search_catalogs\n"
            "- General code exploration / architecture questions → use explore_codebase\n"
            "- Where authentication / authorization / login / sessions / permissions live → use explore_codebase "
            "(use sentinel_mythos_security_audit only when the goal is security auditing, not general “where is auth”).\n"
            "- NEVER use explore_codebase for security or PII goals — use the specialist playbook.\n\n"
            "Use the available tools to gather information needed to answer the user's goal.\n"
            + finish_instruction
            + topo_hook
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
        from codemind.llm.context_manager import ContextCompactor
        compactor = ContextCompactor(llm_driver=self.llm, threshold_ratio=0.6)
        
        # Use dynamic compaction instead of static truncation
        messages.extend(await compactor.compact(state.get("messages", [])))
        
        messages.append(user_msg)
        
        try:
            # Invoke LLM with tools
            config = getattr(self.llm, 'config', None)
            cfg_mt = int(getattr(config, "max_tokens", 4096) or 4096) if config else 4096
            # First-turn routing = small JSON tool_calls; avoid huge budgets (slow on local LM servers).
            think_tokens = max(512, min(cfg_mt // 4, 4096))
            _hard = os.getenv("CODEMIND_PLANNER_THINK_MAX_TOKENS")
            if _hard and _hard.isdigit():
                think_tokens = min(think_tokens, max(256, int(_hard)))

            response = await llm_with_tools.ainvoke(
                messages,
                max_tokens=think_tokens,
                temperature=0.1,
            )
            
            # response is an AIMessage (possibly with tool_calls)
            content = response.content or ""
            has_tool_calls = bool(
                getattr(response, "tool_calls", None)
            )

            # Repair JSON tool_calls emitted as plain text (nested args break regex-only parse
            # in chat_wrapper, which previously left the model "finishing" with raw JSON as the answer)
            if not has_tool_calls and content:
                tool_names = getattr(llm_with_tools, "tool_names", None)
                if tool_names:
                    from codemind.llm.chat_wrapper import _extract_tool_calls as _parse_tc_from_text
                    rem, repaired = _parse_tc_from_text(content, tool_names)
                    if repaired:
                        from langchain_core.messages import AIMessage as AIM
                        response = AIM(content=rem or "", tool_calls=repaired)
                        has_tool_calls = True
                        print(f"[PLANNER] Repaired {len(repaired)} tool call(s) from model JSON text")

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
    
    async def _finish(self, state: PlannerState, on_update=None) -> dict:
        """
        Synthesize final answer from execution history.
        Uses playbook output directly when available, otherwise synthesizes.
        """
        print(f"\n[PLANNER] Finish")
        await asyncio.sleep(0)
        await self._emit_update(state, on_update=on_update)
        
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
                        k in structured_data for k in ("catalog_matches", "summary", "comparison", "build_estimate", "reuse_estimate", "report_markdown", "product_name", "overall_confidence_score", "architecture_composition")
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
                    elif any(k in outputs for k in ("catalog_matches", "summary", "comparison", "build_estimate", "reuse_estimate", "report_markdown", "product_name", "overall_confidence_score", "architecture_composition")):
                        playbook_output = outputs
                    
                    for key, val in outputs.items():
                        if isinstance(val, str) and len(val) > 20:
                            all_data.append(val[:8000])
                        elif isinstance(val, list):
                            all_data.append(str(val[:10]))
        
        # Also collect data from tool messages in the message history
        _STRUCTURED_KEYS = frozenset({
            "catalog_matches", "summary", "comparison", "build_estimate",
            "reuse_estimate", "report_markdown", "product_name",
            "overall_confidence_score", "architecture_composition",
        })
        for msg in state.get("messages", []):
            if isinstance(msg, ToolMessage):
                content = msg.content
                if isinstance(content, str) and len(content) > 0:
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            # ── New executor format: {success, outputs:{result, data, ...}} ──
                            outputs_dict = parsed.get("outputs") if isinstance(parsed.get("outputs"), dict) else None
                            if outputs_dict is not None:
                                # Prefer structured "data" sub-dict (Pydantic-validated output)
                                struct_data = outputs_dict.get("data")
                                if isinstance(struct_data, dict) and any(k in struct_data for k in _STRUCTURED_KEYS):
                                    playbook_output = struct_data
                                else:
                                    # Fall back to "result" string — this is the ReAct synthesis text
                                    result_val = outputs_dict.get("result")
                                    if result_val and str(result_val).strip():
                                        try:
                                            inner = json.loads(str(result_val))
                                            playbook_output = inner if isinstance(inner, dict) else str(result_val)
                                        except (json.JSONDecodeError, TypeError):
                                            playbook_output = str(result_val)
                            # ── Legacy flat format ──────────────────────────────────────────
                            elif any(k in parsed for k in _STRUCTURED_KEYS):
                                playbook_output = parsed
                            elif "result" in parsed:
                                result_val = parsed["result"]
                                if result_val and str(result_val).strip():
                                    try:
                                        inner = json.loads(str(result_val))
                                        playbook_output = inner if isinstance(inner, dict) else str(result_val)
                                    except (json.JSONDecodeError, TypeError):
                                        playbook_output = str(result_val)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    all_data.append(content[:8000])
        
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
            
            config = getattr(self.llm, 'config', None)
            cfg_max = config.max_tokens if config else 4096
            context_window = config.effective_context_window if config else cfg_max * 4
            
            data_context = "\n---\n".join(all_data[:5])
            
            synthesis_prompt = (
                "You are a code analysis assistant. Answer based ONLY on the data below.\n\n"
                "USER GOAL: " + state["goal"] + "\n\n"
                "GATHERED DATA:\n" + data_context[:max(8000, context_window * 3)] + "\n\n"
                "Provide a clear, detailed answer. Synthesize the findings completely based on the data.\n\n"
                "Your answer:"
            )
            
            try:
                synth_tokens = cfg_max  # Full output budget for synthesis
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
            fr = state.get("final_result") or ""
            # Model sometimes emitted tool-call JSON as "final text" when parsing failed earlier
            if isinstance(fr, str) and fr.strip().startswith("{"):
                try:
                    parsed = json.loads(fr.strip())
                    if isinstance(parsed, dict) and "tool_calls" in parsed and len(parsed) <= 3:
                        print("[PLANNER] final_result looks like unparsed tool JSON — not using as user answer")
                        fr = (
                            "The run stopped before a final synthesis. Tool calls were not executed. "
                            "Retry the job; if this persists, increase max_iterations or check the LLM output format."
                        )
                except (json.JSONDecodeError, TypeError):
                    pass
            print(f"[PLANNER] No data gathered, using final_result")
            return {
                "final_answer": {
                    "goal": state["goal"],
                    "answer": fr if fr else "Unable to gather information from the codebase.",
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
        print(f"\n{'='*60}")
        print(f"[PLANNER] Starting autonomous execution")
        print(f"[PLANNER] Goal: {goal}")
        print(f"[PLANNER] Repo: {repo_id or 'ALL'}")
        if allowed_playbooks:
            # Normalize to lowercase — playbook names are always lowercase
            allowed_playbooks = [p.lower().strip() for p in allowed_playbooks]
            print(f"[PLANNER] Allowed Playbooks: {allowed_playbooks}")
        print(f"{'='*60}")
        
        # Create tools and bind them — both are per-call, never stored on self
        tools          = self._create_tools(allowed_playbooks, enforced_repo_id=repo_id)
        llm_with_tools = self._get_chat_model().bind_tools(tools)
        print(f"[PLANNER] Available tools: {[t.name for t in tools]}")

        # Build workflow — llm_with_tools and on_update captured in closures, not on self
        workflow = self._build_workflow(tools, llm_with_tools, on_update=on_update)
        
        # Build PRE-FLIGHT Graphify topology context
        topology_context = self._build_architecture_context(repo_id)
        if topology_context:
            print("[PLANNER] Injected Graphify Pre-Flight Context successfully")
        
        initial_state: PlannerState = {
            "messages": [],  # MessagesState field
            "goal": goal,
            "repo_id": repo_id,
            "topology_context": topology_context,
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
            config = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": 150
            }
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

    def _build_architecture_context(self, repo_id: str | list[str] | None) -> str:
        """
        Executes a Graphify Pre-flight hook.
        Retrieves Topology (Communities & Key Components) directly from 
        Graphify DB to provide a high-level architectural map for the LLM.
        """
        if not repo_id or isinstance(repo_id, list):
            return ""
            
        try:
            from codemind.graph.graph_db import GraphifyAdapter
            db = GraphifyAdapter()
            G = db.get_graph(repo_id)
            if not G or len(G.nodes) == 0:
                return ""
                
            # Group nodes by community/cluster
            communities = {}
            for n, data in G.nodes(data=True):
                cid = data.get("community")
                if cid is None: continue
                if cid not in communities:
                    communities[cid] = {"files": set(), "symbols": []}
                
                if data.get("type") == "File":
                    communities[cid]["files"].add(data.get("label", n))
                elif data.get("type") in ("Function", "Class"):
                    communities[cid]["symbols"].append({
                        "label": data.get("label", n),
                        "degree": G.in_degree(n) if G.is_directed() else G.degree(n)
                    })

            if not communities:
                return ""

            report = "The codebase is organized into the following logical topological clusters:\n\n"
            # Sort communities by size/importance
            sorted_cids = sorted(communities.keys(), key=lambda c: len(communities[c]["files"]), reverse=True)
            
            for cid in sorted_cids[:5]:  # Top 5 communities
                cdata = communities[cid]
                files = sorted(list(cdata["files"]))[:5]
                symbols = sorted(cdata["symbols"], key=lambda s: s["degree"], reverse=True)[:3]
                
                report += f"### Module/Cluster {cid}\n"
                report += f"- Primary Files: {', '.join(files)}\n"
                if symbols:
                    sym_desc = ", ".join([f"{s['label']} (links: {s['degree']})" for s in symbols])
                    report += f"- Structural Pillars: {sym_desc}\n"
                report += "\n"
                
            report += "Use this topological map to orient your search towards the most relevant modules."
            return report
        except Exception as e:
            print(f"[PLANNER] Pre-flight Graphify hook failed: {e}")
            return ""
