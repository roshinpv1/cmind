"""
Autonomous Agent API endpoints.

Provides REST API for skill-based autonomous agent execution.

Endpoints:
- POST /api/v1/agents/autonomous - Execute autonomous agent with goal
- GET /api/v1/agents/autonomous/{job_id}/status - Get job status
- GET /api/v1/agents/autonomous/{job_id}/result - Get job result
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/v1/agents", tags=["autonomous-agents"])

# Global state
planner_agent = None
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


def init_autonomous_agents(lance_storage, graph_service, llm_client, embedder):
    """
    Initialize autonomous agent system.
    
    Args:
        lance_storage: LanceDB storage instance
        graph_service: GraphQueryService instance
        llm_client: LLM client for generation
        embedder: Embedder for query encoding
    """
    global planner_agent
    
    from ..skills import SkillRegistry, SkillExecutor, SkillTools
    from ..agents import PlannerAgent
    
    print("[AUTONOMOUS] Initializing autonomous agent system...")
    
    # Initialize skill system
    registry = SkillRegistry()
    print(f"[AUTONOMOUS] ✓ Loaded {len(registry)} skills")
    
    # Initialize tools (only search_codebase now)
    tools = SkillTools(lance_storage, graph_service, embedder)
    
    # Initialize executor (now needs LLM for prompt-based execution)
    executor = SkillExecutor(registry, tools, llm_client)
    
    # Initialize planner
    planner_agent = PlannerAgent(registry, executor, llm_client)
    
    print(f"[AUTONOMOUS] ✓ Autonomous agent system ready")
    print(f"[AUTONOMOUS] ✓ Available skills: {', '.join(registry.list_skills())}")


async def run_autonomous_task(job_id: str, goal: str, repo_id: str, max_iterations: int):
    """
    Background task for autonomous execution.
    
    Args:
        job_id: Unique job identifier
        goal: User's goal
        repo_id: Repository to work with
        max_iterations: Maximum iterations
    """
    try:
        print(f"[AUTONOMOUS] Starting job {job_id}")
        autonomous_jobs[job_id]["status"] = "running"
        
        # Execute planner
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
async def execute_autonomous(request: AutonomousRequest, background_tasks: BackgroundTasks):
    """
    Execute an autonomous agent with a natural language goal.
    
    The agent will:
    1. Interpret the goal
    2. Select appropriate skills iteratively
    3. Execute skills via the skill executor
    4. Synthesize a final answer
    
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
    
    # Start background task
    background_tasks.add_task(
        run_autonomous_task,
        job_id=job_id,
        goal=request.goal,
        repo_id=request.repo_id,
        max_iterations=request.max_iterations
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
