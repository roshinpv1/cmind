"""
Autonomous Agent API endpoints.

Provides REST API for skill-based autonomous agent execution.

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
skill_executor = None
skill_selector = None
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
    skills_used: Optional[list[str]] = None
    error: Optional[str] = None


class SkillRequest(BaseModel):
    """Request to execute a specific skill."""
    skill_name: str = Field("auto", description="Name of the skill to execute (or 'auto')")
    prompt: str = Field(..., description="Input prompt for the skill")
    repo_id: Optional[str] = Field(None, description="Repository identifier (if needed)")


class SkillResponse(BaseModel):
    """Response from skill execution."""
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
    global planner_agent, skill_executor, skill_selector
    
    from ..skills import SkillRegistry, SkillExecutor, SkillTools
    from ..agents import PlannerAgent, SkillSelector
    
    print("[AUTONOMOUS] Initializing autonomous agent system...")
    
    # Initialize skill system
    registry = SkillRegistry()
    print(f"[AUTONOMOUS] ✓ Loaded {len(registry)} skills")
    
    # Initialize tools (only search_codebase now)
    tools = SkillTools(lance_storage, graph_service, embedder)
    
    # Initialize executor (now needs LLM for prompt-based execution)
    executor = SkillExecutor(registry, tools, llm_client)
    skill_executor = executor
    
    # Initialize planner
    planner_agent = PlannerAgent(registry, executor, llm_client)
    
    # Initialize selector
    skill_selector = SkillSelector(registry, llm_client)
    
    print(f"[AUTONOMOUS] ✓ Autonomous agent system ready")
    print(f"[AUTONOMOUS] ✓ Available skills: {', '.join(registry.list_skills())}")


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
        skills_used=result.get("skills_used"),
        error=job.get("error")
    )


@router.post("/skill", response_model=SkillResponse)
async def execute_skill(request: SkillRequest):
    """
    Execute a single skill directly.
    
    This matches the user's request for "a simple endpoint... Skill can be used as the system prompt".
    """
    if not skill_executor:
        raise HTTPException(
            status_code=503,
            detail="Skill system not initialized"
        )
    
    # Determine skill to use
    final_skill_name = request.skill_name
    
    if final_skill_name == "auto" or not final_skill_name:
        if not skill_selector:
            # Fallback if selector not init (shouldn't happen)
            final_skill_name = "code_analyzer"
        else:
            final_skill_name = await skill_selector.select_skill(request.prompt)
            
    # Construct input for the skill
    # We map 'prompt' to both 'query' and 'goal' to cover different skill expectations
    user_input = {
        "query": request.prompt,
        "goal": request.prompt,
        "repo_id": request.repo_id
    }
    
    # Execute
    result = await skill_executor.execute(final_skill_name, user_input)
    
    # Extract result string from outputs
    output_text = result.get("outputs", {}).get("result")
    
    return SkillResponse(
        success=result["success"],
        result=output_text,
        error=result.get("error"),
        logs=result.get("logs", [])
    )
