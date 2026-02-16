"""
FastAPI control plane for CodeMind.

REST API for indexing, search, and system management.
"""

from dotenv import load_dotenv
load_dotenv()  # Load .env before anything reads os.environ

from contextlib import asynccontextmanager

from datetime import UTC, datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from codemind.jobs import JobManager, JobStatus
from codemind.llm.factory import get_llm_client, get_chat_model
from codemind.storage import ManifestManager
from codemind.storage.lancedb_storage import LanceDBStorage


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
    embedding_model: str | None = None
    embedding_dim: int | None = None


# FastAPI app with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources."""
    from codemind.graph import SQLiteGraphAdapter
    from codemind.graph.graph_query import GraphQueryService

    # Configure LangSmith tracing (if API key is set)
    from codemind.llm.tracing import configure_tracing
    tracing_status = configure_tracing()
    if tracing_status["enabled"]:
        print(f"[SERVER] 📊 LangSmith tracing enabled → project: {tracing_status['project']}")
    else:
        print(f"[SERVER] 📊 LangSmith tracing disabled ({tracing_status['reason']})")

    # Initialize services
    app.state.manifest = ManifestManager()
    app.state.lance_storage = LanceDBStorage()
    app.state.job_manager = JobManager()
    
    # Graph DB (SQLite Adapter) - Reuse connection from job_manager
    app.state.graph_db = SQLiteGraphAdapter(app.state.job_manager.db)
    app.state.graph_query = GraphQueryService(app.state.graph_db)  # Graph query service

    # Initialize agent services (existing doc generator)
    from . import agents as agents_module
    from codemind.indexer.embedder import EmbeddingGenerator
    app.state.embedder = EmbeddingGenerator()
    agents_module.init_agent_services(app.state.lance_storage, app.state.graph_db, app.state.embedder)
    app.include_router(agents_module.router)
    print("[SERVER] ✅ Agent system initialized")
    
    # Initialize autonomous agent system (playbook-based, LangChain-native)
    from .autonomous_agents import init_autonomous_agents, router as autonomous_router
    init_autonomous_agents(
        app.state.lance_storage,
        app.state.graph_query,
        get_chat_model(),
        app.state.embedder,
        app.state.manifest,  # Pass manifest manager
        app.state.job_manager.db # Pass DB instance
    )
    app.include_router(autonomous_router)
    print("[SERVER] ✅ Autonomous agent system initialized (LangChain)")

    yield

    # Cleanup
    app.state.graph_db.close()


app = FastAPI(title="CodeMind API", version="0.1.0", lifespan=lifespan)


@app.post("/api/v1/index", response_model=IndexResponse)
async def index_repository(request: IndexRequest):
    """Start indexing a repository (queues a job for the worker)."""
    job_manager: JobManager = app.state.job_manager
    manifest: ManifestManager = app.state.manifest

    # Determine identifier for job tracking
    identifier = request.repo_url or request.repo_path

    # Try to find existing repo ID to ensure stability
    repo_id = None
    
    if request.repo_url:
        existing_repo = manifest.get_repository_by_url_and_branch(
            request.repo_url, 
            branch=request.branch or "main"
        )
        if existing_repo:
            repo_id = existing_repo.repo_id
            print(f"[SERVER] Found existing repo ID {repo_id} for URL {request.repo_url}")

    if not repo_id and request.repo_path:
        existing_repo = manifest.get_repository(request.repo_path)
        if existing_repo:
            repo_id = existing_repo.repo_id
            print(f"[SERVER] Found existing repo ID {repo_id} for path {request.repo_path}")

    # If still no ID, compute/generate one
    if not repo_id:
        if request.repo_path:
            repo_id = manifest._compute_repo_id(request.repo_path)
        else:
            # For Git URLs, use a temporary ID based on URL
            from codemind.utils.git_utils import GitRepoManager
            git_manager = GitRepoManager()
            repo_id = git_manager._get_repo_id(request.repo_url)
        print(f"[SERVER] Generated new repo ID {repo_id}")

    # Create job with all parameters — the worker will pick this up
    job_id = job_manager.create_job(
        repo_path=identifier,
        repo_url=request.repo_url,
        branch=request.branch,
        repo_id=repo_id,
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
    embedder = app.state.embedder

    # Generate query embedding with proper task prefix
    query_embedding = embedder.encode_query(request.query)

    # Validate repo_id if provided
    if request.repo_id:
        manifest: ManifestManager = app.state.manifest
        if not manifest.get_repository_by_id(request.repo_id):
            raise HTTPException(status_code=404, detail=f"Repository {request.repo_id} not found")

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
            score=1.0 - r.get("_distance", 1.0),
            context=context
        ))

    return results


@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint with embedding model info."""
    embedder = app.state.embedder
    
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        embedding_model=embedder.model_name,
        embedding_dim=embedder.embedding_dim
    )




class RepoListItem(BaseModel):
    """Repository list item."""
    repo_id: str
    name: str | None = None
    branch: str | None = None
    path: str
    repo_url: str | None = None  # New field
    status: str
    total_files: int
    last_indexed: str
    
    # Metadata
    first_author: str | None = None
    total_commits: int | None = None
    last_pr_title: str | None = None
    last_pr_user: str | None = None
    last_pr_merged_at: str | None = None


@app.get("/api/v1/repos", response_model=list[RepoListItem])
async def list_repos():
    """List all indexed repositories."""
    manifest: ManifestManager = app.state.manifest
    repos = manifest.list_repositories()
    
    results = []
    for r in repos:
        # Infer properties from path
        # Expected path format: .../data/repos/<name>/<branch>
        path_parts = str(r.repo_path).split("/")
        name = None
        branch = None
        
        # Heuristic: verify if it looks like our git cache structure
        if len(path_parts) >= 2:
            # Check if parent is 'repos' (maybe too specific?)
            # Just take last two parts
            potential_branch = path_parts[-1]
            potential_name = path_parts[-2]
             
            # Exclude standard dirs if any
            if potential_name != "repos":
                 name = potential_name
                 branch = potential_branch

        results.append(RepoListItem(
            repo_id=r.repo_id,
            name=name or "unknown",
            branch=branch or "unknown",
            path=r.repo_path,
            repo_url=r.repo_url,
            status="indexed",
            total_files=r.total_files_indexed,
            last_indexed=r.last_indexed_at.isoformat(),
            first_author=r.first_author,
            total_commits=r.total_commits,
            last_pr_title=r.last_pr_title,
            last_pr_user=r.last_pr_user,
            last_pr_merged_at=r.last_pr_merged_at
        ))

    
    return results


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
    manifest: ManifestManager = app.state.manifest
    
    # Validate repo_id
    if not manifest.get_repository_by_id(request.repo_id):
         raise HTTPException(status_code=404, detail=f"Repository {request.repo_id} not found")
    
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



class CatalogCreateRequest(BaseModel):
    """Request to create a catalog entry."""
    repo_id: str
    playbook_name: str = "code_analyzer"
    prompt: str | None = None


class CatalogSearchRequest(BaseModel):
    """Request to search catalog entries."""
    query: str
    repo_id: str | None = None
    limit: int = 5
    min_score: float = 0.8


@app.post("/api/v1/catalogs")
async def create_catalog_entry(request: CatalogCreateRequest):
    """
    Execute a playbook on a repo and store the result in the catalog.
    """
    import uuid
    import json
    from codemind.api.autonomous_agents import playbook_executor
    
    if not playbook_executor:
         raise HTTPException(status_code=503, detail="Playbook system not initialized")

    manifest: ManifestManager = app.state.manifest
    lance_storage: LanceDBStorage = app.state.lance_storage
    embedder = app.state.embedder
    
    # 1. Fetch Repository Metadata
    repo = manifest.get_repository_by_id(request.repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository {request.repo_id} not found")
    
    # Construct rich metadata object from repo manifest
    repo_metadata = {
        "repo_id": repo.repo_id,
        "name": repo.repo_path.split("/")[-2] if len(repo.repo_path.split("/")) > 2 else "unknown",
        "path": repo.repo_path,
        "first_author": repo.first_author,
        "total_commits": repo.total_commits,
        "last_pr_title": repo.last_pr_title,
        "last_pr_user": repo.last_pr_user,
        "last_pr_merged_at": repo.last_pr_merged_at,
        "last_indexed": repo.last_indexed_at.isoformat()
    }
    
    # 2. Execute Playbook
    # Resolve prompt
    from codemind.playbooks import PlaybookRegistry
    # We need to access the registry. Since it's not in app.state, we might need to instantiate or access global
    # Ideally registry is singleton or in app.state. Checking autonomous_agents.py...
    # It seems autonomous_agents.init_autonomous_agents initializes a local registry but doesn't expose it easily?
    # Actually, PlaybookExecutor has the registry.
    
    prompt = request.prompt
    if not prompt:
        # Fetch default from playbook definition
        # We can get it via playbook_executor.registry if accessible, or just reload it.
        # But playbook_executor is available here.
        registry = playbook_executor.registry
        playbook_def = registry.get_playbook(request.playbook_name)
        if playbook_def and playbook_def.default_prompt:
            prompt = playbook_def.default_prompt
        else:
             raise HTTPException(status_code=400, detail="Prompt is required (no default found for playback)")

    user_input = {
        "query": prompt,
        "goal": prompt,
        "repo_id": request.repo_id,
        "context": repo_metadata
    }
    
    print(f"[CATALOG] Executing playbook '{request.playbook_name}' for repo {request.repo_id}")
    result = await playbook_executor.execute(request.playbook_name, user_input)
    
    if not result["success"]:
        raise HTTPException(status_code=500, detail=f"Playbook execution failed: {result.get('error')}")
    
    execution_result = result["outputs"].get("result", "")
    
    # 3. Retrieve the full content from SQLite
    # The playbook's tool 'save_catalog_entry' has already persisted it to SQLite and LanceDB(chunks).
    # We just need to fetch it to return it to the user.
    
    full_catalog_data = {}
    catalog_status = "created"
    
    # We can trust the tool saved it under request.repo_id
    try:
        from codemind.storage.database import CatalogStore
        db = app.state.job_manager.db
        if db:
            with db.get_session() as session:
                entry = session.query(CatalogStore).filter_by(repo_id=request.repo_id).first()
                if entry:
                    import json
                    try:
                        # Return the parsed JSON content
                        full_content = json.loads(entry.content)
                        full_catalog_data = full_content
                        # Add timestamps
                        full_catalog_data["created_at"] = entry.created_at
                        full_catalog_data["updated_at"] = entry.updated_at
                    except:
                        full_catalog_data = {"raw_content": entry.content}
                else:
                    catalog_status = "tool_failed_no_entry"
                    full_catalog_data = {"error": "Catalog entry not found in storage after playbook execution."}
    except Exception as e:
        print(f"[ERROR] Failed to fetch catalog entry: {e}")
        full_catalog_data = {"error": f"Failed to fetch entry: {str(e)}", "partial_result": execution_result}

    return {
        "status": catalog_status,
        "repo_id": request.repo_id,
        "catalog_entry": full_catalog_data,
        "llm_response": execution_result # Keep the original text response just in case
    }

@app.get("/api/v1/catalogs/{repo_id}")
async def get_catalog_entries(repo_id: str):
    """Get catalog entries for a repository."""
    manifest: ManifestManager = app.state.manifest
    try:
        # We allow fetching even if not in manifest (it might be a detached repo)
        # But good to validate if possible.
        pass
    except:
        pass

    # Fetch from SQLite
    try:
        from codemind.storage.database import CatalogStore
        db = app.state.job_manager.db
        if db:
            with db.get_session() as session:
                entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
                if entry:
                    import json
                    try:
                        content = json.loads(entry.content)
                        # Inject timestamps
                        content["created_at"] = entry.created_at
                        content["updated_at"] = entry.updated_at
                        # Return as a list because the endpoint name suggests plurality/LanceDB legacy
                        return [content]
                    except:
                        # Fallback for raw text
                        return [{"result": entry.content, "error": "Failed to parse JSON content"}]
        
        return []
    except Exception as e:
        print(f"[ERROR] Failed to get catalog entries: {e}")
        return []


@app.post("/api/v1/catalogs/search")
async def search_catalog(request: CatalogSearchRequest):
    """Semantic search over catalog entries."""
    lance_storage: LanceDBStorage = app.state.lance_storage
    embedder = app.state.embedder
    
    # Validate repo_id if provided
    if request.repo_id:
        manifest: ManifestManager = app.state.manifest
        if not manifest.get_repository_by_id(request.repo_id):
            raise HTTPException(status_code=404, detail=f"Repository {request.repo_id} not found")
    
    # Generate query embedding
    query_embedding = embedder.encode_query(request.query)
    
    # Search
    # Note: lance_storage fetches 2*limit to allow for filtering
    raw_results = lance_storage.search_catalogs(
        query_embedding=query_embedding,
        repo_id=request.repo_id,
        limit=request.limit
    )
    
    processed_results = []
    for item in raw_results:
        # LanceDB returns _distance. For cosine, distance = 1 - similarity.
        # So similarity = 1 - distance.
        score = 1 - item.get("_distance", 1.0)
        
        if score >= request.min_score:
            # Clean up result (remove heavy embedding)
            if "embedding" in item:
                del item["embedding"]
            
            item["score"] = score
            processed_results.append(item)
    
    # Sort by score descending (just in case)
    processed_results.sort(key=lambda x: x["score"], reverse=True)
    
    return processed_results[:request.limit]
