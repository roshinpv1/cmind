"""
FastAPI control plane for CodeMind.

REST API for indexing, search, and system management.
"""

from dotenv import load_dotenv
load_dotenv()  # Load .env before anything reads os.environ

from contextlib import asynccontextmanager
import os

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
    from codemind.graph import SQLiteGraphAdapter
    from codemind.graph.graph_query import GraphQueryService

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
        org=request.org,
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
    last_pr_merged_at: str | None = None


@app.get("/api/v1/repos/{repo_id:path}", response_model=RepoDetail)
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


@app.put("/api/v1/repos/{repo_id:path}")
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
    min_score: float = 0.8


@app.get("/api/v1/catalogs/list")
async def list_catalog_entries():
    """List all catalog entries."""
    from codemind.storage.database import CatalogStore
    import json as _json
    
    manifest: ManifestManager = app.state.manifest
    db_inst = manifest.db
    
    results = []
    with db_inst.get_session() as session:
        entries = session.query(CatalogStore).order_by(CatalogStore.updated_at.desc()).all()
        for entry in entries:
            meta = {}
            if entry.metadata_json:
                meta = entry.metadata_json if isinstance(entry.metadata_json, dict) else _json.loads(entry.metadata_json)
            
            results.append({
                "repo_id": entry.repo_id,
                "repo_name": entry.repo_name or entry.repo_id,
                "org": entry.org or "",
                "description": meta.get("summary_high_level", "")[:200],
                "tech_stack": meta.get("tech_stack", ""),
                "category": meta.get("category", ""),
                "quality_score": meta.get("quality_score", 0),
                "topics": meta.get("topics", []),
                "repo_url": meta.get("repo_url", ""),
                "branch": meta.get("branch", ""),
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            })
    
    return results


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
    """Transform internal catalog results into structured API response format.
    
    The internal format uses a flat text blob in 'chunk_text' for LLM consumption.
    This function unpacks the metadata JSON and returns clean structured fields.
    """
    import json
    formatted = []
    for item in raw_results:
        # Parse the metadata JSON string
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
            "summary_detailed": "",  # Will be populated from content below
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
            "business_functionalities": metadata.get("business_functionalities", [])
        }
        
        # Coerce None values to safe defaults (metadata.get returns None when key exists but value is None)
        for k in ["repo_id", "repo_name", "category", "description", "summary_detailed", 
                   "architecture", "tech_stack", "specification", "repo_url", "branch", "org"]:
            if entry[k] is None:
                entry[k] = ""
        for k in ["topics", "pros", "cons", "business_functionalities"]:
            if entry[k] is None:
                entry[k] = []
        if entry["quality_score"] is None:
            entry["quality_score"] = 0
        if entry["estimated_cost"] is None:
            entry["estimated_cost"] = 0
        
        # Try to get full content from the chunk_text (which has the detailed summary)
        chunk_text = item.get("chunk_text", "")
        if chunk_text:
            # Extract detailed summary from the text blob
            for line in chunk_text.split("\n"):
                if line.startswith("Detailed Summary: "):
                    entry["summary_detailed"] = line.replace("Detailed Summary: ", "").strip()
                elif line.startswith("Description: ") and not entry["description"]:
                    entry["description"] = line.replace("Description: ", "").strip()
        
        formatted.append(entry)
    return formatted


@app.get("/api/v1/catalogs/search")
async def search_catalog_get(
    query: str,
    repo_id: str | None = None,
    limit: int = 5,
    min_score: float = 0.0
):
    """Semantic search over catalog entries (GET).
    
    Returns structured catalog entries with fields like repo_name, description,
    architecture, tech_stack, topics, quality_score, pros, cons, etc.
    """
    from codemind.api.autonomous_agents import playbook_executor
    
    if not playbook_executor:
         raise HTTPException(status_code=503, detail="Playbook system not initialized")
    
    params = {
        "query": query,
        "repo_id": repo_id,
        "limit": limit,
        "min_score": min_score
    }
    
    result = await playbook_executor.tools.search_catalogs(params)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Search failed"))
        
    return _format_catalog_results(result.get("results", []))


@app.post("/api/v1/catalogs/search")
async def search_catalog_post(request: CatalogSearchRequest):
    """Semantic search over catalog entries (POST).
    
    Returns structured catalog entries with fields like repo_name, description,
    architecture, tech_stack, topics, quality_score, pros, cons, etc.
    """
    from codemind.api.autonomous_agents import playbook_executor
    
    if not playbook_executor:
         raise HTTPException(status_code=503, detail="Playbook system not initialized")
    
    params = {
        "query": request.query,
        "repo_id": request.repo_id,
        "limit": request.limit,
        "min_score": request.min_score
    }
    
    result = await playbook_executor.tools.search_catalogs(params)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Search failed"))
        
    return _format_catalog_results(result.get("results", []))


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
