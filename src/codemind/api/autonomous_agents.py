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
import os
import uuid
import asyncio
from datetime import datetime

from ..storage.db_factory import get_database
from ..agents.run_orchestrator import RunOrchestrator

router = APIRouter(prefix="/api/v1/agents", tags=["autonomous-agents"])

# Global state
planner_agent = None
playbook_executor = None
playbook_selector = None
manifest_manager = None
autonomous_jobs = {}
run_orchestrator: RunOrchestrator | None = None


def _retention_days() -> int:
    """Retention window for persisted autonomous run context."""
    raw = os.getenv("CODEMIND_AGENT_CONTEXT_RETENTION_DAYS", "14")
    try:
        return max(1, int(raw))
    except Exception:
        return 14


def _cleanup_expired_context() -> None:
    """Best-effort cleanup of stale persisted run context."""
    if not run_orchestrator:
        return
    try:
        run_orchestrator.cleanup_expired_runs(retention_days=_retention_days())
    except Exception as exc:
        print(f"[AUTONOMOUS] retention cleanup skipped: {exc}")


def _requires_codebase_context(text: str | None) -> bool:
    """Heuristic: detect if prompt is asking about a specific codebase/repository."""
    t = (text or "").lower()
    if not t:
        return False
    markers = (
        "codebase",
        "repository",
        "repo ",
        "repo:",
        "in this code",
        "in this project",
        "in the project",
        "source code",
        "existing code",
    )
    return any(m in t for m in markers)


class AutonomousRequest(BaseModel):
    """Request to execute autonomous agent."""
    goal: str = Field(..., description="Natural language goal", min_length=5)
    job_id: Optional[str] = Field(None, description="Optional existing job_id to continue")
    repo_id: Optional[str | list[str]] = Field(None, description="Repository identifier or list of IDs (optional for global search)")
    max_iterations: int = Field(10, description="Maximum iterations", ge=1, le=50)
    allowed_playbooks: Optional[list[str]] = Field(None, description="Restrict agent to specific playbooks")
    mirror_mode: bool = Field(True, description="Write generated files to mirror workspace only")


class AutonomousJobResponse(BaseModel):
    """Response for autonomous job creation."""
    job_id: str
    status: str
    created_at: str
    goal: str


class AutonomousRunListItem(BaseModel):
    """Recent autonomous run summary for continuation."""

    run_id: str
    goal: str
    repo_id: Optional[str] = None
    status: str
    created_at: str
    updated_at: str
    iterations: int = 0
    steps_taken: int = 0
    mirror_root: Optional[str] = None
    generated_files_count: int = 0


class AutonomousJobStatus(BaseModel):
    """Status of autonomous job."""
    job_id: str
    status: str  # pending, running, completed, failed
    created_at: str
    goal: str
    iterations: Optional[int] = None
    steps_taken: Optional[int] = None
    logs: list[str] = []
    mirror_root: Optional[str] = None
    generated_files: list[str] = []


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
    mirror_root: Optional[str] = None
    generated_files: list[str] = []


class AutonomousRerunRequest(BaseModel):
    """Rerun an autonomous goal from a previous run/checkpoint."""

    checkpoint_key: Optional[str] = Field(
        default=None,
        description="Optional checkpoint key (e.g. iter-3) to annotate rerun context",
    )
    max_iterations: int = Field(10, ge=1, le=50)
    allowed_playbooks: Optional[list[str]] = None
    mirror_mode: bool = Field(True, description="Write generated files to mirror workspace only")


class PlaybookRequest(BaseModel):
    """Request to execute a specific playbook."""
    playbook_name: str = Field("auto", description="Name of the playbook to execute (or 'auto')")
    prompt: Optional[str] = Field(None, description="Input prompt for the playbook")
    repo_id: Optional[str | list[str]] = Field(None, description="Repository identifier or list of IDs (if needed)")
    mirror_mode: bool = Field(True, description="Write generated files to mirror workspace only")


class PlaybookResponse(BaseModel):
    """Response from playbook execution."""
    success: bool
    result: Optional[str] = None
    data: Optional[Any] = None
    error: Optional[str] = None
    logs: list[str] = []
    mirror_root: Optional[str] = None


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
    global planner_agent, playbook_executor, playbook_selector, manifest_manager, _db, run_orchestrator
    
    from ..playbooks import PlaybookRegistry, PlaybookExecutor, PlaybookTools
    from ..agents import PlannerAgent, PlaybookSelector
    
    print("[AUTONOMOUS] Initializing autonomous agent system...")
    
    manifest_manager = manifest_mgr
    _db = db if db else get_database()
    run_orchestrator = RunOrchestrator(_db)

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
    allowed_playbooks: Optional[list[str]] = None,
    mirror_root: Optional[str] = None,
    rerun_context: Optional[dict] = None,
):
    """
    Background task for autonomous execution.
    Runs as a concurrent coroutine via asyncio.create_task().
    """
    try:
        print(f"[AUTONOMOUS] Starting job {job_id}")
        autonomous_jobs[job_id]["status"] = "running"
        autonomous_jobs[job_id]["logs"] = ["Job started..."]
        if run_orchestrator:
            run_orchestrator.mark_running(job_id)

        effective_goal = goal
        if rerun_context:
            ckpt = rerun_context.get("checkpoint_key")
            if ckpt:
                effective_goal = (
                    f"{goal}\n\n[RERUN CONTEXT]\n"
                    f"- Prior checkpoint: {ckpt}\n"
                    "- Re-run with same intent. Re-evaluate from this point forward.\n"
                )
            prior_status = rerun_context.get("prior_status")
            prior_iterations = rerun_context.get("prior_iterations")
            prior_generated = rerun_context.get("generated_files") or []
            if prior_status or prior_iterations or prior_generated:
                effective_goal += (
                    "\n- Previous run state:\n"
                    f"  - status: {prior_status}\n"
                    f"  - iterations: {prior_iterations}\n"
                    f"  - generated_files: {len(prior_generated)}\n"
                )
        
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
            generated_files: list[str] = []
            for m in state.get("messages", []):
                try:
                    if getattr(m, "name", "") != "playbook_generic_code_generator":
                        continue
                    import json as _json
                    parsed = _json.loads(str(getattr(m, "content", "") or "{}"))
                    outputs = parsed.get("outputs") if isinstance(parsed, dict) else None
                    if isinstance(outputs, dict):
                        g = outputs.get("generated_files")
                        if isinstance(g, list):
                            generated_files.extend(str(p) for p in g if p)
                except Exception:
                    continue
            if generated_files:
                autonomous_jobs[job_id]["generated_files"] = list(dict.fromkeys(generated_files))
            if run_orchestrator:
                step_payload = {
                    "iteration": iteration,
                    "thoughts": thoughts[-3:],
                    "actions": actions[-3:],
                    "generated_files": autonomous_jobs[job_id].get("generated_files", []),
                }
                run_orchestrator.upsert_step(
                    run_id=job_id,
                    step_index=iteration,
                    status="running",
                    payload=step_payload,
                )
                checkpoint_payload = {
                    "iteration": iteration,
                    "thought_count": len(thoughts),
                    "action_count": len(actions),
                    "finished": bool(state.get("finished")),
                    "final_result_preview": str(state.get("final_result", ""))[:500],
                    "generated_files": autonomous_jobs[job_id].get("generated_files", []),
                }
                run_orchestrator.save_checkpoint(
                    run_id=job_id,
                    checkpoint_key=f"iter-{iteration}",
                    step_index=iteration,
                    state_json=checkpoint_payload,
                )
                run_orchestrator.update_runtime_state(
                    run_id=job_id,
                    iterations=iteration,
                    steps_taken=len(actions),
                    generated_files=autonomous_jobs[job_id].get("generated_files", []),
                )
        
        # Execute planner directly as async coroutine
        result = await planner_agent.execute(
            effective_goal,
            repo_id, 
            max_iterations, 
            on_update=update_job_state,
            allowed_playbooks=allowed_playbooks,
            thread_id=job_id,
            execution_context={
                "run_id": job_id,
                "mirror_root": mirror_root,
                "prefer_mirror_reads": bool(mirror_root),
            },
        )
        
        # Update job with result
        autonomous_jobs[job_id]["status"] = "completed"
        autonomous_jobs[job_id]["result"] = result
        autonomous_jobs[job_id]["iterations"] = result.get("iterations", 0)
        autonomous_jobs[job_id]["steps_taken"] = result.get("steps_taken", 0)
        if "generated_files" not in autonomous_jobs[job_id]:
            autonomous_jobs[job_id]["generated_files"] = []
        if run_orchestrator:
            run_orchestrator.upsert_step(
                run_id=job_id,
                step_index=int(result.get("iterations", 0) or 0),
                status="completed",
                payload={
                    "steps_taken": result.get("steps_taken", 0),
                    "playbooks_used": result.get("playbooks_used", []),
                    "generated_files": autonomous_jobs[job_id].get("generated_files", []),
                },
            )
            persisted_result = dict(result or {})
            persisted_result["generated_files"] = autonomous_jobs[job_id].get("generated_files", [])
            run_orchestrator.mark_completed(
                run_id=job_id,
                result=persisted_result,
                iterations=result.get("iterations", 0),
                steps_taken=result.get("steps_taken", 0),
            )
        
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
        if run_orchestrator:
            run_orchestrator.upsert_step(
                run_id=job_id,
                step_index=int(autonomous_jobs[job_id].get("iterations", 0) or 0),
                status="failed",
                error=str(e),
            )
            run_orchestrator.mark_failed(job_id, str(e))
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
    _cleanup_expired_context()
        
    if request.repo_id and manifest_manager:
        repo_ids = request.repo_id if isinstance(request.repo_id, list) else [request.repo_id]
        for r_id in repo_ids:
            if not manifest_manager.get_repository_by_id(r_id):
                raise HTTPException(status_code=404, detail=f"Repository not found or not indexed: {r_id}")

    if not request.repo_id and _requires_codebase_context(request.goal):
        message = (
            "No codebase selected. Your goal appears codebase-specific. "
            "Select a repository to continue with playbook execution."
        )
        blocked_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        autonomous_jobs[blocked_id] = {
            "status": "failed",
            "created_at": now,
            "goal": request.goal,
            "repo_id": None,
            "max_iterations": request.max_iterations,
            "logs": [message],
            "error": message,
        }
        if run_orchestrator:
            run_row = run_orchestrator.create_run(goal=request.goal, repo_id=None)
            run_orchestrator.mark_failed(run_row["run_id"], message)
            blocked_id = run_row["run_id"]
            autonomous_jobs[blocked_id] = {
                "status": "failed",
                "created_at": run_row["created_at"],
                "goal": request.goal,
                "repo_id": None,
                "max_iterations": request.max_iterations,
                "mirror_root": run_row["mirror_root"],
                "logs": [message],
                "error": message,
            }
        return AutonomousJobResponse(
            job_id=blocked_id,
            status="failed",
            created_at=autonomous_jobs[blocked_id]["created_at"],
            goal=request.goal,
        )
    
    continuation_context: dict | None = None
    if request.job_id:
        if not run_orchestrator:
            raise HTTPException(status_code=503, detail="Run orchestration not initialized")
        prior = run_orchestrator.get_run(request.job_id)
        if not prior:
            raise HTTPException(status_code=404, detail=f"Job not found: {request.job_id}")
        if prior.get("status") == "running":
            return AutonomousJobResponse(
                job_id=request.job_id,
                status="running",
                created_at=prior["created_at"],
                goal=prior["goal"],
            )

        job_id = request.job_id
        primary_repo = (
            request.repo_id[0] if isinstance(request.repo_id, list) and request.repo_id
            else (request.repo_id or prior.get("repo_id"))
        )
        latest_ckpt = run_orchestrator.get_latest_checkpoint(job_id)
        continuation_context = {
            "checkpoint_key": latest_ckpt.get("checkpoint_key") if latest_ckpt else None,
            "prior_status": prior.get("status"),
            "prior_iterations": prior.get("iterations"),
            "generated_files": (prior.get("result") or {}).get("generated_files", []),
        }
        autonomous_jobs[job_id] = {
            "status": "pending",
            "created_at": prior["created_at"],
            "goal": request.goal or prior["goal"],
            "repo_id": primary_repo,
            "max_iterations": request.max_iterations,
            "mirror_root": prior.get("mirror_root"),
            "generated_files": (prior.get("result") or {}).get("generated_files", []),
            "logs": [f"Continuing job {job_id} from DB state"],
        }
    else:
        primary_repo = request.repo_id[0] if isinstance(request.repo_id, list) and request.repo_id else request.repo_id
        run_row = None
        if run_orchestrator:
            run_row = run_orchestrator.create_run(
                goal=request.goal,
                repo_id=primary_repo,
            )
            job_id = run_row["run_id"]
        else:
            job_id = str(uuid.uuid4())

        autonomous_jobs[job_id] = {
            "status": "pending",
            "created_at": run_row["created_at"] if run_row else datetime.now().isoformat(),
            "goal": request.goal,
            "repo_id": request.repo_id,
            "max_iterations": request.max_iterations,
            "mirror_root": run_row["mirror_root"] if run_row else None,
            "generated_files": [],
        }
    
    # Use asyncio.create_task for true concurrent execution
    # (BackgroundTasks blocks the event loop for async functions)
    asyncio.create_task(
        run_autonomous_task(
            job_id=job_id,
            goal=request.goal,
            repo_id=(request.repo_id if request.repo_id is not None else autonomous_jobs[job_id].get("repo_id")),
            max_iterations=request.max_iterations,
            allowed_playbooks=request.allowed_playbooks,
            mirror_root=autonomous_jobs[job_id].get("mirror_root") if request.mirror_mode else None,
            rerun_context=continuation_context,
        )
    )
    
    return AutonomousJobResponse(
        job_id=job_id,
        status="pending",
        created_at=autonomous_jobs[job_id]["created_at"],
        goal=request.goal
    )


@router.get("/autonomous/runs", response_model=list[AutonomousRunListItem])
async def list_autonomous_runs(limit: int = 20):
    """List recent persisted autonomous runs for UI continuation picker."""
    if not run_orchestrator:
        raise HTTPException(status_code=503, detail="Run orchestration not initialized")
    _cleanup_expired_context()
    return run_orchestrator.list_runs(limit=limit)


@router.get("/autonomous/{job_id}/status", response_model=AutonomousJobStatus)
async def get_autonomous_status(job_id: str):
    """Get status of autonomous job."""
    if job_id not in autonomous_jobs:
        if not run_orchestrator or not run_orchestrator.get_run(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    
    if run_orchestrator:
        persisted = run_orchestrator.get_run(job_id)
        if persisted:
            persisted_generated = []
            if isinstance(persisted.get("result"), dict):
                persisted_generated = persisted["result"].get("generated_files", []) or []
            job = {
                "status": persisted["status"],
                "created_at": persisted["created_at"],
                "goal": persisted["goal"],
                "iterations": persisted.get("iterations"),
                "steps_taken": persisted.get("steps_taken"),
                "logs": autonomous_jobs.get(job_id, {}).get("logs", []),
                "mirror_root": persisted.get("mirror_root"),
                "generated_files": persisted_generated or autonomous_jobs.get(job_id, {}).get("generated_files", []),
            }
        else:
            job = autonomous_jobs[job_id]
    else:
        job = autonomous_jobs[job_id]
    
    return AutonomousJobStatus(
        job_id=job_id,
        status=job["status"],
        created_at=job["created_at"],
        goal=job["goal"],
        iterations=job.get("iterations"),
        steps_taken=job.get("steps_taken"),
        logs=job.get("logs", []),
        mirror_root=job.get("mirror_root"),
        generated_files=job.get("generated_files", []),
    )


@router.get("/autonomous/{job_id}/result", response_model=AutonomousJobResult)
async def get_autonomous_result(job_id: str):
    """
    Get result of autonomous job.
    
    Only returns result if job is completed or failed.
    """
    persisted = run_orchestrator.get_run(job_id) if run_orchestrator else None
    if job_id not in autonomous_jobs and not persisted:
        raise HTTPException(status_code=404, detail="Job not found")

    job = autonomous_jobs.get(job_id, {})
    effective_status = persisted["status"] if persisted else job.get("status")
    if effective_status in {"pending", "running"}:
        raise HTTPException(
            status_code=425,
            detail=f"Job is {effective_status}, not yet complete"
        )

    result = job.get("result", {})
    mirror_root = job.get("mirror_root")
    generated_files = job.get("generated_files", [])
    if persisted:
        result = persisted.get("result") or result
        mirror_root = persisted.get("mirror_root") or mirror_root
        if isinstance(persisted.get("result"), dict):
            generated_files = persisted["result"].get("generated_files", generated_files)
        if persisted.get("error"):
            job["error"] = persisted["error"]
    
    return AutonomousJobResult(
        job_id=job_id,
        status=effective_status or "unknown",
        goal=(persisted["goal"] if persisted else job.get("goal", "")),
        answer=result.get("answer"),
        steps_taken=result.get("steps_taken"),
        iterations=result.get("iterations"),
        playbooks_used=result.get("playbooks_used"),
        error=job.get("error"),
        mirror_root=mirror_root,
        generated_files=generated_files or [],
    )


@router.get("/autonomous/{job_id}/checkpoints")
async def list_autonomous_checkpoints(job_id: str):
    """List point-in-time checkpoints for a run."""
    if not run_orchestrator:
        raise HTTPException(status_code=503, detail="Run orchestration not initialized")
    run = run_orchestrator.get_run(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "checkpoints": run_orchestrator.list_checkpoints(job_id)}


@router.post("/autonomous/{job_id}/rerun", response_model=AutonomousJobResponse)
async def rerun_autonomous(job_id: str, request: AutonomousRerunRequest):
    """Create a new run that replays goal from a prior run/checkpoint context."""
    if not planner_agent:
        raise HTTPException(status_code=503, detail="Autonomous agent system not initialized")
    if not run_orchestrator:
        raise HTTPException(status_code=503, detail="Run orchestration not initialized")

    prior = run_orchestrator.get_run(job_id)
    if not prior:
        raise HTTPException(status_code=404, detail="Original run not found")

    checkpoint_key = request.checkpoint_key
    if checkpoint_key:
        ckpt = run_orchestrator.get_checkpoint(job_id, checkpoint_key)
        if not ckpt:
            raise HTTPException(status_code=404, detail=f"Checkpoint not found: {checkpoint_key}")

    new_run = run_orchestrator.create_run(
        goal=prior["goal"],
        repo_id=prior.get("repo_id"),
        parent_run_id=job_id,
        rerun_from_checkpoint=checkpoint_key,
    )
    new_job_id = new_run["run_id"]
    autonomous_jobs[new_job_id] = {
        "status": "pending",
        "created_at": new_run["created_at"],
        "goal": prior["goal"],
        "repo_id": prior.get("repo_id"),
        "max_iterations": request.max_iterations,
        "mirror_root": new_run.get("mirror_root"),
    }

    asyncio.create_task(
        run_autonomous_task(
            job_id=new_job_id,
            goal=prior["goal"],
            repo_id=prior.get("repo_id"),
            max_iterations=request.max_iterations,
            allowed_playbooks=request.allowed_playbooks,
            mirror_root=new_run.get("mirror_root") if request.mirror_mode else None,
            rerun_context={"checkpoint_key": checkpoint_key} if checkpoint_key else None,
        )
    )

    return AutonomousJobResponse(
        job_id=new_job_id,
        status="pending",
        created_at=new_run["created_at"],
        goal=prior["goal"],
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
    _cleanup_expired_context()
    
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

    if not request.repo_id and _requires_codebase_context(prompt):
        message = (
            "No codebase selected. The request references a codebase/repository. "
            "Select a repository to continue with playbook execution."
        )
        return PlaybookResponse(
            success=False,
            result=message,
            data=None,
            error=message,
            logs=[message],
            mirror_root=None,
        )

    # Fetch Repository Metadata if available
    repo_metadata = {}
    if request.repo_id and manifest_manager:
        repo_ids = request.repo_id if isinstance(request.repo_id, list) else [request.repo_id]
        
        # Validate that all requested repositories exist
        for r_id in repo_ids:
            if not manifest_manager.get_repository_by_id(r_id):
                raise HTTPException(status_code=404, detail=f"Repository not found or not indexed: {r_id}")
        
        # Use the first repository for playbook context metadata
        repo = manifest_manager.get_repository_by_id(repo_ids[0])
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

    run_row = None
    if request.mirror_mode and run_orchestrator:
        primary_repo = request.repo_id[0] if isinstance(request.repo_id, list) and request.repo_id else request.repo_id
        run_row = run_orchestrator.create_run(goal=prompt, repo_id=primary_repo)
        run_orchestrator.mark_running(run_row["run_id"])
        user_input["execution_context"] = {
            "run_id": run_row["run_id"],
            "mirror_root": run_row["mirror_root"],
            "prefer_mirror_reads": True,
        }
    
    # Execute
    result = await playbook_executor.execute(final_playbook_name, user_input)
    if run_row and run_orchestrator:
        if result.get("success"):
            outputs = result.get("outputs", {}) or {}
            run_orchestrator.mark_completed(
                run_id=run_row["run_id"],
                result=result,
                iterations=int(outputs.get("iterations", 0) or 0),
                steps_taken=1,
            )
        else:
            run_orchestrator.mark_failed(run_row["run_id"], result.get("error", "Playbook execution failed"))
    
    # Extract result payload from normalized outputs
    outputs = result.get("outputs", {}) or {}
    output_text = outputs.get("result")
    output_data = outputs.get("data")

    # If textual result is empty but structured data exists, provide compact text fallback
    if (not output_text or not str(output_text).strip()) and output_data is not None:
        import json as _json

        try:
            output_text = _json.dumps(output_data, default=str)
        except Exception:
            output_text = str(output_data)
    elif not output_text or not str(output_text).strip():
        output_text = "Playbook completed but returned no analyzable findings. Check tool logs and repository scope."
    
    return PlaybookResponse(
        success=result["success"],
        result=output_text,
        data=output_data,
        error=result.get("error"),
        logs=result.get("logs", []),
        mirror_root=(run_row["mirror_root"] if run_row else None),
    )
