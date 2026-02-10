"""
Autonomous Agent API endpoints.

Provides REST API for playbook-based autonomous agent execution.

Endpoints:
- POST /api/v1/agents/autonomous - Execute autonomous agent with goal
- GET /api/v1/agents/autonomous/{job_id}/status - Get job status
- GET /api/v1/agents/autonomous/{job_id}/result - Get job result
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import uuid
import asyncio
from datetime import datetime

router = APIRouter(prefix="/api/v1/agents", tags=["autonomous-agents"])

# Global state
# Global state
planner_agent = None
playbook_executor = None
playbook_selector = None
autonomous_jobs = {}


class AutonomousRequest(BaseModel):
    """Request to execute autonomous agent."""
    goal: str = Field(..., description="Natural language goal", min_length=5)
    repo_id: str = Field(..., description="Repository identifier")
    max_iterations: int = Field(10, description="Maximum iterations", ge=1, le=50)


class AutonomousJobResponse(BaseModel):
    """Response for autonomous job creation."""
    job_id: str
    status: str
    created_at: str
    goal: str


class AutonomousJobStatus(BaseModel):
    """Status of autonomous job."""
    job_id: str
    status: str  # pending, running, completed, failed
    created_at: str
    goal: str
    iterations: Optional[int] = None
    steps_taken: Optional[int] = None


class AutonomousJobResult(BaseModel):
    """Result of autonomous job."""
    job_id: str
    status: str
    goal: str
    answer: Optional[str] = None
    steps_taken: Optional[int] = None
    iterations: Optional[int] = None
    playbooks_used: Optional[list[str]] = None
    error: Optional[str] = None


class PlaybookRequest(BaseModel):
    """Request to execute a specific playbook."""
    playbook_name: str = Field("auto", description="Name of the playbook to execute (or 'auto')")
    prompt: str = Field(..., description="Input prompt for the playbook")
    repo_id: Optional[str] = Field(None, description="Repository identifier (if needed)")


class PlaybookResponse(BaseModel):
    """Response from playbook execution."""
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
    logs: list[str] = []


def init_autonomous_agents(lance_storage, graph_service, llm_client, embedder):
    """
    Initialize autonomous agent system.
    
    Args:
        lance_storage: LanceDB storage instance
        graph_service: GraphQueryService instance
        llm_client: LLM client for generation
        embedder: Embedder for query encoding
    """
    global planner_agent, playbook_executor, playbook_selector
    
    from ..playbooks import PlaybookRegistry, PlaybookExecutor, PlaybookTools
    from ..agents import PlannerAgent, PlaybookSelector
    
    print("[AUTONOMOUS] Initializing autonomous agent system...")
    
    # Initialize playbook system
    registry = PlaybookRegistry()
    print(f"[AUTONOMOUS] ✓ Loaded {len(registry)} playbooks")
    
    # Initialize tools (only search_codebase now)
    tools = PlaybookTools(lance_storage, graph_service, embedder)
    
    # Initialize executor (now needs LLM for prompt-based execution)
    executor = PlaybookExecutor(registry, tools, llm_client)
    playbook_executor = executor
    
    # Initialize planner
    planner_agent = PlannerAgent(registry, executor, llm_client)
    
    # Initialize selector
    playbook_selector = PlaybookSelector(registry, llm_client)
    
    print(f"[AUTONOMOUS] ✓ Autonomous agent system ready")
    print(f"[AUTONOMOUS] ✓ Available playbooks: {', '.join(registry.list_playbooks())}")


async def run_autonomous_task(job_id: str, goal: str, repo_id: str, max_iterations: int):
    """
    Background task for autonomous execution.
    Runs as a concurrent coroutine via asyncio.create_task().
    """
    try:
        print(f"[AUTONOMOUS] Starting job {job_id}")
        autonomous_jobs[job_id]["status"] = "running"
        
        # Execute planner directly as async coroutine
        result = await planner_agent.execute(goal, repo_id, max_iterations)
        
        # Update job with result
        autonomous_jobs[job_id]["status"] = "completed"
        autonomous_jobs[job_id]["result"] = result
        autonomous_jobs[job_id]["iterations"] = result.get("iterations", 0)
        autonomous_jobs[job_id]["steps_taken"] = result.get("steps_taken", 0)
        
        print(f"[AUTONOMOUS] ✓ Job {job_id} completed successfully")
        
    except Exception as e:
        autonomous_jobs[job_id]["status"] = "failed"
        autonomous_jobs[job_id]["error"] = str(e)
        print(f"[AUTONOMOUS] ✗ Job {job_id} failed: {e}")
        import traceback
        traceback.print_exc()


@router.post("/autonomous", response_model=AutonomousJobResponse)
async def execute_autonomous(request: AutonomousRequest):
    """
    Execute an autonomous agent with a natural language goal.
    
    Uses asyncio.create_task() for true non-blocking execution.
    Returns a job ID for tracking execution.
    """
    if not planner_agent:
        raise HTTPException(
            status_code=503,
            detail="Autonomous agent system not initialized"
        )
    
    job_id = str(uuid.uuid4())
    
    autonomous_jobs[job_id] = {
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "goal": request.goal,
        "repo_id": request.repo_id,
        "max_iterations": request.max_iterations
    }
    
    # Use asyncio.create_task for true concurrent execution
    # (BackgroundTasks blocks the event loop for async functions)
    asyncio.create_task(
        run_autonomous_task(
            job_id=job_id,
            goal=request.goal,
            repo_id=request.repo_id,
            max_iterations=request.max_iterations
        )
    )
    
    return AutonomousJobResponse(
        job_id=job_id,
        status="pending",
        created_at=autonomous_jobs[job_id]["created_at"],
        goal=request.goal
    )


@router.get("/autonomous/{job_id}/status", response_model=AutonomousJobStatus)
async def get_autonomous_status(job_id: str):
    """Get status of autonomous job."""
    if job_id not in autonomous_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = autonomous_jobs[job_id]
    
    return AutonomousJobStatus(
        job_id=job_id,
        status=job["status"],
        created_at=job["created_at"],
        goal=job["goal"],
        iterations=job.get("iterations"),
        steps_taken=job.get("steps_taken")
    )


@router.get("/autonomous/{job_id}/result", response_model=AutonomousJobResult)
async def get_autonomous_result(job_id: str):
    """
    Get result of autonomous job.
    
    Only returns result if job is completed or failed.
    """
    if job_id not in autonomous_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = autonomous_jobs[job_id]
    
    if job["status"] == "pending" or job["status"] == "running":
        raise HTTPException(
            status_code=425,
            detail=f"Job is {job['status']}, not yet complete"
        )
    
    result = job.get("result", {})
    
    return AutonomousJobResult(
        job_id=job_id,
        status=job["status"],
        goal=job["goal"],
        answer=result.get("answer"),
        steps_taken=result.get("steps_taken"),
        iterations=result.get("iterations"),
        playbooks_used=result.get("playbooks_used"),
        error=job.get("error")
    )


@router.post("/playbook", response_model=PlaybookResponse)
async def execute_playbook(request: PlaybookRequest):
    """
    Execute a single playbook directly.
    
    This matches the user's request for "a simple endpoint... Playbook can be used as the system prompt".
    """
    if not playbook_executor:
        raise HTTPException(
            status_code=503,
            detail="Playbook system not initialized"
        )
    
    # Determine playbook to use
    final_playbook_name = request.playbook_name
    
    if final_playbook_name == "auto" or not final_playbook_name:
        if not playbook_selector:
            # Fallback if selector not init (shouldn't happen)
            final_playbook_name = "code_analyzer"
        else:
            final_playbook_name = await playbook_selector.select_playbook(request.prompt)
            
    # Construct input for the playbook
    # We map 'prompt' to both 'query' and 'goal' to cover different playbook expectations
    user_input = {
        "query": request.prompt,
        "goal": request.prompt,
        "repo_id": request.repo_id
    }
    
    # Execute
    result = await playbook_executor.execute(final_playbook_name, user_input)
    
    # Extract result string from outputs
    output_text = result.get("outputs", {}).get("result")
    
    return PlaybookResponse(
        success=result["success"],
        result=output_text,
        error=result.get("error"),
        logs=result.get("logs", [])
    )
