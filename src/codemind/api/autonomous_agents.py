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
from typing import Optional, Any
import uuid
import asyncio
from datetime import datetime

from ..storage.db_factory import get_database

router = APIRouter(prefix="/api/v1/agents", tags=["autonomous-agents"])

# Global state
planner_agent = None
playbook_executor = None
playbook_selector = None
manifest_manager = None
autonomous_jobs = {}


class AutonomousRequest(BaseModel):
    """Request to execute autonomous agent."""
    goal: str = Field(..., description="Natural language goal", min_length=5)
    repo_id: Optional[str | list[str]] = Field(None, description="Repository identifier or list of IDs (optional for global search)")
    max_iterations: int = Field(10, description="Maximum iterations", ge=1, le=50)
    allowed_playbooks: Optional[list[str]] = Field(None, description="Restrict agent to specific playbooks")


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
    logs: list[str] = []


class AutonomousJobResult(BaseModel):
    """Result of autonomous job."""
    job_id: str
    status: str
    goal: str
    answer: Optional[Any] = None
    steps_taken: Optional[int] = None
    iterations: Optional[int] = None
    playbooks_used: Optional[list[str]] = None
    error: Optional[str] = None


class PlaybookRequest(BaseModel):
    """Request to execute a specific playbook."""
    playbook_name: str = Field("auto", description="Name of the playbook to execute (or 'auto')")
    prompt: Optional[str] = Field(None, description="Input prompt for the playbook")
    repo_id: Optional[str | list[str]] = Field(None, description="Repository identifier or list of IDs (if needed)")


class PlaybookResponse(BaseModel):
    """Response from playbook execution."""
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
    logs: list[str] = []


def init_autonomous_agents(lance_storage, graph_service, chat_model, embedder, manifest_mgr=None, db=None):
    """
    Initialize autonomous agent system.
    
    Args:
        lance_storage: LanceDB storage instance
        graph_service: GraphQueryService instance
        chat_model: CmindChatModel instance (LangChain-compatible)
        embedder: Embedder for query encoding
        manifest_mgr: ManifestManager instance (optional)
        db: Database instance (optional)
    """
    global planner_agent, playbook_executor, playbook_selector, manifest_manager, _db
    
    from ..playbooks import PlaybookRegistry, PlaybookExecutor, PlaybookTools
    from ..agents import PlannerAgent, PlaybookSelector
    
    print("[AUTONOMOUS] Initializing autonomous agent system...")
    
    manifest_manager = manifest_mgr
    _db = db if db else get_database()

    # Initialize playbook system
    registry = PlaybookRegistry()
    print(f"[AUTONOMOUS] ✓ Loaded {len(registry)} playbooks")
    
    # Initialize tools (only search_codebase now)
    tools = PlaybookTools(lance_storage, graph_service, embedder, db)
    
    # Extract raw LLMDriver for executor (uses .generate() directly)
    llm_driver = chat_model.driver
    
    # Initialize executor with raw driver
    executor = PlaybookExecutor(registry, tools, llm_driver)
    playbook_executor = executor
    
    # Initialize planner with chat model (uses bind_tools/ToolNode)
    planner_agent = PlannerAgent(registry, executor, llm_driver)
    
    # Initialize selector with raw driver
    playbook_selector = PlaybookSelector(registry, llm_driver)
    
    print(f"[AUTONOMOUS] ✓ Autonomous agent system ready (LangChain chat model)")
    print(f"[AUTONOMOUS] ✓ Available playbooks: {', '.join(registry.list_playbooks())}")


async def run_autonomous_task(
    job_id: str, 
    goal: str, 
    repo_id: Optional[str | list[str]], 
    max_iterations: int,
    allowed_playbooks: Optional[list[str]] = None
):
    """
    Background task for autonomous execution.
    Runs as a concurrent coroutine via asyncio.create_task().
    """
    try:
        print(f"[AUTONOMOUS] Starting job {job_id}")
        autonomous_jobs[job_id]["status"] = "running"
        autonomous_jobs[job_id]["logs"] = ["Job started..."]
        
        async def update_job_state(state: dict):
            """Callback to update job state from planner."""
            # Extract logs/thoughts
            thoughts = state.get("thoughts", [])
            actions = state.get("actions", [])
            iteration = state.get("iteration", 0)
            
            # Construct a simple log for now (can be richer later)
            logs = []
            for t in thoughts:
                logs.append(f"Thinking: {t[:500]}...")
            
            for i, action in enumerate(actions):
                name = action.get("playbook") or action.get("tool")
                logs.append(f"Action {i+1}: Executing {name}")
                
            autonomous_jobs[job_id]["iterations"] = iteration
            autonomous_jobs[job_id]["steps_taken"] = len(actions)
            autonomous_jobs[job_id]["logs"] = logs
        
        # Execute planner directly as async coroutine
        result = await planner_agent.execute(
            goal, 
            repo_id, 
            max_iterations, 
            on_update=update_job_state,
            allowed_playbooks=allowed_playbooks,
            thread_id=job_id
        )
        
        # Update job with result
        autonomous_jobs[job_id]["status"] = "completed"
        autonomous_jobs[job_id]["result"] = result
        autonomous_jobs[job_id]["iterations"] = result.get("iterations", 0)
        autonomous_jobs[job_id]["steps_taken"] = result.get("steps_taken", 0)
        
        # Debug dump
        import json as _json
        try:
            with open("/tmp/autonomous_result_debug.json", "w") as f:
                _json.dump(result, f, indent=2, default=str)
            print(f"[AUTONOMOUS] Debug result dumped to /tmp/autonomous_result_debug.json")
            answer = result.get("answer")
            print(f"[AUTONOMOUS] Answer type: {type(answer).__name__}, keys: {list(answer.keys()) if isinstance(answer, dict) else 'N/A'}")
        except Exception as de:
            print(f"[AUTONOMOUS] Debug dump failed: {de}")
        
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
            max_iterations=request.max_iterations,
            allowed_playbooks=request.allowed_playbooks
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
        steps_taken=job.get("steps_taken"),
        logs=job.get("logs", [])
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
        if not request.prompt:
             raise HTTPException(status_code=400, detail="Prompt is required for auto-selection")

        if not playbook_selector:
            # Fallback if selector not init (shouldn't happen)
            final_playbook_name = "code_analyzer"
        else:
            final_playbook_name = await playbook_selector.select_playbook(request.prompt)
            
    # Resolve prompt if missing
    prompt = request.prompt
    if not prompt:
        registry = playbook_executor.registry
        playbook_def = registry.get_playbook(final_playbook_name)
        if playbook_def and playbook_def.default_prompt:
             prompt = playbook_def.default_prompt
        else:
             # If no prompt and no default, we proceed with empty prompt as requested
             prompt = ""

    # Fetch Repository Metadata if available
    repo_metadata = {}
    if request.repo_id and manifest_manager:
        repo = manifest_manager.get_repository_by_id(request.repo_id)
        if repo:
            repo_metadata = {
                "repo_id": repo.repo_id,
                "name": repo.repo_path.split("/")[-2] if len(repo.repo_path.split("/")) > 2 else "unknown",
                "path": repo.repo_path,
                "org": getattr(repo, 'org', None) or "",
                "first_author": repo.first_author,
                "total_commits": repo.total_commits,
                "last_pr_title": repo.last_pr_title,
                "last_pr_user": repo.last_pr_user,
                "last_pr_merged_at": repo.last_pr_merged_at,
                "last_indexed": repo.last_indexed_at.isoformat(),
                "repo_url": repo.repo_url,
                "branch": repo.branch
            }

    # Construct input for the playbook
    # We map 'prompt' to both 'query' and 'goal' to cover different playbook expectations
    # And inject context for tools to pick up
    user_input = {
        "query": prompt,
        "goal": prompt,
        "repo_id": request.repo_id,
        "context": repo_metadata
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
