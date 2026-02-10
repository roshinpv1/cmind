"""
Planner state definition for autonomous agent execution.

The planner uses this state to track:
- Goal and context
- Planning decisions
- Execution history (thoughts, actions, observations)
- Termination status
"""

from typing import TypedDict, Annotated, Optional, Literal
import operator


class PlannerState(TypedDict):
    """
    State for autonomous planner agent.
    
    The planner iterates through think-act-observe until goal is satisfied.
    """
    
    # === Input ===
    goal: str  # User's natural language goal
    repo_id: str  # Repository to work with
    
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
