"""
FastAPI control plane for CodeMind.

REST API for indexing, search, and system management.
"""

from dotenv import load_dotenv
load_dotenv()  # Load .env before anything reads os.environ

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from codemind.jobs import JobManager, JobStatus
from codemind.llm.factory import get_llm_client
from codemind.storage import ManifestManager
from codemind.storage.lancedb_storage import LanceDBStorage
from codemind.workflows import IndexingState, IndexingWorkflow


# Request/Response models
class IndexRequest(BaseModel):
    """Request to index a repository."""

    repo_path: str | None = None  # Local filesystem path
    repo_url: str | None = None  # Git repository URL (https/ssh)
    branch: str = "main"  # Branch to index (for git URLs)

    def model_post_init(self, __context):
        """Validate that either repo_path or repo_url is provided."""
        if not self.repo_path and not self.repo_url:
            raise ValueError("Either repo_path or repo_url must be provided")
        if self.repo_path and self.repo_url:
            raise ValueError("Provide either repo_path or repo_url, not both")


class IndexResponse(BaseModel):
    """Response from index request."""

    job_id: str
    status: str
    repo_id: str | None = None


class JobStatusResponse(BaseModel):
    """Job status response."""

    job_id: str
    repo_path: str
    status: str
    stage: str | None
    progress: int
    error: str | None
    repo_id: str | None = None


class SearchRequest(BaseModel):
    """Semantic search request."""

    query: str
    repo_id: str | None = None
    limit: int = 10
    search_mode: str = "hybrid"  # "semantic", "structural", "hybrid"
    filters: dict | None = None  # {"file_type": ".py", "class_name": "FastAPI"}
    expand_context: bool = True  # Include graph context in results


class SearchResult(BaseModel):
    """Search result item."""

    chunk_text: str
    file_path: str
    start_line: int
    score: float
    context: dict | None = None  # Graph context: classes, functions, etc.


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


# FastAPI app with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources."""
    from codemind.graph import KuzuGraphDB
    from codemind.graph.graph_query import GraphQueryService

    # Initialize services
    app.state.manifest = ManifestManager()
    app.state.lance_storage = LanceDBStorage()
    app.state.graph_db = KuzuGraphDB()  # Persistent Kùzu graph
    app.state.graph_query = GraphQueryService(app.state.graph_db)  # Graph query service
    app.state.job_manager = JobManager()
    app.state.workflow = IndexingWorkflow(
        app.state.manifest, app.state.lance_storage, app.state.graph_db
    )

    # Initialize agent services (existing doc generator)
    from . import agents as agents_module
    agents_module.init_agent_services(app.state.lance_storage, app.state.graph_db, app.state.workflow.embedder)
    app.include_router(agents_module.router)
    print("[SERVER] ✅ Agent system initialized")
    
    # Initialize autonomous agent system (skill-based)
    from .autonomous_agents import init_autonomous_agents, router as autonomous_router
    init_autonomous_agents(
        app.state.lance_storage,
        app.state.graph_query,
        get_llm_client(),
        app.state.workflow.embedder
    )
    app.include_router(autonomous_router)
    print("[SERVER] ✅ Autonomous agent system initialized")

    yield

    # Cleanup
    app.state.graph_db.close()


app = FastAPI(title="CodeMind API", version="0.1.0", lifespan=lifespan)


def run_indexing_job(
    job_id: str,
    repo_path: str | None,
    repo_url: str | None,
    branch: str,
    repo_id: str,
):
    """Background task to run indexing."""
    from codemind.utils.git_utils import GitRepoManager

    job_manager: JobManager = app.state.job_manager
    workflow: IndexingWorkflow = app.state.workflow

    try:
        # Update job to running
        job_manager.update_job(job_id, status=JobStatus.RUNNING, stage="starting")

        # Handle git URL if provided
        if repo_url:
            git_manager = GitRepoManager()
            local_path, computed_repo_id, _ = git_manager.ensure_repo(repo_url, branch)
            actual_repo_path = str(local_path)
            # Use the computed repo_id from GitRepoManager
            actual_repo_id = computed_repo_id
        else:
            actual_repo_path = repo_path
            actual_repo_id = repo_id

        # Create state
        state = IndexingState(repo_path=actual_repo_path, repo_id=actual_repo_id, job_id=job_id)

        # Run workflow
        final_state = workflow.run(state)

        # Update job based on result
        if final_state.error:
            job_manager.update_job(
                job_id,
                status=JobStatus.FAILED,
                stage=final_state.stage,
                error=final_state.error,
            )
        else:
            job_manager.update_job(
                job_id, status=JobStatus.COMPLETED, stage="completed", progress=100
            )

    except Exception as e:
        job_manager.update_job(job_id, status=JobStatus.FAILED, error=str(e), progress=0)


@app.post("/api/v1/index", response_model=IndexResponse)
async def index_repository(request: IndexRequest, background_tasks: BackgroundTasks):
    """Start indexing a repository (async)."""
    job_manager: JobManager = app.state.job_manager
    manifest: ManifestManager = app.state.manifest

    # Determine identifier for job tracking
    identifier = request.repo_url or request.repo_path

    # Compute repo_id upfront
    if request.repo_path:
        repo_id = manifest._compute_repo_id(request.repo_path)
    else:
        # For Git URLs, use a temporary ID based on URL
        # The actual repo_id will be determined after cloning
        from codemind.utils.git_utils import GitRepoManager

        git_manager = GitRepoManager()
        repo_id = git_manager._get_repo_id(request.repo_url)

    # Create job
    job_id = job_manager.create_job(identifier)

    # Queue background task
    background_tasks.add_task(
        run_indexing_job,
        job_id,
        request.repo_path,
        request.repo_url,
        request.branch,
        repo_id,
    )

    return IndexResponse(job_id=job_id, status="pending", repo_id=repo_id)


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get job status."""
    job_manager: JobManager = app.state.job_manager
    manifest: ManifestManager = app.state.manifest

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Try to get repo_id from manifest
    repo_id = None
    if job.repo_path:
        repo = manifest.get_repository(job.repo_path)
        if repo:
            repo_id = repo.repo_id
        else:
            # Compute it if repo doesn't exist yet
            repo_id = manifest._compute_repo_id(job.repo_path)

    return JobStatusResponse(
        job_id=job.id,
        repo_path=job.repo_path,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        error=job.error,
        repo_id=repo_id,
    )


@app.post("/api/v1/search", response_model=list[SearchResult])
async def semantic_search(request: SearchRequest):
    """Perform semantic or hybrid search over code."""
    lance_storage: LanceDBStorage = app.state.lance_storage
    graph_query = app.state.graph_query
    embedder = app.state.workflow.embedder

    # Generate query embedding with proper task prefix
    query_embedding = embedder.encode_query(request.query)

    results = []

    # Handle different search modes
    if request.search_mode == "structural":
        # Pure structural search - no semantic similarity
        if not request.filters or not request.repo_id:
            return []
        
        file_paths = graph_query.filter_by_structure(request.repo_id, request.filters)
        
        # Return structural results
        for file_path in file_paths[:request.limit]:
            context = None
            if request.expand_context:
                context = graph_query.get_file_context(request.repo_id, file_path)
            
            results.append(SearchResult(
                chunk_text=f"File: {file_path}",
                file_path=file_path,
                start_line=1,
                score=1.0,
                context=context
            ))
        
        return results

    # Semantic or Hybrid search
    candidate_files = None
    
    # Check if filters are actually provided and convert to dict
    filters_dict = None
    if request.filters:
        # Handle both dict and Pydantic model
        if isinstance(request.filters, dict):
            filters_dict = {k: v for k, v in request.filters.items() if v is not None}
        else:
            filters_dict = request.filters.model_dump(exclude_none=True)
    
    has_filters = filters_dict and any(v is not None for v in filters_dict.values())
    
    if request.search_mode == "hybrid" and has_filters and request.repo_id:
        # Apply structural filters first
        try:
            print(f"[SEARCH] Applying filters: {filters_dict}")
            
            candidate_files = graph_query.filter_by_structure(request.repo_id, filters_dict)
            print(f"[SEARCH] Hybrid mode: {len(candidate_files) if candidate_files else 0} candidate files from graph")
            
            if not candidate_files:
                print(f"[SEARCH] No candidates from graph filter, falling back to semantic-only")
                # Fallback: treat as semantic search if no graph results
                candidate_files = None
        except Exception as e:
            print(f"[SEARCH] Graph filter error: {e}, falling back to semantic-only")
            import traceback
            traceback.print_exc()
            candidate_files = None

    # Perform semantic search
    search_results = lance_storage.search(
        query_embedding, 
        repo_id=request.repo_id, 
        limit=request.limit
    )

    # Filter by candidate files if in hybrid mode with valid results
    if candidate_files:
        print(f"[SEARCH] Candidate files from graph ({len(candidate_files)} total):")
        for i, f in enumerate(candidate_files[:3]):
            print(f"  [{i}] '{f}'")
        
        print(f"\n[SEARCH] LanceDB file paths ({len(search_results)} results):")
        for i, r in enumerate(search_results[:3]):
            print(f"  [{i}] '{r['file_path']}'")
        
        # Normalize paths: graph has relative paths, LanceDB has full paths
        # LanceDB format: data/repos/{repo_name}/{branch}/{relative_path}
        # Graph format: {relative_path}
        
        # Check if we need to normalize by comparing a sample
        if search_results and candidate_files:
            sample_lance = search_results[0]['file_path']
            sample_graph = candidate_files[0]
            
            # If LanceDB path doesn't match graph path, try to normalize
            if not sample_lance.endswith(sample_graph):
                print(f"[SEARCH] Path normalization needed")
                
                # Strategy: check if LanceDB path ends with graph path
                normalized_candidates = set()
                for lance_result in search_results:
                    lance_path = lance_result['file_path']
                    # Check if this lance path ends with any candidate
                    for candidate in candidate_files:
                        if lance_path.endswith(candidate):
                            normalized_candidates.add(lance_path)
                            break
                
                print(f"[SEARCH] After normalization: {len(normalized_candidates)} candidates matched")
                
                search_results = [
                    r for r in search_results 
                    if r["file_path"] in normalized_candidates
                ]
            else:
                # Direct match
                search_results = [
                    r for r in search_results 
                    if r["file_path"] in candidate_files
                ]
        
        print(f"[SEARCH] After filtering: {len(search_results)} results")

    # Build response with optional context expansion
    for r in search_results:
        context = None
        if request.expand_context and request.repo_id:
            try:
                context = graph_query.get_file_context(request.repo_id, r["file_path"])
            except Exception as e:
                print(f"[SEARCH] Context expansion failed for {r['file_path']}: {e}")
                context = None
        
        results.append(SearchResult(
            chunk_text=r["chunk_text"],
            file_path=r["file_path"],
            start_line=r["start_line"],
            score=r.get("_distance", 0.0),
            context=context
        ))

    return results


@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="healthy", version="0.1.0")


@app.get("/api/v1/stats")
async def get_stats():
    """Get system stats."""
    job_manager: JobManager = app.state.job_manager

    # Get counts (simplified)
    jobs = job_manager.list_jobs()

    return {
        "total_jobs": len(jobs),
        "pending_jobs": len([j for j in jobs if j.status == JobStatus.PENDING]),
        "running_jobs": len([j for j in jobs if j.status == JobStatus.RUNNING]),
        "completed_jobs": len([j for j in jobs if j.status == JobStatus.COMPLETED]),
        "failed_jobs": len([j for j in jobs if j.status == JobStatus.FAILED]),
    }


class GraphQueryRequest(BaseModel):
    """Graph query request."""
    repo_id: str
    query_type: str  # "files", "classes", "functions", "symbol"
    pattern: str | None = None
    file_type: str | None = None
    class_name: str | None = None
    symbol_name: str | None = None


@app.post("/api/v1/graph/query")
async def query_graph(request: GraphQueryRequest):
    """Query code structure using Kùzu graph."""
    graph_query = app.state.graph_query
    
    if request.query_type == "files":
        return graph_query.find_files_by_pattern(
            request.repo_id, 
            pattern=request.pattern,
            file_type=request.file_type
        )
    
    elif request.query_type == "classes":
        if request.pattern:
            return graph_query.find_symbol_by_name(
                request.repo_id, 
                request.pattern, 
                symbol_type="Class"
            )
        return []
    
    elif request.query_type == "functions":
        if request.class_name:
            return graph_query.get_functions_in_class(
                request.repo_id, 
                request.class_name
            )
        elif request.pattern:
            return graph_query.find_symbol_by_name(
                request.repo_id, 
                request.pattern, 
                symbol_type="Function"
            )
        return []
    
    elif request.query_type == "symbol":
        if request.symbol_name:
            return graph_query.find_symbol_by_name(
                request.repo_id, 
                request.symbol_name
            )
        return []
    
    return []
