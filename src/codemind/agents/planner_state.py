"""
Planner state definition for autonomous agent execution.

Uses LangGraph's MessagesState for native tool calling support,
extended with cmind-specific fields for goal tracking and control.
"""

from typing import TypedDict, Annotated, Optional
from langgraph.graph import MessagesState
import operator


class PlannerState(MessagesState):
    """
    State for autonomous planner agent.
    
    Extends LangGraph's MessagesState (which provides a `messages` list)
    with cmind-specific fields for goal tracking and execution control.
    
    The planner iterates through think → tool_call → observe until 
    goal is satisfied.
    """
    
    # === Input ===
    goal: str  # User's natural language goal
    repo_id: Optional[str]  # Repository to work with (None for global)
    allowed_playbooks: Optional[list[str]]  # Whitelist of playbooks (None = all)
    
    # === Planning ===
    plan: list[dict]  # Current plan: [{"playbook": "...", "params": {...}}]
    current_step: int  # Which step we're on
    
    # === Execution History (append-only) ===
    thoughts: Annotated[list[str], operator.add]  # Agent's reasoning
    actions: Annotated[list[dict], operator.add]  # Playbooks executed
    observations: Annotated[list[dict], operator.add]  # Results from playbooks
    
    # === Control ===
    iteration: int  # Current iteration number
    max_iterations: int  # Safety limit
    finished: bool  # Whether goal is satisfied
    
    # === Output ===
    final_result: str  # Final reasoning/answer
    final_answer: Optional[dict]  # Structured final output
