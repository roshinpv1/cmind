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
from codemind.indexer.import_resolver import ImportResolver
from codemind.indexer.models import FileChange
from codemind.storage import ManifestManager
from codemind.storage.lancedb_storage import LanceDBStorage


@dataclass
class IndexingState:
    """State for indexing workflow."""

    repo_path: str
    repo_id: str
    job_id: str
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

            self._report_progress("extracting_ast", 15)
            state = self.extract_ast(state)
            if state.error:
                print(f"[WORKFLOW] ❌ extract_ast failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ extract_ast complete")
            self._report_progress("extracting_ast", 20)

            self._report_progress("chunking", 25)
            state = self.chunk_and_embed_files(state)
            if state.error:
                print(f"[WORKFLOW] ❌ chunk_and_embed_files failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ chunk_and_embed_files complete — {state.chunks_created} chunks, {state.embeddings_generated} embeddings")
            self._report_progress("embedding", 65)

            self._report_progress("building_graph", 70)
            state = self.build_graph(state)
            if state.error:
                print(f"[WORKFLOW] ❌ build_graph failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ build_graph complete")
            self._report_progress("building_graph", 80)

            self._report_progress("extracting_relationships", 85)
            state = self.extract_relationships(state)
            if state.error:
                print(f"[WORKFLOW] ❌ extract_relationships failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ extract_relationships complete")
            self._report_progress("extracting_relationships", 90)

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

    def _get_ast(self, file_path: Path, language: str):
        """Helper to get AST with in-memory caching for the duration of the workflow."""
        path_str = str(file_path)
        if hasattr(self, "_ast_cache") and path_str in self._ast_cache:
            return self._ast_cache[path_str]
        
        result = self.ast_extractor.extract(file_path, language)
        if hasattr(self, "_ast_cache"):
            self._ast_cache[path_str] = result
        return result

    def detect_changes(self, state: IndexingState) -> IndexingState:
        """Node: Detect changed files."""
        try:
            state.stage = "detecting_changes"

            detector = ChangeDetector(state.repo_path)
            repo = self.manifest.get_repository(state.repo_path)

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
                    if getattr(self, "graph", None):
                        self.graph.delete_file_nodes(state.repo_id, files_to_purge)
                    if getattr(self, "manifest", None):
                        self.manifest.delete_symbols_by_file(state.repo_id, files_to_purge)
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

    def extract_ast(self, state: IndexingState) -> IndexingState:
        """Node: Extract AST from files."""
        try:
            state.stage = "extracting_ast"

            # Extract AST from each changed Python file
            # AST extraction happens during graph building phase
            # to avoid extracting twice

        except Exception as e:
            state.error = f"AST extraction failed: {e}"

        return state

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
                        new_with_emb = self.embedder.generate_embeddings(batch_chunks, existing_hashes)
                        if new_with_emb:
                            self.storage.append_chunks(state.repo_id, new_with_emb)
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

    def build_graph(self, state: IndexingState) -> IndexingState:
        """Node: Build code graph."""
        try:
            state.stage = "building_graph"
            print(f"[GRAPH] Starting graph building for repo: {state.repo_id}")

            # Build repository node
            print(f"[GRAPH] Creating repository node...")
            self.graph_builder.build_repository_node(state.repo_id, state.repo_path)
            print(f"[GRAPH] ✅ Repository node created")

            # Build file nodes and extract structure
            print(f"[GRAPH] Processing {len(state.changed_files)} changed files...")
            files_processed = 0
            
            import time

            for file_change in state.changed_files:
                file_path = Path(state.repo_path) / file_change.path

                # Add file node
                self.graph_builder.build_file_node(
                    state.repo_id, 
                    str(file_change.path),
                    str(file_change.path)  # relative_path
                )
                files_processed += 1

                # Extract and add classes/functions for ALL supported languages
                language = self.ast_extractor.detect_language(file_path)
                if language:
                    try:
                        # Cooperative internal timeout will handle stalls
                        result = self._get_ast(file_path, language)

                        if result and result.symbols:
                            # Add class nodes
                            for symbol in result.symbols:
                                if symbol.type in ("class", "interface", "struct", "trait", "enum"):
                                    self.graph_builder.build_class_node(
                                        state.repo_id,
                                        str(file_change.path),
                                        symbol.name,
                                    )

                            # Add function nodes
                            for symbol in result.symbols:
                                if symbol.type in ("function", "method"):
                                    parent_class = symbol.parent if symbol.type == "method" else None
                                    self.graph_builder.build_function_node(
                                        state.repo_id,
                                        str(file_change.path),
                                        symbol.name,
                                        parent_class,
                                    )
                    except TimeoutError:
                        print(f"[GRAPH] ⚠️  AST extraction timed out for {file_change.path}, skipping.")
                    except Exception as e:
                        print(f"[GRAPH] ⚠️  AST extraction failed for {file_change.path}: {e}")

            print(f"[GRAPH] ✅ Processed {files_processed} files")

        except Exception as e:
            print(f"[GRAPH] ❌ Graph building failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            state.error = f"Graph building failed: {e}"

        return state

    def extract_relationships(self, state: IndexingState) -> IndexingState:
        """Node: Extract cross-file relationships (IMPORTS, CALLS, INHERITS)."""
        try:
            state.stage = "extracting_relationships"
            repo_path = Path(state.repo_path)
            import_resolver = ImportResolver(repo_path)

            import_edges = 0
            call_edges = 0
            inherit_edges = 0

            # Build a symbol-to-file index for resolving cross-file calls
            symbol_file_map: dict[str, list[str]] = {}  # name → [file_paths]

            import time

            # 1. Extract AST and Imports
            for file_change in state.changed_files:
                file_path = repo_path / file_change.path
                if not file_path.exists():
                    continue

                language = self.ast_extractor.detect_language(file_path)
                if not language:
                    continue

                try:
                    # Cooperative internal timeout will handle stalls
                    result = self._get_ast(file_path, language)

                    if result and result.symbols:
                        # Index symbols
                        for sym in result.symbols:
                            if sym.name not in symbol_file_map:
                                symbol_file_map[sym.name] = []
                            symbol_file_map[sym.name].append(str(file_change.path))

                        # Build IMPORTS edges
                        for imp in result.imports:
                            # Skip resolving massive imports to avoid ImportResolver O(N) worst-case
                            if len(state.changed_files) > 1000 and len(imp.module) > 200:
                                continue
                                
                            resolved = import_resolver.resolve(
                                imp.module, language, source_file=file_path
                            )
                            if resolved:
                                self.graph_builder.build_import_edges(
                                    state.repo_id, str(file_change.path), resolved, imp.module
                                )
                                import_edges += 1

                        # Build INHERITS edges
                        for sym in result.symbols:
                            if sym.bases:
                                for base in sym.bases:
                                    # Find the file that declares the base class
                                    if base in symbol_file_map:
                                        for base_file in symbol_file_map[base]:
                                            if base_file != str(file_change.path):
                                                self.graph_builder.build_inheritance_edges(
                                                    state.repo_id,
                                                    str(file_change.path), sym.name,
                                                    base_file, base,
                                                )
                                                inherit_edges += 1

                except TimeoutError:
                    print(f"[REL] ⚠️  AST extraction timed out for {file_change.path}, skipping.")
                except Exception as e:
                    print(f"[REL] ⚠️  Relationship extraction failed for {file_change.path}: {e}")

            # 2. Extract Calls
            print(f"[REL] Extracting calls from {len(state.changed_files)} files...")
            for file_change in state.changed_files:
                file_path = repo_path / file_change.path
                if not file_path.exists():
                    continue

                try:
                    # Cooperative internal timeout
                    calls = self.call_extractor.extract_calls(file_path)
                    
                    if calls:
                        for call in calls:
                            # Resolve callee to a file
                            if call.callee_name in symbol_file_map:
                                for callee_file in symbol_file_map[call.callee_name]:
                                    self.graph_builder.build_call_edges(
                                        state.repo_id,
                                        str(file_change.path), call.caller_name,
                                        callee_file, call.callee_name,
                                        call.line,
                                    )
                                    call_edges += 1
                except TimeoutError:
                    print(f"[REL] ⚠️  Call extraction timed out for {file_change.path}, skipping.")
                except Exception:
                    pass

            print(f"[REL] ✅ Extracted {import_edges} imports, {call_edges} calls, {inherit_edges} inheritance edges")

        except Exception as e:
            print(f"[REL] ❌ Relationship extraction failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            # Non-fatal: don't set state.error so indexing continues

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
            repo = self.manifest.get_repository(state.repo_path)
            
            metadata = getattr(state, "metadata", {})
            branch = getattr(state, "branch", "main")
            
            if not repo:
                self.manifest.create_repository(
                    state.repo_path, 
                    repo_id=state.repo_id,
                    branch=branch,
                    org=getattr(state, 'org', None),
                    user_id=getattr(state, 'user_id', None)
                )
                repo = self.manifest.get_repository(state.repo_path)
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

        # ── Phase 2: Persist symbols (OPTIONAL — failure is non-fatal) ───
        try:
            symbols_to_persist = []
            repo_path = Path(state.repo_path)
            for file_change in state.changed_files:
                file_path = repo_path / file_change.path
                if not file_path.exists():
                    continue
                
                language = self.ast_extractor.detect_language(file_path)
                if not language:
                    continue
                    
                try:
                    result = self._get_ast(file_path, language)
                    if not result:
                        continue
                    for sym in result.symbols:
                        import hashlib
                        symbol_id = hashlib.sha256(
                            f"{state.repo_id}:{file_change.path}:{sym.type}:{sym.name}".encode()
                        ).hexdigest()[:20]
                        
                        parent_id = None
                        if sym.parent:
                            parent_id = hashlib.sha256(
                                f"{state.repo_id}:{file_change.path}:class:{sym.parent}".encode()
                            ).hexdigest()[:20]
                        
                        symbols_to_persist.append({
                            "symbol_id": symbol_id,
                            "file_path": str(file_change.path),
                            "symbol_name": sym.name,
                            "symbol_type": sym.type,
                            "signature": getattr(sym, 'signature', None),
                            "language": language,
                            "start_line": sym.start_line,
                            "end_line": sym.end_line,
                            "parent_symbol_id": parent_id,
                            "docstring": getattr(sym, 'docstring', None),
                            "commit_sha": state.commit_hash,
                        })
                except Exception as e:
                    print(f"[MANIFEST] ⚠️ Symbol extraction failed for {file_change.path}: {e}")
            
            if symbols_to_persist:
                count = self.manifest.upsert_symbols(state.repo_id, symbols_to_persist)
                state.symbols_extracted = count
                print(f"[MANIFEST] ✅ Persisted {count} symbols")

        except Exception as e:
            # Symbol persistence is non-fatal — repo is already saved
            print(f"[MANIFEST] ⚠️ Symbol persistence failed (non-fatal): {e}")

        # ── Phase 3: Save commit snapshot (OPTIONAL) ─────────────────────
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
