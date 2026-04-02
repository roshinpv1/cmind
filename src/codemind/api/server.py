"""
FastAPI control plane for CodeMind.

REST API for indexing, search, and system management.
"""
from dotenv import load_dotenv
load_dotenv()  # Load .env before anything reads os.environ

from contextlib import asynccontextmanager
import os
import sys
import logging
import functools

from datetime import UTC, datetime
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from codemind.api.auth import get_current_user, require_user, create_access_token, sync_user_to_db

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
    org: str | None = None  # Organization owning this component

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
    from codemind.graph import KuzuGraphAdapter
    from codemind.graph.graph_query import GraphQueryService

    # Initialize services
    app.state.manifest = ManifestManager()
    app.state.lance_storage = LanceDBStorage()
    app.state.job_manager = JobManager()
    
    # Graph DB (Kuzu) - Independent embedded graph database
    app.state.graph_db = KuzuGraphAdapter()
    app.state.graph_query = GraphQueryService(app.state.graph_db)

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

    # Initialize PlaybookStore API (CRUD + marketplace)
    from .playbook_api import init_playbook_api, router as playbook_api_router
    init_playbook_api(app.state.job_manager.db)
    app.include_router(playbook_api_router)
    print("[SERVER] ✅ PlaybookStore API initialized")

    # Log all registered routes at startup
    routes_msg = "\n===== REGISTERED ROUTES =====\n"
    for route in app.routes:
        if hasattr(route, "methods"):
            for method in route.methods:
                routes_msg += f"  {method:6s} {route.path}\n"
    routes_msg += "============================="
    print(routes_msg, file=sys.stderr)

    # Start index worker as background thread (shares Kuzu instance)
    import threading
    from codemind.worker.index_worker import IndexWorker, _shutdown as _worker_shutdown
    import codemind.worker.index_worker as worker_module

    worker = IndexWorker(
        poll_interval=int(os.getenv("WORKER_POLL_INTERVAL", "5")),
        db_path=os.getenv("CODEMIND_DB_PATH", os.path.join(os.getenv("CODEMIND_BASE_PATH", "./tmp/"), "codemind.db")),
        graph_db=app.state.graph_db,  # Share Kuzu instance
    )
    worker_thread = threading.Thread(target=worker.run, daemon=True, name="index-worker")
    worker_thread.start()

    yield

    # Cleanup: signal worker to stop, then close graph
    worker_module._shutdown = True
    worker_thread.join(timeout=10)
    print("[SERVER] ⏹  Index worker stopped")
    app.state.graph_db.close()


logger = logging.getLogger("codemind.api")

# Force flush for print statements so they appear even when stdout is redirected
_original_print = print
print = functools.partial(_original_print, flush=True)

app = FastAPI(title="CodeMind API", version="0.1.0", lifespan=lifespan)

# CORS middleware — allow frontend dev server (Vite) to make API calls
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Debug middleware: log all incoming requests to /repos endpoints
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class RepoDebugMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if "/repos" in path:
            print(f"[MIDDLEWARE] {request.method} {path} (full URL: {request.url})")
        response = await call_next(request)
        if "/repos" in path:
            print(f"[MIDDLEWARE] → Response status: {response.status_code}")
        return response

app.add_middleware(RepoDebugMiddleware)


# Diagnostic endpoint — defined early so it always registers
@app.get("/api/v1/debug/routes")
async def debug_routes():
    """Return all registered routes. Use this to verify endpoint registration."""
    routes = []
    for route in app.routes:
        if hasattr(route, "methods"):
            for method in route.methods:
                routes.append({"method": method, "path": route.path})
    return {"total": len(routes), "routes": sorted(routes, key=lambda r: r["path"])}

@app.post("/api/v1/index", response_model=IndexResponse)
async def index_repository_job(
    request: IndexRequest,
    user: dict = Depends(require_user)
):
    """Start an indexing job for a repository."""
    job_manager: JobManager = app.state.job_manager
    manifest: ManifestManager = app.state.manifest

    # Determine identifier for job tracking
    identifier = request.repo_url or request.repo_path

    # Validate repository and branch existence before continuing
    from codemind.utils.git_utils import GitRepoManager
    git_manager = GitRepoManager()
    is_valid, err_msg = git_manager.validate_repository_and_branch(
        repo_url=identifier, 
        branch=request.branch or "main"
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Validation failed: {err_msg}")

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
        existing_repo = manifest.get_repository(request.repo_path, branch=request.branch or "main")
        if existing_repo:
            repo_id = existing_repo.repo_id
            print(f"[SERVER] Found existing repo ID {repo_id} for path {request.repo_path} branch {request.branch or 'main'}")

    # If still no ID, compute/generate one
    if not repo_id:
        if request.repo_path:
            repo_id = manifest._compute_repo_id(request.repo_path, request.branch or "main")
        else:
            # For Git URLs, use a temporary ID based on URL
            from codemind.utils.git_utils import GitRepoManager
            git_manager = GitRepoManager()
            repo_id = git_manager._get_repo_id(request.repo_url, request.branch or "main")
        print(f"[SERVER] Generated new repo ID {repo_id}")

    # Create job with all parameters — the worker will pick this up
    job_id = job_manager.create_job(
        repo_path=identifier,
        repo_url=request.repo_url,
        branch=request.branch,
        repo_id=repo_id,
        org=request.org,
        user_id=user["user_id"],
    )

    return IndexResponse(job_id=job_id, status="pending", repo_id=repo_id)


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, user: dict = Depends(require_user)):
    """Get job status."""
    job_manager: JobManager = app.state.job_manager
    manifest: ManifestManager = app.state.manifest

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Ownership check (unless Admin)
    if user["role"] != "admin" and job.user_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this job")

    # Try to get repo_id from manifest
    repo_id = None
    if job.repo_path:
        repo = manifest.get_repository(job.repo_path, branch=job.branch or "main")
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
    cd_repo_url: str | None = None
    contributors: list[dict] | None = None  # [{"name": "...", "commits": N}, ...]


@app.get("/api/v1/repos", response_model=list[RepoListItem])
async def list_repos(user: dict = Depends(require_user)):
    """List indexed repositories visible to the user."""
    import json as _json
    manifest: ManifestManager = app.state.manifest
    
    # All indexed repos are visible to all authenticated users (enterprise discovery model)
    repos = manifest.list_repositories()
    
    results = []
    seen_repo_ids = set()
    
    for r in repos:
        # Infer properties from path
        path_parts = str(r.repo_path).split("/")
        name = None
        branch = None
        
        if len(path_parts) >= 2:
            potential_branch = path_parts[-1]
            potential_name = path_parts[-2]
            if potential_name != "repos":
                 name = potential_name
                 branch = potential_branch

        # Parse contributors JSON
        contributors = None
        if r.contributors:
            try:
                contributors = _json.loads(r.contributors)
            except Exception:
                contributors = None

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
            last_pr_merged_at=r.last_pr_merged_at,
            cd_repo_url=r.cd_repo_url,
            contributors=contributors,
        ))
        seen_repo_ids.add(r.repo_id)

    # Also include catalog-only repos (enterprise-indexed, not in local manifest)
    from codemind.storage.database import CatalogStore
    db_inst = manifest.db
    with db_inst.get_session() as session:
        catalogs = session.query(CatalogStore).all()
        for c in catalogs:
            if c.repo_id not in seen_repo_ids:
                results.append(RepoListItem(
                    repo_id=c.repo_id,
                    name=c.repo_name or c.repo_id,
                    branch=None,
                    path=c.repo_id,
                    repo_url=None,
                    status="catalog-only",
                    total_files=0,
                    last_indexed=str(c.updated_at or c.created_at or 0),
                    first_author=None,
                    total_commits=None,
                    last_pr_title=None,
                    last_pr_user=None,
                    last_pr_merged_at=None,
                ))

    return results


class RepoUpdateRequest(BaseModel):
    """Request to update repository metadata."""
    org: str | None = None
    repo_url: str | None = None
    branch: str | None = None
    first_author: str | None = None
    total_commits: int | None = None
    last_pr_title: str | None = None
    last_pr_user: str | None = None
    last_pr_merged_at: str | None = None


class RepoDetail(RepoListItem):
    """Detailed repository info for edit page."""
    org: str | None = None
    embedding_model: str | None = None
    embedding_version: int | None = None
    last_commit_hash: str | None = None


@app.get("/api/v1/repos/{repo_id}", response_model=RepoDetail)
async def get_repo_detail(repo_id: str):
    """Get detailed info for a single repository."""
    from urllib.parse import unquote
    repo_id = unquote(repo_id)  # Handle URL-encoded IDs
    
    print(f"[SERVER] get_repo_detail called with repo_id='{repo_id}' (len={len(repo_id)})")
    
    manifest: ManifestManager = app.state.manifest
    r = manifest.get_repository_by_id(repo_id)
    
    if r:
        # Found in manifest
        print(f"[SERVER] Found repo in manifest: {r.repo_id}")
        path_parts = str(r.repo_path).split("/")
        name = path_parts[-2] if len(path_parts) >= 2 and path_parts[-2] != "repos" else "unknown"
        branch = path_parts[-1] if len(path_parts) >= 2 else "unknown"

        return RepoDetail(
            repo_id=r.repo_id,
            name=name,
            branch=r.branch or branch,
            path=r.repo_path,
            repo_url=r.repo_url,
            org=r.org,
            status="indexed",
            total_files=r.total_files_indexed,
            last_indexed=r.last_indexed_at.isoformat(),
            first_author=r.first_author,
            total_commits=r.total_commits,
            last_pr_title=r.last_pr_title,
            last_pr_user=r.last_pr_user,
            last_pr_merged_at=r.last_pr_merged_at,
            embedding_model=r.embedding_model,
            embedding_version=r.embedding_version,
            last_commit_hash=r.last_commit_hash,
        )

    # Fallback: check catalog_store (for enterprise-indexed repos)
    print(f"[SERVER] Repo not in manifest, checking catalog_store...")
    from codemind.storage.database import CatalogStore
    db_inst = manifest.db
    with db_inst.get_session() as session:
        # Debug: list all catalog repo_ids
        all_catalogs = session.query(CatalogStore).all()
        catalog_ids = [c.repo_id for c in all_catalogs]
        print(f"[SERVER] Catalog repo_ids in DB: {catalog_ids}")
        
        catalog = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if catalog:
            print(f"[SERVER] Found repo in catalog_store: {catalog.repo_id}")
            import json as _json
            meta = {}
            if catalog.metadata_json:
                meta = catalog.metadata_json if isinstance(catalog.metadata_json, dict) else _json.loads(catalog.metadata_json)
            content = {}
            try:
                content = _json.loads(catalog.content) if catalog.content else {}
            except (ValueError, TypeError):
                pass
            
            return RepoDetail(
                repo_id=catalog.repo_id,
                name=catalog.repo_name or repo_id,
                branch=meta.get("branch"),
                path=meta.get("repo_url", repo_id),
                repo_url=meta.get("repo_url"),
                org=catalog.org,
                status="catalog-only",
                total_files=0,
                last_indexed=str(catalog.updated_at or catalog.created_at or 0),
                first_author=None,
                total_commits=None,
                last_pr_title=None,
                last_pr_user=None,
                last_pr_merged_at=None,
                embedding_model=None,
                embedding_version=None,
                last_commit_hash=None,
            )

    print(f"[SERVER] ❌ Repo '{repo_id}' not found in manifest or catalog")
    raise HTTPException(status_code=404, detail="Repository not found in manifest or catalog")


@app.post("/api/v1/repos/{repo_id}/repair")
async def repair_repo_manifest(repo_id: str):
    """Re-run manifest update for a repo that failed at the manifest step.
    
    Useful when indexing completed (embeddings, graph built) but the
    final manifest/symbol persistence crashed. Avoids re-running the
    full expensive pipeline.
    """
    from urllib.parse import unquote
    repo_id = unquote(repo_id)
    
    manifest: ManifestManager = app.state.manifest
    repo = manifest.get_repository_by_id(repo_id)
    
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found in manifest")
    
    from codemind.workflows import IndexingWorkflow
    from codemind.storage.lancedb_storage import LanceDBStorage
    
    lance = LanceDBStorage()
    graph_db = app.state.job_manager.graph_db if hasattr(app.state.job_manager, 'graph_db') else None
    
    result = IndexingWorkflow.repair_manifest(
        repo_path=repo.repo_path,
        repo_id=repo_id,
        manifest=manifest,
        lance_storage=lance,
        graph_db=graph_db,
    )
    
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["error"])
    
    return result


@app.put("/api/v1/repos/{repo_id}")
async def update_repo_metadata(repo_id: str, request: RepoUpdateRequest):
    """Update repository metadata."""
    from urllib.parse import unquote
    repo_id = unquote(repo_id)
    
    manifest: ManifestManager = app.state.manifest
    r = manifest.get_repository_by_id(repo_id)
    
    # Build kwargs for update — only include non-None fields
    update_kwargs = {}
    for field in ["org", "repo_url", "branch", "first_author", "total_commits",
                  "last_pr_title", "last_pr_user", "last_pr_merged_at"]:
        val = getattr(request, field, None)
        if val is not None:
            update_kwargs[field] = val

    if not update_kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")

    if r:
        # Update manifest
        manifest.update_repository(repo_id, **update_kwargs)
        updated_in = "manifest"
    else:
        # Fallback: update catalog_store
        from codemind.storage.database import CatalogStore
        db_inst = manifest.db
        with db_inst.get_session() as session:
            catalog = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
            if not catalog:
                raise HTTPException(status_code=404, detail="Repository not found in manifest or catalog")
            
            if "org" in update_kwargs:
                catalog.org = update_kwargs["org"]
            if "repo_url" in update_kwargs or "branch" in update_kwargs:
                import json as _json
                meta = catalog.metadata_json or {}
                if isinstance(meta, str):
                    meta = _json.loads(meta)
                if "repo_url" in update_kwargs:
                    meta["repo_url"] = update_kwargs["repo_url"]
                if "branch" in update_kwargs:
                    meta["branch"] = update_kwargs["branch"]
                catalog.metadata_json = meta
            
            from datetime import UTC, datetime
            catalog.updated_at = int(datetime.now(UTC).timestamp())
            session.commit()
            updated_in = "catalog"

    return {"status": "updated", "repo_id": repo_id, "source": updated_in, "fields_updated": list(update_kwargs.keys())}


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
    min_score: float = 0.5


@app.get("/api/v1/catalogs/list")
async def list_catalog_entries(status: str | None = None):
    """List catalog entries, optionally filtered by status."""
    from codemind.storage.database import CatalogStore
    import json as _json
    
    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db
    
    results = []
    seen_ids = set()
    with db_inst.get_session() as session:
        query = session.query(CatalogStore)
        if status:
            query = query.filter(CatalogStore.status == status)
        entries = query.order_by(CatalogStore.updated_at.desc()).all()
        for entry in entries:
            # Deduplicate by repo_id (keep most recent, which comes first)
            if entry.repo_id in seen_ids:
                continue
            seen_ids.add(entry.repo_id)
            
            meta = {}
            if entry.metadata_json:
                meta = entry.metadata_json if isinstance(entry.metadata_json, dict) else _json.loads(entry.metadata_json)
            
            # Ensure tech_stack and category are always strings
            raw_ts = meta.get("tech_stack", "")
            tech_stack = ", ".join(raw_ts) if isinstance(raw_ts, list) else str(raw_ts) if raw_ts else ""
            raw_cat = meta.get("category", "")
            category = ", ".join(raw_cat) if isinstance(raw_cat, list) else str(raw_cat) if raw_cat else ""
            
            results.append({
                "repo_id": entry.repo_id,
                "repo_name": entry.repo_name or entry.repo_id,
                "org": entry.org or "",
                "description": meta.get("summary_high_level", "")[:200],
                "tech_stack": tech_stack,
                "category": category,
                "quality_score": entry.quality_score or meta.get("quality_score", 0),
                "topics": meta.get("topics", []),
                "repo_url": entry.git_url or meta.get("repo_url", ""),
                "branch": entry.git_branch or meta.get("branch", ""),
                "status": getattr(entry, 'status', 'active') or 'active',
                "created_by": getattr(entry, 'created_by', None),
                "source_gap": getattr(entry, 'source_gap', None),
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "contributors": meta.get("contributors", []),
                "search_count": entry.search_count or 0,
                "view_count": entry.view_count or 0,
                "popularity_points": entry.popularity_points or 0,
                "likes_count": entry.likes_count or 0,
            })
    
    return results


class ProposalRequest(BaseModel):
    """Request to create a proposal from a gap."""
    gap_name: str
    gap_description: str
    architecture_layer: str | None = None
    user_query: str | None = None
    org: str | None = None
    created_by: str | None = None
    source_analysis_id: str | None = None
    build_cost_usd: int | None = None
    dev_weeks: int | None = None


class ProposalRequirementsUpdate(BaseModel):
    """Request to update proposal requirements."""
    requirements: dict


class PromoteRequest(BaseModel):
    """Request to promote a proposal."""
    git_url: str
    git_branch: str | None = "main"
    quality_score: int | None = None


@app.post("/api/v1/catalogs/propose")
async def create_proposal(request: ProposalRequest):
    """Create a proposed catalog entry from a gap, auto-generating requirements via LLM."""
    import uuid
    import time
    import json
    from codemind.storage.database import CatalogStore
    
    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db
    
    # --- Deduplication check: reject if ≥75% matching component exists ---
    gap_name_lower = request.gap_name.lower().strip()
    gap_words = set(gap_name_lower.split())
    
    with db_inst.get_session() as session:
        all_entries = session.query(CatalogStore).all()
        for entry in all_entries:
            entry_name = (entry.repo_name or entry.source_gap or "").lower().strip()
            entry_words = set(entry_name.split())
            
            if not gap_words or not entry_words:
                continue
            
            # Word overlap score
            overlap = len(gap_words & entry_words) / max(len(gap_words), 1)
            
            # Substring match bonus
            if gap_name_lower in entry_name or entry_name in gap_name_lower:
                overlap = max(overlap, 0.9)
            
            if overlap >= 0.75:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": f"A similar component already exists: '{entry.repo_name}' ({entry.status or 'active'})",
                        "existing_entry": {
                            "repo_id": entry.repo_id,
                            "repo_name": entry.repo_name,
                            "status": entry.status or "active",
                            "match_score": round(overlap, 2),
                        }
                    }
                )
    
    # Generate a unique repo_id for the proposal
    proposal_id = f"proposed-{uuid.uuid4().hex[:12]}"
    
    # Auto-generate requirements via LLM
    requirements = {}
    try:
        from codemind.llm.factory import get_chat_model
        llm = get_chat_model()
        
        req_prompt = (
            f"Generate detailed software requirements for a component called '{request.gap_name}'.\n\n"
            f"Description: {request.gap_description}\n"
        )
        if request.architecture_layer:
            req_prompt += f"Architecture Layer: {request.architecture_layer}\n"
        if request.user_query:
            req_prompt += f"Original User Need: {request.user_query}\n"
        if request.build_cost_usd:
            req_prompt += f"Estimated Build Cost: ${request.build_cost_usd}\n"
        if request.dev_weeks:
            req_prompt += f"Estimated Dev Weeks: {request.dev_weeks}\n"
        
        req_prompt += (
            "\n\nGenerate a JSON object with these fields:\n"
            "{\n"
            '  "functional_requirements": ["list of functional requirements"],\n'
            '  "non_functional_requirements": ["list of NFRs like performance, security"],\n'
            '  "api_contracts": ["list of API endpoints/contracts needed"],\n'
            '  "data_model": "description of data model needed",\n'
            '  "integration_points": ["list of integration points with other systems"],\n'
            '  "acceptance_criteria": ["list of acceptance criteria"],\n'
            '  "tech_stack_suggestion": "suggested technology stack",\n'
            '  "estimated_effort": "effort estimate"\n'
            "}\n\n"
            "Return ONLY the JSON object, no other text."
        )
        
        # Use a small max_tokens — requirement generation doesn't need 100K
        from langchain_core.messages import HumanMessage as _HumanMessage
        raw_output = await llm._agenerate_impl(
            [_HumanMessage(content=req_prompt)],
            max_tokens=60000
        )
        raw_text = raw_output.generations[0].message.content if raw_output.generations else ""
        
        # Parse JSON from LLM output
        import re
        json_match = re.search(r'```json\s*({.*?})\s*```', raw_text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'({[\s\S]*})', raw_text)
        if json_match:
            requirements = json.loads(json_match.group(1))
        else:
            requirements = {"raw_response": raw_text}
            
    except Exception as e:
        import traceback
        print(f"[PROPOSAL] Failed to auto-generate requirements: {e}")
        traceback.print_exc()
        requirements = {
            "functional_requirements": [f"Implement {request.gap_name}"],
            "non_functional_requirements": [],
            "api_contracts": [],
            "data_model": "",
            "integration_points": [],
            "acceptance_criteria": [],
            "error": f"Auto-generation failed: {str(e)}"
        }
    
    # Build content JSON (similar to catalog entry format)
    content = {
        "product_name": request.gap_name,
        "summary_high_level": request.gap_description,
        "architecture_layer": request.architecture_layer or "Unknown",
        "category": "Proposed Component",
        "tech_stack": requirements.get("tech_stack_suggestion", "TBD"),
        "quality_score": 0,
        "requirements": requirements,
    }
    if request.build_cost_usd:
        content["estimated_cost"] = request.build_cost_usd
    if request.dev_weeks:
        content["dev_weeks"] = request.dev_weeks
    
    # Save to catalog_store
    now = int(time.time())
    with db_inst.get_session() as session:
        entry = CatalogStore(
            repo_id=proposal_id,
            repo_name=request.gap_name,
            org=request.org,
            content=json.dumps(content),
            metadata_json=content,
            status="proposed",
            created_by=request.created_by,
            source_gap=request.gap_name,
            source_analysis_id=request.source_analysis_id,
            requirements=requirements,
            quality_score=0,
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
        session.commit()
    
    return {
        "repo_id": proposal_id,
        "status": "proposed",
        "gap_name": request.gap_name,
        "requirements": requirements,
        "content": content,
    }


@app.post("/api/v1/catalogs/{repo_id}/regenerate")
async def regenerate_proposal_requirements(repo_id: str):
    """Re-generate requirements for an existing proposed catalog entry using the LLM."""
    import time
    import json
    from codemind.storage.database import CatalogStore

    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db

    # Load existing entry
    with db_inst.get_session() as session:
        entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Catalog entry not found")
        if entry.status != "proposed":
            raise HTTPException(status_code=400, detail="Only proposed entries can be regenerated")

        # Extract gap data from the existing entry
        gap_name = entry.repo_name or entry.source_gap or "Unknown Component"
        meta = entry.metadata_json or {}
        gap_description = meta.get("summary_high_level", "")
        architecture_layer = meta.get("architecture_layer", "")

    # Re-run LLM to generate fresh requirements
    requirements = {}
    try:
        from codemind.llm.factory import get_chat_model
        llm = get_chat_model()

        req_prompt = (
            f"Generate detailed software requirements for a component called '{gap_name}'.\n\n"
            f"Description: {gap_description}\n"
        )
        if architecture_layer:
            req_prompt += f"Architecture Layer: {architecture_layer}\n"

        req_prompt += (
            "\n\nGenerate a JSON object with these fields:\n"
            "{\n"
            '  "functional_requirements": ["list of functional requirements"],\n'
            '  "non_functional_requirements": ["list of NFRs like performance, security"],\n'
            '  "api_contracts": ["list of API endpoints/contracts needed"],\n'
            '  "data_model": "description of data model needed",\n'
            '  "integration_points": ["list of integration points with other systems"],\n'
            '  "acceptance_criteria": ["list of acceptance criteria"],\n'
            '  "tech_stack_suggestion": "suggested technology stack",\n'
            '  "estimated_effort": "effort estimate"\n'
            "}\n\n"
            "Return ONLY the JSON object, no other text."
        )

        from langchain_core.messages import HumanMessage as _HumanMessage
        raw_output = await llm._agenerate_impl(
            [_HumanMessage(content=req_prompt)],
            max_tokens=4096
        )
        raw_text = raw_output.generations[0].message.content if raw_output.generations else ""

        import re
        json_match = re.search(r'```json\s*({.*?})\s*```', raw_text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'({[\s\S]*})', raw_text)
        if json_match:
            requirements = json.loads(json_match.group(1))
        else:
            requirements = {"raw_response": raw_text}

    except Exception as e:
        import traceback
        print(f"[REGENERATE] Failed to regenerate requirements: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")

    # Update the existing entry in-place
    now = int(time.time())
    with db_inst.get_session() as session:
        entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if entry:
            entry.requirements = requirements
            # Also update content JSON
            content = entry.metadata_json or {}
            content["requirements"] = requirements
            content["tech_stack"] = requirements.get("tech_stack_suggestion", content.get("tech_stack", "TBD"))
            entry.metadata_json = content
            entry.content = json.dumps(content)
            entry.updated_at = now
            session.commit()

    return {
        "repo_id": repo_id,
        "status": "regenerated",
        "gap_name": gap_name,
        "requirements": requirements,
    }

@app.get("/api/v1/catalogs/proposed")
@app.get("/api/v1/catalogs/propose")
async def list_proposed_entries(
    org: str | None = None,
    user: dict = Depends(require_user)
):
    """List all proposed catalog entries."""
    from codemind.storage.database import CatalogStore
    import json as _json
    
    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db
    
    results = []
    with db_inst.get_session() as session:
        query = session.query(CatalogStore).filter(CatalogStore.status.in_(["proposed", "qualified"]))
        # Visibility filter for proposals: only show to creator (unless Admin)
        if user["role"] != "admin":
            query = query.filter(CatalogStore.created_by_user_id == user["user_id"])
            
        if org:
            query = query.filter(CatalogStore.org == org)
        entries = query.order_by(CatalogStore.updated_at.desc()).all()
        for entry in entries:
            meta = {}
            if entry.metadata_json:
                meta = entry.metadata_json if isinstance(entry.metadata_json, dict) else _json.loads(entry.metadata_json)
            
            results.append({
                "repo_id": entry.repo_id,
                "repo_name": entry.repo_name or entry.repo_id,
                "org": entry.org or "",
                "description": meta.get("summary_high_level", "")[:200],
                "architecture_layer": meta.get("architecture_layer", ""),
                "status": entry.status,
                "created_by": getattr(entry, 'created_by', None),
                "source_gap": getattr(entry, 'source_gap', None),
                "requirements": getattr(entry, 'requirements', None),
                "git_url": getattr(entry, 'git_url', None),
                "git_branch": getattr(entry, 'git_branch', None),
                "quality_score": entry.quality_score,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            })
    
    return results


@app.post("/api/v1/catalogs/match-gaps")
async def match_gaps_to_proposed(request: Request, user: dict = Depends(require_user)):
    """Check if any gaps match existing proposed/qualified catalog entries.
    Returns a mapping of gap_name -> proposed_entry."""
    body = await request.json()
    gaps = body.get("gaps", [])
    if not gaps:
        return {}
    
    from codemind.storage.database import CatalogStore
    db = app.state.job_manager.db
    if not db:
        return {}
    
    matches = {}
    with db.get_session() as session:
        query = session.query(CatalogStore).filter(
            CatalogStore.status.in_(["proposed", "qualified"])
        )
        # Regular users can only see their own proposals, but everyone can see qualified ones
        if user["role"] != "admin":
            query = query.filter(
                (CatalogStore.created_by_user_id == user["user_id"]) |
                (CatalogStore.status == "qualified")
            )
            
        proposed = query.all()
        
        for gap in gaps:
            if isinstance(gap, dict):
                gap_name = str(gap.get("name") or gap.get("description") or gap.get("component_name") or "").lower()
            else:
                gap_name = str(gap).lower()
            gap_words = set(gap_name.split())
            best_match = None
            best_score = 0
            
            for entry in proposed:
                entry_name = (entry.repo_name or entry.source_gap or "").lower()
                entry_words = set(entry_name.split())
                
                # Check for word overlap
                if not gap_words or not entry_words:
                    continue
                overlap = len(gap_words & entry_words) / max(len(gap_words), 1)
                
                # Also check substring match
                if gap_name in entry_name or entry_name in gap_name:
                    overlap = max(overlap, 0.8)
                
                if overlap > best_score and overlap >= 0.3:
                    best_score = overlap
                    best_match = entry
            
            if best_match:
                import json as _json
                if isinstance(gap, dict):
                    key = str(gap.get("name") or gap.get("description") or gap.get("component_name") or _json.dumps(gap))
                else:
                    key = str(gap)
                matches[key] = {
                    "repo_id": best_match.repo_id,
                    "repo_name": best_match.repo_name,
                    "status": best_match.status,
                    "source_gap": best_match.source_gap,
                    "created_by": best_match.created_by,
                    "match_score": round(best_score, 2),
                }
    
    return matches


@app.put("/api/v1/catalogs/{repo_id}/requirements")
async def update_proposal_requirements(repo_id: str, request: ProposalRequirementsUpdate):
    """Update requirements for a proposed catalog entry."""
    import time
    from urllib.parse import unquote
    from codemind.storage.database import CatalogStore
    
    repo_id = unquote(repo_id)
    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db
    
    with db_inst.get_session() as session:
        entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Catalog entry not found")
        if getattr(entry, 'status', 'active') == "active":
            raise HTTPException(status_code=400, detail="Cannot update requirements for active catalog entries")
        
        entry.requirements = request.requirements
        entry.updated_at = int(time.time())
        
        # Also update content JSON with requirements
        import json
        try:
            content = json.loads(entry.content) if isinstance(entry.content, str) else entry.content
            content["requirements"] = request.requirements
            entry.content = json.dumps(content)
        except:
            pass
        
        session.commit()
    
    return {"repo_id": repo_id, "status": "updated", "requirements": request.requirements}
@app.post("/api/v1/catalogs/{repo_id}/contribute")
async def contribute_to_proposal(repo_id: str, request: Request):
    """Record a contributor's UID and Org against an existing proposed catalog entry."""
    import time
    import json
    from codemind.storage.database import CatalogStore
    
    body = await request.json()
    uid = body.get("uid", "").strip()
    org = body.get("org", "").strip()
    
    if not uid:
        raise HTTPException(status_code=400, detail="uid is required")
    
    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db
    
    with db_inst.get_session() as session:
        entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Proposal not found")
        
        # Append to contributors list in metadata
        meta = dict(entry.metadata_json or {})  # copy to ensure new object
        contributors = list(meta.get("contributors", []))
        # Avoid duplicate contributor
        already = any(c.get("uid") == uid for c in contributors)
        if not already:
            contributors.append({
                "uid": uid,
                "org": org,
                "contributed_at": int(time.time()),
            })
            meta["contributors"] = contributors
            entry.metadata_json = meta
            entry.updated_at = int(time.time())
            # Force SQLAlchemy to detect JSON column change
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(entry, "metadata_json")
            session.commit()
    
    return {"repo_id": repo_id, "status": "contributed", "uid": uid, "org": org}


@app.put("/api/v1/catalogs/{repo_id}/promote")
async def promote_proposal(repo_id: str, request: PromoteRequest):
    """Promote a proposed catalog entry by adding Git info and optionally qualifying it."""
    import time
    from urllib.parse import unquote
    from codemind.storage.database import CatalogStore
    
    repo_id = unquote(repo_id)
    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db
    
    with db_inst.get_session() as session:
        entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Catalog entry not found")
        
        entry.git_url = request.git_url
        entry.git_branch = request.git_branch or "main"
        entry.updated_at = int(time.time())
        
        if request.quality_score is not None:
            entry.quality_score = request.quality_score
        
        # Determine new status based on quality
        quality = entry.quality_score or 0
        if quality >= 60 and request.git_url:
            entry.status = "active"
        elif request.git_url:
            entry.status = "qualified"
        
        # Update metadata with git info
        import json
        try:
            meta = entry.metadata_json if isinstance(entry.metadata_json, dict) else json.loads(entry.metadata_json or "{}")
            meta["repo_url"] = request.git_url
            meta["branch"] = request.git_branch
            entry.metadata_json = meta
        except:
            pass
        
        new_status = entry.status
        session.commit()
    
    return {
        "repo_id": repo_id,
        "status": new_status,
        "git_url": request.git_url,
        "git_branch": request.git_branch,
        "quality_score": request.quality_score,
    }


@app.delete("/api/v1/catalogs/{repo_id}")
async def delete_catalog_entry(repo_id: str):
    """Delete a proposed catalog entry."""
    from codemind.storage.database import CatalogStore
    db = app.state.job_manager.db
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    
    with db.get_session() as session:
        entry = session.query(CatalogStore).filter(
            CatalogStore.repo_id == repo_id
        ).first()
        
        if not entry:
            raise HTTPException(status_code=404, detail=f"Catalog entry '{repo_id}' not found")
        
        if entry.status not in ("proposed", "qualified"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete catalog with status '{entry.status}'. Only proposed/qualified entries can be deleted."
            )
        
        entry_name = entry.repo_name or entry.repo_id
        session.delete(entry)
        session.commit()
    
    return {"message": f"Catalog entry '{entry_name}' deleted successfully", "repo_id": repo_id}

# ---------------------------------------------------------------------------
# Authentication & SSO Endpoints
# ---------------------------------------------------------------------------

class SSOLoginRequest(BaseModel):
    """Mock request for enterprise SSO login."""
    id_token: str  # In reality, this would be an OIDC ID Token from the provider

@app.post("/api/v1/auth/sso-login")
async def sso_login(req: SSOLoginRequest):
    """
    Simulated Enterprise SSO Callback.
    In a real app, this would validate the OIDC id_token against the provider (Azure/Okta).
    """
    # Simulate decoding a valid ID token from an enterprise provider
    try:
        # Static mock payload for demo/dev purposes
        # In production: payload = oidc_provider.verify_id_token(req.id_token)
        import json
        payload = json.loads(req.id_token)
        
        db = app.state.job_manager.db
        with db.get_session() as session:
            user = sync_user_to_db(payload, session)
            
            # Create our own application JWT
            access_token = create_access_token(
                data={"sub": user.user_id, "email": user.email, "name": user.full_name, "role": user.role, "dept": user.department}
            )
            
            return {
                "access_token": access_token, 
                "token_type": "bearer",
                "user": {
                    "user_id": user.user_id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "role": user.role
                }
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid SSO token: {str(e)}")

@app.get("/api/v1/auth/me")
async def get_my_profile(user: dict = Depends(require_user)):
    """Fetch current user profile from JWT."""
    return user

@app.post("/api/v1/catalogs")
async def create_catalog_entry(
    request: CatalogCreateRequest, 
    user: dict = Depends(require_user)
):
    """
    Execute a playbook on a repo and store the result in the catalog.
    """
    from codemind.storage.database import CatalogStore
    import json
    
    db = app.state.job_manager.db
    with db.get_session() as session:
        # Check if already exists
        entry = session.query(CatalogStore).filter_by(repo_id=request.repo_id).first()
        if entry:
             # Update metadata
             entry.created_by_user_id = user["user_id"]
             entry.created_by = user["full_name"]
             session.commit()
    
    # ... trigger the job ...
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
                    # Fallback: try to parse result from raw LLM output
                    import re
                    import json
                    try:
                        # Extract the fields we care about from the LLM's raw response
                        # We look for a JSON block (markdown or raw)
                        match = re.search(r'```json\s*({.*?})\s*```', execution_result, re.DOTALL)
                        if not match:
                             match = re.search(r'({[\s\S]*})', execution_result)
                        
                        if match:
                            raw_data = json.loads(match.group(1))
                            # If it's a tool-wrapped JSON, unwrap it
                            if "params" in raw_data:
                                full_catalog_data = raw_data["params"]
                            elif "data" in raw_data and "tool_name" in raw_data:
                                full_catalog_data = raw_data["data"]
                            else:
                                full_catalog_data = raw_data
                            
                            catalog_status = "unverified_llm_response"
                        else:
                            catalog_status = "tool_failed_no_entry"
                            full_catalog_data = {"error": "Catalog entry not found in storage after playbook execution."}
                    except:
                        catalog_status = "tool_failed_no_entry"
                        full_catalog_data = {"error": "Catalog entry not found and failed to parse LLM response"}
    except Exception as e:
        print(f"[ERROR] Failed to fetch catalog entry: {e}")
        full_catalog_data = {"error": f"Failed to fetch entry: {str(e)}", "partial_result": execution_result}

    return {
        "status": catalog_status,
        "repo_id": request.repo_id,
        "catalog_entry": full_catalog_data,
        "llm_response": execution_result # Keep the original text response just in case
    }

def _format_catalog_results(raw_results: list[dict]) -> list[dict]:
    """Transform internal catalog results into structured API response format."""
    import json
    formatted = []
    for item in raw_results:
        # Parse the metadata
        metadata = {}
        meta_str = item.get("metadata", "{}")
        if isinstance(meta_str, str):
            try:
                metadata = json.loads(meta_str)
            except:
                metadata = {}
        elif isinstance(meta_str, dict):
            metadata = meta_str
        
        entry = {
            "repo_id": item.get("repo_id", ""),
            "repo_name": item.get("repo_name", metadata.get("repo_name", "")),
            "score": round(item.get("score", 0.0), 4),
            "category": metadata.get("category", ""),
            "description": metadata.get("summary_high_level", ""),
            "summary_detailed": "", 
            "architecture": metadata.get("architecture", ""),
            "tech_stack": metadata.get("tech_stack", ""),
            "topics": metadata.get("topics", []),
            "quality_score": metadata.get("quality_score", 0),
            "specification": metadata.get("specification", ""),
            "pros": metadata.get("pros", []),
            "cons": metadata.get("cons", []),
            "repo_url": metadata.get("repo_url", ""),
            "branch": metadata.get("branch", ""),
            "org": metadata.get("org", ""),
            "estimated_cost": metadata.get("estimated_cost", 0),
            "business_functionalities": metadata.get("business_functionalities", []),
            "status": item.get("status", metadata.get("status", "active")),
            "source_gap": metadata.get("source_gap", ""),
            # Defaults for shared metrics
            "likes_count": 0,
            "popularity_points": 0,
            "search_count": 0,
            "view_count": 0
        }

        # Try to get full content from the chunk_text (which has the detailed summary)
        chunk_text = item.get("chunk_text", "")
        if chunk_text:
            lines = chunk_text.split("\n")
            for i, line in enumerate(lines):
                if "ARCHITECTURE & ANALYSIS:" in line and i + 1 < len(lines):
                    entry["summary_detailed"] = lines[i+1].strip()
                elif "DESCRIPTION:" in line and i + 1 < len(lines):
                    entry["description"] = lines[i+1].strip()

        # Coerce None values to safe defaults
        for k, v in entry.items():
            if v is None:
                if isinstance(v, list): entry[k] = []
                elif isinstance(v, int): entry[k] = 0
                else: entry[k] = ""

        formatted.append(entry)

    # Fetch latest shared metrics from SQLite (source of truth)
    try:
        from codemind.storage.database import CatalogStore
        db = app.state.job_manager.db
        repo_ids = [e["repo_id"] for e in formatted if e["repo_id"]]
        if db and repo_ids:
            with db.get_session() as session:
                db_stats = session.query(
                    CatalogStore.repo_id,
                    CatalogStore.likes_count,
                    CatalogStore.popularity_points,
                    CatalogStore.search_count,
                    CatalogStore.view_count
                ).filter(CatalogStore.repo_id.in_(repo_ids)).all()
                
                stats_map = {
                    s.repo_id: {
                        "likes_count": s.likes_count or 0,
                        "popularity_points": s.popularity_points or 0,
                        "search_count": s.search_count or 0,
                        "view_count": s.view_count or 0
                    } for s in db_stats
                }
                
                for e in formatted:
                    stats = stats_map.get(e["repo_id"])
                    if stats:
                        e.update(stats)
    except Exception as e:
        print(f"[SERVER] Error fetching shared metrics: {e}")

    return formatted


@app.get("/api/v1/catalogs/search")
async def search_catalog(
    query: str, 
    repo_id: str | None = None, 
    limit: int = 20, 
    min_score: float = 0.5,
    user: dict = Depends(get_current_user)
):
    """Semantic search over catalog entries."""
    from codemind.api.autonomous_agents import playbook_executor
    
    if not playbook_executor:
         raise HTTPException(status_code=503, detail="Playbook system not initialized")
    
    params = {
        "query": query,
        "repo_id": repo_id,
        "limit": limit,
        "min_score": min_score,
        "user_id": user["user_id"] if user else None
    }
    
    result = await playbook_executor.tools.search_catalogs(params)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Search failed"))
        
    return _format_catalog_results(result.get("results", []))


@app.post("/api/v1/catalogs/search")
async def search_catalog_post(
    request: CatalogSearchRequest,
    user: dict = Depends(get_current_user)
):
    """Semantic search over catalog entries (POST version)."""
    from codemind.api.autonomous_agents import playbook_executor
    
    if not playbook_executor:
         raise HTTPException(status_code=503, detail="Playbook system not initialized")
    
    params = {
        "query": request.query,
        "repo_id": request.repo_id,
        "limit": request.limit,
        "min_score": request.min_score,
        "user_id": user["user_id"] if user else None
    }
    
    result = await playbook_executor.tools.search_catalogs(params)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Search failed"))
        
    return _format_catalog_results(result.get("results", []))



@app.get("/api/v1/catalogs/trending")
async def get_trending_catalogs(
    sort_by: str = "popularity_points",
    limit: int = 10,
    user: dict = Depends(get_current_user)
):
    """Get trending / most popular catalog entries.
    
    Args:
        sort_by: One of 'popularity_points', 'search_count', 'view_count'.
        limit: Max items to return (default 10).
    """
    from codemind.storage.database import CatalogStore
    import json as _json
    
    ALLOWED_SORT = {"popularity_points", "search_count", "view_count"}
    if sort_by not in ALLOWED_SORT:
        raise HTTPException(status_code=400, detail=f"sort_by must be one of {ALLOWED_SORT}")
    
    db = app.state.job_manager.db
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    sort_col = getattr(CatalogStore, sort_by)
    
    results = []
    with db.get_session() as session:
        from sqlalchemy import or_
        user_id = user["user_id"] if user else None
        
        entries = (
            session.query(CatalogStore)
            .filter(or_(
                CatalogStore.status == "active",
                CatalogStore.created_by_user_id == user_id
            ))
            .order_by(sort_col.desc())
            .limit(limit)
            .all()
        )
        for entry in entries:
            meta = {}
            if entry.metadata_json:
                meta = entry.metadata_json if isinstance(entry.metadata_json, dict) else _json.loads(entry.metadata_json)
            
            raw_ts = meta.get("tech_stack", "")
            tech_stack = ", ".join(raw_ts) if isinstance(raw_ts, list) else str(raw_ts) if raw_ts else ""
            
            results.append({
                "repo_id": entry.repo_id,
                "repo_name": entry.repo_name or entry.repo_id,
                "org": entry.org or "",
                "category": meta.get("category", ""),
                "description": meta.get("summary_high_level", "")[:200],
                "summary_detailed": meta.get("summary_detailed", "") or meta.get("description", ""),
                "architecture": meta.get("architecture", ""),
                "tech_stack": tech_stack,
                "score": 1.0, # Trending/Popular view isn't semantic search, default to 100%
                "quality_score": entry.quality_score or meta.get("quality_score", 0),
                "search_count": entry.search_count or 0,
                "view_count": entry.view_count or 0,
                "popularity_points": entry.popularity_points or 0,
                "likes_count": entry.likes_count or 0,
                "repo_url": entry.git_url or meta.get("repo_url", ""),
                "topics": meta.get("topics", []),
                "specification": meta.get("specification", ""),
                "pros": meta.get("pros", []),
                "cons": meta.get("cons", []),
                "estimated_cost": entry.estimated_cost if hasattr(entry, 'estimated_cost') else meta.get("estimated_cost", 0),
                "business_functionalities": meta.get("business_functionalities", []),
            })
    
    return results


@app.get("/api/v1/catalogs/{repo_id}")
async def get_catalog_entries(repo_id: str):
    """Get catalog entries for a repository."""
    manifest: ManifestManager = app.state.manifest
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


@app.post("/api/v1/catalogs/{repo_id}/interact")
async def track_catalog_interaction(repo_id: str):
    """Record a user view/click on a catalog item.
    
    Increments view_count by 1 and popularity_points by 5.
    Used by the frontend when a user opens a catalog detail view.
    """
    from codemind.storage.database import CatalogStore
    
    db = app.state.job_manager.db
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    with db.get_session() as session:
        entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Catalog entry not found")
        
        entry.view_count = (entry.view_count or 0) + 1
        entry.popularity_points = (entry.popularity_points or 0) + 5
        session.commit()
    
    return {"status": "ok", "repo_id": repo_id}


@app.post("/api/v1/catalogs/{repo_id}/like")
async def like_catalog(repo_id: str, user: dict = Depends(require_user)):
    """Record an explicit like on a catalog item.
    
    Increments likes_count by 1 and popularity_points by 10.
    Frontend can call this when a user clicks the like button.
    """
    from codemind.storage.database import CatalogStore

    db = app.state.job_manager.db
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")

    with db.get_session() as session:
        entry = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Catalog entry not found")

        entry.likes_count = (entry.likes_count or 0) + 1
        entry.popularity_points = (entry.popularity_points or 0) + 10
        # Audit: Track user who liked
        entry.created_by_user_id = user["user_id"] 
        session.commit()

        likes = entry.likes_count or 0
        popularity = entry.popularity_points or 0

    return {
        "status": "ok",
        "repo_id": repo_id,
        "likes_count": likes,
        "popularity_points": popularity,
    }


# ---------------------------------------------------------------------------
# Git Integration Endpoints (multi-platform search & branch listing)
# ---------------------------------------------------------------------------

@app.get("/api/v1/git/search")
async def search_git_repositories(
    keywords: str,
    endpoint: str = "github.com",
    limit: int = 20,
):
    """Search repositories across Git platforms.

    Args:
        keywords: Comma-separated search terms (e.g. "fastapi,auth")
        endpoint: Git host — github.com, gitlab.com, bitbucket.org, or enterprise hostname
        limit: Max results (default 20)
    """
    from codemind.utils.git_utils import GitIntegration

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not kw_list:
        raise HTTPException(status_code=400, detail="keywords parameter is required")

    # Token resolution handled internally by GitIntegration (GitSaaS → static env vars)
    git = GitIntegration()

    try:
        results = git.search_repositories(kw_list, git_endpoint=endpoint, limit=limit)
        return {"results": results, "total": len(results)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


@app.get("/api/v1/git/branches")
async def list_git_branches(
    owner: str,
    name: str,
    endpoint: str = "github.com",
):
    """List branches for a repository.

    Args:
        owner: Repository owner/org
        name: Repository name
        endpoint: Git host
    """
    from codemind.utils.git_utils import GitIntegration

    # Token resolution handled internally by GitIntegration (GitSaaS → static env vars)
    git = GitIntegration()

    try:
        branches = git.list_branches(owner, name, git_endpoint=endpoint)
        return {"branches": branches, "total": len(branches)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Branch listing failed: {e}")
