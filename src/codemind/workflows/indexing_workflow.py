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


class IndexingWorkflow:
    """LangGraph-style workflow for code indexing."""

    def __init__(
        self,
        manifest_manager: ManifestManager,
        lance_storage: LanceDBStorage,
        graph_db,
    ):
        """Initialize workflow."""
        self.manifest = manifest_manager
        self.storage = lance_storage
        self.graph = graph_db

        # Initialize components
        self.ast_extractor = ASTExtractor()
        self.chunker = ASTChunker()  # AST-aware chunking (falls back to char-based)
        self.call_extractor = CallExtractor()
        self.embedder = EmbeddingGenerator()
        self.graph_builder = GraphBuilder(graph_db)

    def run(self, state: IndexingState) -> IndexingState:
        """Execute full indexing workflow."""
        try:
            print(f"[WORKFLOW] ========== STARTING INDEXING WORKFLOW ==========")
            print(f"[WORKFLOW] Repo: {state.repo_id}")
            
            state = self.detect_changes(state)
            if state.error:
                print(f"[WORKFLOW] ❌ detect_changes failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ detect_changes complete")

            state = self.extract_ast(state)
            if state.error:
                print(f"[WORKFLOW] ❌ extract_ast failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ extract_ast complete")

            state = self.chunk_files(state)  # FIXED: was chunk_code
            if state.error:
                print(f"[WORKFLOW] ❌ chunk_files failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ chunk_files complete")

            state = self.generate_embeddings(state)
            if state.error:
                print(f"[WORKFLOW] ❌ generate_embeddings failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ generate_embeddings complete")

            state = self.build_graph(state)
            if state.error:
                print(f"[WORKFLOW] ❌ build_graph failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ build_graph complete")

            state = self.extract_relationships(state)
            if state.error:
                print(f"[WORKFLOW] ❌ extract_relationships failed: {state.error}")
                return state
            print(f"[WORKFLOW] ✅ extract_relationships complete")

            state = self.update_manifest(state)
            state.stage = "completed"

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
        try:
            state.stage = "chunking"
            print(f"[CHUNKING] Starting chunking for repo: {state.repo_id}")
            print(f"[CHUNKING] Changed files: {len(state.changed_files)}")

            all_chunks = []
            for file_change in state.changed_files:
                file_path = Path(state.repo_path) / file_change.path

                # Chunk all programming-related files
                if file_path.exists() and (
                    file_path.suffix.lower() in CODE_EXTENSIONS
                    or file_path.name in KNOWN_FILENAMES
                ):
                    chunks = self.chunker.chunk_file(str(file_path))
                    all_chunks.extend(chunks)

            print(f"[CHUNKING] Created {len(all_chunks)} chunks total")
            state.chunks_created = len(all_chunks)

            # Store chunks in state for embedding
            print(f"[CHUNKING] Setting state.all_chunks with {len(all_chunks)} chunks")
            state.all_chunks = all_chunks
            print(f"[CHUNKING] ✅ Chunking complete")

        except Exception as e:
            print(f"[CHUNKING] ❌ Error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            state.error = f"Chunking failed: {e}"

        return state

    def generate_embeddings(self, state: IndexingState) -> IndexingState:
        """Node: Generate embeddings."""
        try:
            state.stage = "embedding"
            print(f"[EMBEDDING] ========== STARTING EMBEDDING STEP ==========")
            print(f"[EMBEDDING] Repo ID: {state.repo_id}")
            print(f"[EMBEDDING] Has all_chunks attr: {hasattr(state, 'all_chunks')}")
            if hasattr(state, 'all_chunks'):
                print(f"[EMBEDDING] all_chunks length: {len(state.all_chunks) if state.all_chunks else 0}")

            if not hasattr(state, "all_chunks") or not state.all_chunks:
                print(f"[EMBEDDING] ⚠️  No chunks found in state - skipping embedding")
                return state

            print(f"[EMBEDDING] Processing {len(state.all_chunks)} chunks...")

            # Get existing chunks from LanceDB to avoid re-embedding
            existing_hashes = set()
            try:
                existing_data = self.storage.get_all_chunks(state.repo_id)
                existing_hashes = {row["chunk_hash"] for row in existing_data}
            except Exception:
                # Table might not exist yet
                pass

            # Generate embeddings only for new chunks
            print(f"[EMBEDDING] Calling embedder.generate_embeddings...")
            new_chunks_with_embeddings = self.embedder.generate_embeddings(
                state.all_chunks, existing_hashes
            )
            print(f"[EMBEDDING] Generated {len(new_chunks_with_embeddings)} new embeddings")

            # Add to LanceDB
            if new_chunks_with_embeddings:
                print(f"[EMBEDDING] Storing {len(new_chunks_with_embeddings)} embeddings")
                self.storage.append_chunks(state.repo_id, new_chunks_with_embeddings)
                print(f"[EMBEDDING] ✅ Successfully stored embeddings")

            state.embeddings_generated = len(new_chunks_with_embeddings)

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
            if not repo:
                # Use commit hash from state (set during change detection if Git repo)
                self.manifest.create_repository(state.repo_path, state.commit_hash)
                repo = self.manifest.get_repository(state.repo_path)

            # Update file manifests in batch
            self.manifest.update_files(repo.repo_id, state.changed_files, state.deleted_files)

        except Exception as e:
            state.error = f"Manifest update failed: {e}"

        return state
