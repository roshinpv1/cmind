from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal
from ..llm.agents import LangGraphDocAgent
from ..llm.factory import get_llm_client
from ..storage.lancedb_storage import LanceDBStorage
from ..graph.graph_query import GraphQueryService
from ..graph.graph_db import SQLiteGraphAdapter
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# Global services (initialized on startup)
lance_storage: Optional[LanceDBStorage] = None
graph_service: Optional[GraphQueryService] = None
agent_embedder = None

# Simple job storage (todo: use database)
agent_jobs = {}


class AgentExecuteRequest(BaseModel):
    agent_type: Literal["doc_generator"]
    repo_id: str
    task: str = "generate_readme"
    config: dict = {}


class AgentJobResponse(BaseModel):
    job_id: str
    status: str
    created_at: str


class AgentResultResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = None


def init_agent_services(lance: LanceDBStorage, graph_db: SQLiteGraphAdapter, embedder=None):
    """Initialize services for agents."""
    global lance_storage, graph_service, agent_embedder
    lance_storage = lance
    graph_service = GraphQueryService(graph_db)
    agent_embedder = embedder


async def run_agent_task(job_id: str, agent_type: str, repo_id: str, task: str, config: dict):
    """Background task to run agent."""
    try:
        agent_jobs[job_id]["status"] = "running"
        
        if agent_type == "doc_generator":
            # Create LangGraph agent
            agent = LangGraphDocAgent(
                search_service=lance_storage,
                graph_service=graph_service,
                llm_client=get_llm_client(),
                embedder=agent_embedder
            )
            
            # Set defaults in config if not present
            config.setdefault("doc_type", "readme")
            config.setdefault("scope", "entire_repo")
            config.setdefault("include_examples", True)
            
            # Execute workflow
            result_dict = await agent.execute(
                repo_id=repo_id,
                **config
            )
            
            # Check for errors
            if result_dict.get("error"):
                agent_jobs[job_id]["status"] = "failed"
                agent_jobs[job_id]["error"] = result_dict["error"]
            else:
                agent_jobs[job_id]["status"] = "completed"
                agent_jobs[job_id]["result"] = result_dict["documentation"]
                agent_jobs[job_id]["progress"] = result_dict.get("progress", [])
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")

            
    except Exception as e:
        agent_jobs[job_id]["status"] = "failed"
        agent_jobs[job_id]["error"] = str(e)
        print(f"[AGENT] Job {job_id} failed: {e}")
        import traceback
        traceback.print_exc()


@router.post("/execute", response_model=AgentJobResponse)
async def execute_agent(request: AgentExecuteRequest, background_tasks: BackgroundTasks):
    """
    Execute an agent task asynchronously.
    
    Returns a job_id to check status and get results.
    """
    job_id = str(uuid.uuid4())
    
    agent_jobs[job_id] = {
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "agent_type": request.agent_type,
        "repo_id": request.repo_id,
        "task": request.task
    }
    
    # Run in background
    background_tasks.add_task(
        run_agent_task,
        job_id=job_id,
        agent_type=request.agent_type,
        repo_id=request.repo_id,
        task=request.task,
        config=request.config
    )
    
    return AgentJobResponse(
        job_id=job_id,
        status="pending",
        created_at=agent_jobs[job_id]["created_at"]
    )


@router.get("/{job_id}/status")
async def get_agent_status(job_id: str):
    """Get the status of an agent job."""
    if job_id not in agent_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = agent_jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"]
    }


@router.get("/{job_id}/result", response_model=AgentResultResponse)
async def get_agent_result(job_id: str):
    """Get the result of a completed agent job."""
    if job_id not in agent_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = agent_jobs[job_id]
    
    return AgentResultResponse(
        job_id=job_id,
        status=job["status"],
        result=job.get("result"),
        error=job.get("error")
    )
