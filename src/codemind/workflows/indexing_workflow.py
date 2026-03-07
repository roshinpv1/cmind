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
    changed_files: list[FileChange] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    commit_hash: str | None = None  # Git commit hash if using git detection
    metadata: dict = field(default_factory=dict)
    org: str | None = None  # Organization owning this component


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
        try:
            print(f"[WORKFLOW] ========== STARTING INDEXING WORKFLOW ==========")
            print(f"[WORKFLOW] Repo: {state.repo_id}")
            
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
            state = self.chunk_files(state)  # FIXED: was chunk_code
            if state.error:
                print(f"[WORKFLOW] ❌ chunk_files failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ chunk_files complete — {state.chunks_created} chunks")
            self._report_progress("chunking", 35)

            self._report_progress("embedding", 40)
            state = self.generate_embeddings(state)
            if state.error:
                print(f"[WORKFLOW] ❌ generate_embeddings failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ generate_embeddings complete — {state.embeddings_generated} embeddings")
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
            state.stage = "completed"
            self._report_progress("completed", 100)

        except Exception as e:
            state.error = str(e)
            state.stage = "failed"

        return state

    def detect_changes(self, state: IndexingState) -> IndexingState:
        """Node: Detect changed files."""
        try:
            state.stage = "detecting_changes"

            detector = ChangeDetector(state.repo_path)
            repo = self.manifest.get_repository(state.repo_path)

            if repo:
                last_commit = repo.last_commit_hash
                last_hashes = self.manifest.get_file_hashes(repo.repo_id)
                changes = detector.detect_changes(last_commit, last_hashes)
            else:
                changes = detector.detect_changes()

            state.files_changed = len(changes.changed_files)
            state.changed_files = changes.changed_files
            state.deleted_files = changes.deleted_files
            state.commit_hash = changes.commit_hash  # Store git commit if available
            
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

    def chunk_files(self, state: IndexingState) -> IndexingState:
        """Node: Chunk files into smaller pieces."""
        import threading
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        PER_FILE_TIMEOUT = 30  # seconds — skip files that take longer
        MAX_FILE_SIZE = 500 * 1024  # 500 KB

        try:
            state.stage = "chunking"
            total = len(state.changed_files)
            print(f"[CHUNKING] Starting chunking for repo: {state.repo_id}")
            print(f"[CHUNKING] Changed files: {total}")

            all_chunks = []
            skipped = 0
            errored = 0
            timed_out = 0

            for idx, file_change in enumerate(state.changed_files):
                file_path = Path(state.repo_path) / file_change.path

                # Chunk all programming-related files
                if file_path.exists() and (
                    file_path.suffix.lower() in CODE_EXTENSIONS
                    or file_path.name in KNOWN_FILENAMES
                ):
                    try:
                        # Skip very large files
                        try:
                            fsize = file_path.stat().st_size
                            if fsize > MAX_FILE_SIZE:
                                skipped += 1
                                continue
                        except OSError:
                            pass

                        # Use a daemon thread with timeout so hangs don't block
                        result_holder = [None]
                        error_holder = [None]

                        def _do_chunk(fp=str(file_path)):
                            try:
                                result_holder[0] = self.chunker.chunk_file(fp)
                            except Exception as ex:
                                error_holder[0] = ex

                        t = threading.Thread(target=_do_chunk, daemon=True)
                        t.start()
                        t.join(timeout=PER_FILE_TIMEOUT)

                        if t.is_alive():
                            # Thread is still running (hung) — skip this file
                            timed_out += 1
                            print(f"[CHUNKING] ⏰ TIMEOUT ({PER_FILE_TIMEOUT}s) on: {file_change.path}")
                            # Daemon thread will be killed when process exits
                        elif error_holder[0]:
                            raise error_holder[0]
                        elif result_holder[0]:
                            all_chunks.extend(result_holder[0])

                    except Exception as e:
                        errored += 1
                        print(f"[CHUNKING] ⚠️ Error chunking {file_change.path}: {type(e).__name__}: {e}")

                # Progress log every 50 files or at the end
                if (idx + 1) % 50 == 0 or idx == total - 1:
                    pct = int((idx + 1) / total * 100)
                    print(f"[CHUNKING] Progress: {idx + 1}/{total} files ({pct}%), {len(all_chunks)} chunks so far")

            print(f"[CHUNKING] Created {len(all_chunks)} chunks total (skipped={skipped} large, errors={errored}, timeouts={timed_out})")
            state.chunks_created = len(all_chunks)

            # Store chunks in state for embedding
            state.all_chunks = all_chunks
            print(f"[CHUNKING] ✅ Chunking complete")

        except Exception as e:
            print(f"[CHUNKING] ❌ Error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            state.error = f"Chunking failed: {e}"

        return state

    def generate_embeddings(self, state: IndexingState) -> IndexingState:
        """Node: Generate embeddings in batches to limit memory."""
        EMBED_BATCH_SIZE = 500  # Process and write 500 chunks at a time

        try:
            state.stage = "embedding"
            print(f"[EMBEDDING] ========== STARTING EMBEDDING STEP ==========")
            print(f"[EMBEDDING] Repo ID: {state.repo_id}")

            if not hasattr(state, "all_chunks") or not state.all_chunks:
                print(f"[EMBEDDING] ⚠️  No chunks found in state - skipping embedding")
                return state

            total_chunks = len(state.all_chunks)
            print(f"[EMBEDDING] Processing {total_chunks} chunks in batches of {EMBED_BATCH_SIZE}...")

            # Get existing chunks from LanceDB to avoid re-embedding
            existing_hashes = set()
            try:
                existing_data = self.storage.get_all_chunks(state.repo_id)
                existing_hashes = {row["chunk_hash"] for row in existing_data}
                del existing_data  # Free immediately
            except Exception:
                pass

            total_embedded = 0

            # Process in batches to limit peak memory
            for batch_start in range(0, total_chunks, EMBED_BATCH_SIZE):
                batch_end = min(batch_start + EMBED_BATCH_SIZE, total_chunks)
                batch_chunks = state.all_chunks[batch_start:batch_end]

                # Generate embeddings for this batch
                new_with_emb = self.embedder.generate_embeddings(batch_chunks, existing_hashes)

                # Write to LanceDB immediately
                if new_with_emb:
                    self.storage.append_chunks(state.repo_id, new_with_emb)
                    total_embedded += len(new_with_emb)

                pct = int(batch_end / total_chunks * 100)
                print(f"[EMBEDDING] Batch {batch_start}-{batch_end}/{total_chunks} ({pct}%) — {len(new_with_emb)} new embeddings written")

                # Free batch memory
                del new_with_emb
                del batch_chunks

            state.embeddings_generated = total_embedded
            print(f"[EMBEDDING] ✅ Done: {total_embedded} embeddings stored")

            # Free chunk text from memory — no longer needed
            state.all_chunks = None

        except Exception as e:
            print(f"[EMBEDDING] ❌ Error during embedding: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            state.error = f"Embedding failed: {e}"

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
                        result = self.ast_extractor.extract(file_path, language)

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
                    except Exception as e:
                        print(f"[GRAPH] ⚠️  AST extraction failed for {file_change.path}: {e}")
                        pass

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

            for file_change in state.changed_files:
                file_path = repo_path / file_change.path
                if not file_path.exists():
                    continue

                language = self.ast_extractor.detect_language(file_path)
                if not language:
                    continue

                try:
                    result = self.ast_extractor.extract(file_path, language)

                    # Index symbols
                    for sym in result.symbols:
                        if sym.name not in symbol_file_map:
                            symbol_file_map[sym.name] = []
                        symbol_file_map[sym.name].append(str(file_change.path))

                    # Build IMPORTS edges
                    for imp in result.imports:
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

                except Exception as e:
                    print(f"[REL] ⚠️  Relationship extraction failed for {file_change.path}: {e}")

            # Build CALLS edges
            for file_change in state.changed_files:
                file_path = repo_path / file_change.path
                if not file_path.exists():
                    continue

                try:
                    calls = self.call_extractor.extract_calls(file_path)
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
                except Exception:
                    pass

            print(f"[REL] ✅ Extracted {import_edges} imports, {call_edges} calls, {inherit_edges} inheritance edges")

        except Exception as e:
            print(f"[REL] ❌ Relationship extraction failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            # Non-fatal: don't set state.error so indexing continues

        return state

    def update_manifest(self, state: IndexingState) -> IndexingState:
        """Node: Update manifest."""
        try:
            state.stage = "updating_manifest"

            # Create or update repository
            repo = self.manifest.get_repository(state.repo_path)
            
            metadata = getattr(state, "metadata", {})
            branch = getattr(state, "branch", "main")  # Start using branch if available in state
            
            if not repo:
                # Use ID from state (which might come from GitRepoManager)
                self.manifest.create_repository(
                    state.repo_path, 
                    repo_id=state.repo_id,
                    branch=branch,
                    org=getattr(state, 'org', None)
                )
                repo = self.manifest.get_repository(state.repo_path)
            
            # Update manifest with commit hash and metadata, ensuring ID consistency
            self.manifest.update_repository(
                state.repo_id,  # Use state ID to be sure
                branch=branch,
                org=getattr(state, 'org', None),
                last_commit_hash=state.commit_hash,
                metadata=metadata
            )

            # Update file manifests in batch
            self.manifest.update_files(repo.repo_id, state.changed_files, state.deleted_files)

        except Exception as e:
            state.error = f"Manifest update failed: {e}"

        return state
