"""
LangGraph workflow orchestration for indexing pipeline.

Defines state and workflow nodes for end-to-end indexing.
"""

from dataclasses import dataclass, field
from pathlib import Path

from codemind.graph import GraphBuilder
from codemind.indexer import ChangeDetector
from codemind.indexer.ast_extractor import ASTExtractor
from codemind.indexer.ast_chunker import ASTChunker
from codemind.indexer.call_extractor import CallExtractor
from codemind.indexer.embedder import EmbeddingGenerator
from codemind.indexer.file_filters import CODE_EXTENSIONS, KNOWN_FILENAMES
from codemind.indexer.models import FileChange
from codemind.storage import ManifestManager
from codemind.storage.bm25_storage import BM25Storage
from codemind.storage.lancedb_storage import LanceDBStorage
from codemind.graphify.extract import extract as graphify_extract


@dataclass
class IndexingState:
    """State for indexing workflow."""

    repo_path: str
    repo_id: str
    job_id: str
    branch: str = "main"
    stage: str = "init"
    error: str | None = None
    files_changed: int = 0
    chunks_created: int = 0
    embeddings_generated: int = 0
    symbols_extracted: int = 0
    changed_files: list[FileChange] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    commit_hash: str | None = None  # Git commit hash if using git detection
    metadata: dict = field(default_factory=dict)
    org: str | None = None  # Organization owning this component
    user_id: str | None = None  # User who initiated the indexing
    repo_url: str | None = None  # Git remote URL (when cloned)
    cd_repo_url: str | None = None  # Companion CD repo URL (if found)
    graphify_data: dict = field(default_factory=dict) # Unified extraction results


class IndexingWorkflow:
    """LangGraph-style workflow for code indexing."""

    def __init__(
        self,
        manifest_manager: ManifestManager,
        lance_storage: LanceDBStorage,
        graph_db,
        progress_callback=None,
    ):
        """Initialize workflow.
        
        Args:
            progress_callback: Optional callable(stage: str, progress: int)
                               called after each workflow step to report progress.
        """
        self.manifest = manifest_manager
        self.graph = graph_db
        self._progress_callback = progress_callback

        # Initialize components
        self.ast_extractor = ASTExtractor()
        self.chunker = ASTChunker()  # AST-aware chunking (falls back to char-based)
        self.call_extractor = CallExtractor()
        self.embedder = EmbeddingGenerator()
        
        # Pass embedding dimension to storage (ensures schema matches model)
        self.storage = LanceDBStorage(
            db_path=lance_storage.db_path,
            embedding_dim=self.embedder.embedding_dim
        )
        self.bm25 = BM25Storage()
        self.graph_builder = GraphBuilder(graph_db)

    def _report_progress(self, stage: str, progress: int):
        """Report progress via callback if registered."""
        if self._progress_callback:
            try:
                self._progress_callback(stage, progress)
            except Exception as e:
                print(f"[WORKFLOW] ⚠️ Progress callback error: {e}")

    def run(self, state: IndexingState) -> IndexingState:
        """Execute full indexing workflow."""
        self._ast_cache = {}  # Initialize AST cache for this run
        try:
            print(f"[WORKFLOW] ========== STARTING INDEXING WORKFLOW ==========")
            print(f"[WORKFLOW] Repo: {state.repo_id}")
            
            # Create index run record
            try:
                self.manifest.create_index_run(
                    run_id=state.job_id,
                    repo_id=state.repo_id,
                    branch=getattr(state, 'branch', None),
                    commit_sha=state.commit_hash,
                )
            except Exception as e:
                print(f"[WORKFLOW] ⚠️ Failed to create index run: {e}")
            
            self._report_progress("detecting_changes", 5)
            state = self.detect_changes(state)
            if state.error:
                print(f"[WORKFLOW] ❌ detect_changes failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ detect_changes complete — {state.files_changed} changed files")
            self._report_progress("detecting_changes", 10)

            # AST extraction now consolidated into discovery pass
            self._report_progress("discovery", 15)

            self._report_progress("chunking", 25)
            state = self.chunk_and_embed_files(state)
            if state.error:
                print(f"[WORKFLOW] ❌ chunk_and_embed_files failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ chunk_and_embed_files complete — {state.chunks_created} chunks, {state.embeddings_generated} embeddings")
            self._report_progress("embedding", 65)

            self._report_progress("discovery", 70)
            state = self.run_discovery(state)
            if state.error:
                print(f"[WORKFLOW] ❌ discovery failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ discovery complete")
            self._report_progress("discovery", 90)

            self._report_progress("updating_manifest", 95)
            state = self.update_manifest(state)
            if state.error and "Manifest update failed" in state.error:
                # Manifest update is non-fatal — embeddings and graph are already persisted.
                # Log the error but let the job complete successfully.
                print(f"[WORKFLOW] ⚠️ Manifest update had errors (non-fatal): {state.error}")
                state.error = None  # Clear so job isn't marked FAILED
            state.stage = "completed"
            self._report_progress("completed", 100)
            self._ast_cache.clear()  # Free memory

            # Complete index run with stats
            try:
                self.manifest.complete_index_run(
                    run_id=state.job_id,
                    status="completed",
                    files_indexed=state.files_changed,
                    symbols_extracted=getattr(state, 'symbols_extracted', 0),
                    chunks_created=state.chunks_created,
                    embeddings_generated=state.embeddings_generated,
                )
            except Exception as e:
                print(f"[WORKFLOW] ⚠️ Failed to complete index run: {e}")

        except Exception as e:
            state.error = str(e)
            state.stage = "failed"
            if hasattr(self, "_ast_cache"):
                self._ast_cache.clear()
            # Record failed index run
            try:
                self.manifest.complete_index_run(
                    run_id=state.job_id,
                    status="failed",
                    error=str(e),
                    files_indexed=state.files_changed,
                )
            except Exception:
                pass

        return state

    def detect_changes(self, state: IndexingState) -> IndexingState:
        """Node: Detect changed files."""
        try:
            state.stage = "detecting_changes"

            detector = ChangeDetector(state.repo_path)
            branch = getattr(state, "branch", "main")
            repo = self.manifest.get_repository(state.repo_path, branch=branch)

            if repo:
                last_commit = repo.last_commit_hash
                changes = detector.detect_changes(last_commit)
            else:
                changes = detector.detect_changes()

            state.files_changed = len(changes.changed_files)
            state.changed_files = changes.changed_files
            state.deleted_files = changes.deleted_files
            state.commit_hash = changes.commit_hash  # Store git commit if available
            
            # --- PURGE OLD DATA FOR DELTA INDEXING ---
            # Compile list of all file paths that were modified or deleted
            files_to_purge = [f.path for f in state.changed_files] + state.deleted_files
            if files_to_purge:
                print(f"[WORKFLOW] Purging old indexed data for {len(files_to_purge)} modified/deleted files...")
                try:
                    self.storage.delete_chunks_by_file(state.repo_id, files_to_purge)
                    self.bm25.delete_by_files(state.repo_id, files_to_purge)
                    if getattr(self, "graph", None):
                        self.graph.delete_file_nodes(state.repo_id, files_to_purge)
                except Exception as purge_err:
                    print(f"[WORKFLOW] ⚠️ Non-fatal error purging old data: {purge_err}")
            # -----------------------------------------
            
            # Extract metadata if it's a git repo
            if changes.detection_method == "git":
                try:
                    from codemind.utils.git_utils import GitRepoManager
                    mgr = GitRepoManager()
                    state.metadata = mgr.extract_metadata(Path(state.repo_path))
                    print(f"[WORKFLOW] Extracted metadata: {list(state.metadata.keys())}")
                except Exception as e:
                    print(f"[WORKFLOW] ⚠️ Metadata extraction failed: {e}")
                    state.metadata = {}
            else:
                state.metadata = {}

        except Exception as e:
            state.error = f"Change detection failed: {e}"

        return state

    # Legacy extract_ast consolidated into run_discovery

    def chunk_and_embed_files(self, state: IndexingState) -> IndexingState:
        """Node: Chunk files and generate embeddings in streams to limit memory."""
        import os
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        PER_FILE_TIMEOUT = 30  # seconds — skip files that take longer
        MAX_FILE_SIZE = 500 * 1024  # 500 KB
        MAX_WORKERS = min(os.cpu_count() or 4, 8)  # Cap at 8 to avoid over-subscribing
        FILE_BATCH_SIZE = 250  # Process 250 files at a time

        try:
            state.stage = "chunking_and_embedding"
            total = len(state.changed_files)
            print(f"[STREAM] Starting streamed chunking & embedding for {total} files...")
            
            # --- Diagnostic for Zero-Vector Mode ---
            if getattr(self.embedder.provider, "__class__", None).__name__ == "NoneEmbeddingProvider":
                print(f"[STREAM] ⚡ Zero-Vector Mode active: skipping actual embedding generation.")
            # ----------------------------------------

            # Pre-filter files
            chunkable = []
            skipped = 0
            for file_change in state.changed_files:
                file_path = Path(state.repo_path) / file_change.path
                if not file_path.exists():
                    continue
                if file_path.suffix.lower() not in CODE_EXTENSIONS and file_path.name not in KNOWN_FILENAMES:
                    continue
                try:
                    if file_path.stat().st_size > MAX_FILE_SIZE:
                        skipped += 1
                        continue
                except OSError:
                    pass
                chunkable.append((file_change, file_path))

            print(f"[STREAM] Chunkable files: {len(chunkable)} (skipped {skipped} large)")

            # Get existing chunks from LanceDB to avoid re-embedding
            existing_hashes = set()
            try:
                # Optimized DB projection to only fetch 1 string per chunk instead of 3MB objects
                existing_hashes = self.storage.get_chunk_hashes(state.repo_id)
            except Exception as e:
                print(f"[STREAM] ⚠️ Could not fetch existing hashes: {e}")

            total_chunks_created = 0
            total_embeddings_generated = 0
            errored = 0
            timed_out = 0

            # Process in micro-batches
            for i in range(0, len(chunkable), FILE_BATCH_SIZE):
                file_batch = chunkable[i:i + FILE_BATCH_SIZE]
                batch_chunks = []

                # Chunk this specific mini-batch in parallel
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    futures = {}
                    for file_change, file_path in file_batch:
                        future = pool.submit(self.chunker.chunk_file, str(file_path))
                        futures[future] = file_change

                    from concurrent.futures import as_completed
                    for future in as_completed(futures, timeout=PER_FILE_TIMEOUT * len(futures) + 60):
                        file_change = futures[future]
                        try:
                            res = future.result(timeout=PER_FILE_TIMEOUT)
                            if res:
                                batch_chunks.extend(res)
                        except FuturesTimeout:
                            timed_out += 1
                        except Exception as e:
                            errored += 1

                total_chunks_created += len(batch_chunks)
                
                # Directly embed and dump to DB, allowing cyclic reclamation of the array buffer
                if batch_chunks:
                    try:
                        if self.embedder.provider_type == "none":
                            bm25_rows = []
                            for chunk in batch_chunks:
                                bm25_rows.append(
                                    {
                                        "chunk_hash": chunk.chunk_hash,
                                        "file_path": chunk.file_path,
                                        "start_line": chunk.start_line,
                                        "end_line": chunk.end_line,
                                        "language": getattr(chunk, "language", "") or "",
                                        "symbol_name": getattr(chunk, "symbol_name", "") or "",
                                        "chunk_text": chunk.text,
                                    }
                                )
                            self.bm25.upsert_chunks(state.repo_id, bm25_rows)
                        else:
                            new_with_emb = self.embedder.generate_embeddings(batch_chunks, existing_hashes)
                            if new_with_emb:
                                self.storage.append_chunks(state.repo_id, new_with_emb)
                                bm25_rows = []
                                for chunk, _emb in new_with_emb:
                                    bm25_rows.append(
                                        {
                                            "chunk_hash": chunk.chunk_hash,
                                            "file_path": chunk.file_path,
                                            "start_line": chunk.start_line,
                                            "end_line": chunk.end_line,
                                            "language": getattr(chunk, "language", "") or "",
                                            "symbol_name": getattr(chunk, "symbol_name", "") or "",
                                            "chunk_text": chunk.text,
                                        }
                                    )
                                self.bm25.upsert_chunks(state.repo_id, bm25_rows)
                                total_embeddings_generated += len(new_with_emb)
                    except Exception as e:
                        print(f"[STREAM] ❌ Error embedding batch: {type(e).__name__}: {e}")
                
                # Nuke variables to avoid memory hoarding
                del batch_chunks
                
                pct = int((i + len(file_batch)) / max(len(chunkable), 1) * 100)
                print(f"[STREAM] Progress: {i + len(file_batch)}/{len(chunkable)} files ({pct}%) -> {total_chunks_created} total chunks queued, {total_embeddings_generated} records dumped.")

            state.chunks_created = total_chunks_created
            state.embeddings_generated = total_embeddings_generated

        except Exception as e:
            print(f"[STREAM] ❌ Global Error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            state.error = f"Chunk/Embed pipeline collapse: {e}"

        return state

    def run_discovery(self, state: IndexingState) -> IndexingState:
        """Unified discovery phase using Graphify."""
        try:
            state.stage = "discovery"
            repo_path = Path(state.repo_path)
            
            # Map changed files to absolute paths
            paths_to_process = []
            for fc in state.changed_files:
                p = repo_path / fc.path
                if p.exists():
                    paths_to_process.append(p)
            
            if not paths_to_process:
                print(f"[DISCOVERY] No new files to discover.")
                state.graphify_data = {"nodes": [], "edges": []}
                return state

            print(f"[DISCOVERY] Running Graphify extraction on {len(paths_to_process)} files...")

            # Pass the repo_id dir as the cache root so the per-file cache lands at
            # {REPOS_PATH}/{repo_id}/graphify-out/cache/  — outside the clone.
            cache_root: Path | None = None
            if self.graph and hasattr(self.graph, "base_path"):
                cache_root = Path(self.graph.base_path) / state.repo_id

            result = graphify_extract(paths_to_process, cache_root=cache_root)
            state.graphify_data = result
            
            # Count extracted symbols roughly
            symbols_count = len([n for n in result.get("nodes", []) if n.get("type") in ("Function", "Method", "Class")])
            state.symbols_extracted = symbols_count
            print(f"[DISCOVERY] ✅ Extracted {len(result.get('nodes', []))} nodes and {len(result.get('edges', []))} edges.")

            # Update Graph Database directly
            if not self.graph_builder.is_noop:
                print(f"[DISCOVERY] Syncing results to graph database...")
                self.graph_builder.set_batch_mode(True)
                
                G = self.graph_builder.graph.get_graph(state.repo_id)
                
                # Add nodes
                for node in result.get("nodes", []):
                    # Standardize node properties for UI/Agent compatibility
                    props = {k: v for k, v in node.items() if k not in ("id", "label")}
                    G.add_node(node["id"], label=node["label"], **props)
                
                # Add edges
                for edge in result.get("edges", []):
                    src = edge.get("source")
                    tgt = edge.get("target")
                    rel = edge.get("relation")
                    if src and tgt and rel:
                        props = {k: v for k, v in edge.items() if k not in ("source", "target", "relation")}
                        G.add_edge(src, tgt, relation=rel, **props)

                self.graph_builder.commit(state.repo_id)
                self.graph_builder.set_batch_mode(False)
                print(f"[DISCOVERY] ✅ Graph database synchronized")

        except Exception as e:
            print(f"[DISCOVERY] ❌ Failed: {e}")
            import traceback
            traceback.print_exc()
            state.error = str(e)
            
        return state

    @classmethod
    def repair_manifest(cls, repo_path: str, repo_id: str, manifest: ManifestManager, lance_storage: LanceDBStorage, graph_db) -> dict:
        """Standalone repair: re-run only the manifest update for an existing repo.
        
        Use this to fix jobs that completed indexing but failed at manifest update.
        All expensive work (embeddings, graph) is already persisted — this just
        re-extracts symbols from source and updates the DB.
        
        Returns dict with status and details.
        """
        workflow = cls(manifest, lance_storage, graph_db)
        
        # Reconstruct minimal state from the repo on disk
        state = IndexingState(
            repo_path=repo_path,
            repo_id=repo_id,
            job_id="repair",
        )
        
        # Detect files to build changed_files list
        state = workflow.detect_changes(state)
        if state.error:
            return {"status": "error", "error": state.error}
        
        # Run only the manifest update
        state = workflow.update_manifest(state)
        if state.error:
            return {"status": "error", "error": state.error}
        
        return {
            "status": "repaired",
            "repo_id": repo_id,
            "files": state.files_changed,
            "symbols": state.symbols_extracted,
        }

    def update_manifest(self, state: IndexingState) -> IndexingState:
        """Node: Update manifest, persist symbols, and save commit snapshot.
        
        Structured as 3 independent phases so a failure in symbol persistence
        doesn't prevent the repo from appearing in the manifest.
        """
        state.stage = "updating_manifest"

        # ── Phase 1: Create/update repository in manifest (CRITICAL) ─────
        try:
            branch = getattr(state, "branch", "main")
            repo = self.manifest.get_repository(state.repo_path, branch=branch)
            
            metadata = getattr(state, "metadata", {})
            
            if not repo:
                self.manifest.create_repository(
                    state.repo_path, 
                    repo_id=state.repo_id,
                    branch=branch,
                    org=getattr(state, 'org', None),
                    user_id=getattr(state, 'user_id', None)
                )
                repo = self.manifest.get_repository(state.repo_path, branch=branch)
                print(f"[MANIFEST] ✅ Created repository entry for {state.repo_id}")
            
            # Update manifest with commit hash and metadata
            metadata = getattr(state, "metadata", {})
            if state.cd_repo_url:
                metadata["cd_repo_url"] = state.cd_repo_url
            
            # Count total files on disk for accurate total
            repo_dir = Path(state.repo_path)
            total_files_on_disk = sum(1 for f in repo_dir.rglob("*") if f.is_file() and ".git" not in f.parts)

            self.manifest.update_repository(
                state.repo_id,
                repo_url=state.repo_url,
                branch=branch,
                org=getattr(state, 'org', None),
                last_commit_hash=state.commit_hash,
                total_files=total_files_on_disk,
                metadata=metadata
            )
            print(f"[MANIFEST] ✅ Updated repository manifest ({total_files_on_disk} files)")

        except Exception as e:
            # This is a critical failure — repo won't appear in list
            state.error = f"Manifest update failed: {e}"
            print(f"[MANIFEST] ❌ Repository creation/update failed: {e}")
            return state

        # ── Phase 2: Save commit snapshot (OPTIONAL) ─────────────────────
        if state.commit_hash:
            try:
                self.manifest.save_commit_snapshot(
                    repo_id=state.repo_id,
                    commit_sha=state.commit_hash,
                    files_changed=state.files_changed,
                )
            except Exception as e:
                print(f"[MANIFEST] ⚠️ Commit snapshot failed: {e}")

        return state
