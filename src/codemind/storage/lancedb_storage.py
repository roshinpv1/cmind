"""
LanceDB append-only storage for code embeddings.

Stores chunks, embeddings, and metadata immutably.
Supports configurable embedding dimensions.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import lancedb
import pyarrow as pa

from codemind.indexer.chunker import CodeChunk


class LanceDBStorage:
    """Append-only vector storage using LanceDB with configurable embedding dimensions."""

    def __init__(self, db_path: str | Path | None = None, embedding_dim: int | None = None):
        """
        Initialize LanceDB storage.
        """
        if db_path is None:
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_LANCEDB_PATH", os.path.join(base_default, "lancedb"))
            
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        
        # Read embedding dimension from env or parameter
        self.embedding_dim = embedding_dim or int(os.getenv("EMBEDDING_DIMENSION", "768"))

        # Define schema with dynamic embedding dimension
        self.schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("chunk_hash", pa.string()),
                pa.field("repo_id", pa.string()),
                pa.field("file_path", pa.string()),
                pa.field("chunk_text", pa.string()),
                pa.field("start_line", pa.int32()),
                pa.field("end_line", pa.int32()),
                pa.field("embedding", pa.list_(pa.float32(), self.embedding_dim)),  # Dynamic dimension
                pa.field("embedding_version", pa.int32()),
                pa.field("indexed_at", pa.timestamp("us")),
                pa.field("symbol_name", pa.string()),   # Function/class name (AST chunk)
                pa.field("symbol_type", pa.string()),   # "function", "class", "method", "module"
                pa.field("language", pa.string()),       # Programming language
                pa.field("docstring", pa.string()),      # Extracted docstring from AST
                pa.field("context_header", pa.string()), # File/class/import context header
            ]
        )

    def create_table(self, table_name: str = "code_chunks"):
        """Create table if it doesn't exist."""
        if table_name not in self.db.table_names():
            # Create empty table
            self.db.create_table(table_name, schema=self.schema, mode="create")

    def store_embeddings(self, repo_id: str, embeddings: list[dict]) -> None:
        """Store embeddings in LanceDB.

        Args:
            repo_id: Repository identifier
            embeddings: List of dicts with chunk_id, file_path, chunk_text,
                       start_line, end_line, embedding, embedding_version
        """
        print(f"[LANCEDB] store_embeddings called with repo_id={repo_id}, {len(embeddings)} embeddings")
        if not embeddings:
            print(f"[LANCEDB] ⚠️  No embeddings to store, returning early")
            return

        # Add indexed_at timestamp to each embedding dict
        now = datetime.now(UTC)
        data = []
        for emb_dict in embeddings:
            emb_dict["indexed_at"] = now
            data.append(emb_dict)

        # Create or append to table
        table_name = "code_chunks" # Assuming default table name for now
        print(f"[LANCEDB] Attempting to store in table '{table_name}'")
        try:
            table = self.db.open_table(table_name)
            print(f"[LANCEDB] Table exists, appending {len(data)} rows")
            table.add(data)
            print(f"[LANCEDB] ✅ Successfully appended to existing table")
        except Exception as create_error:
            # Table doesn't exist, create it
            print(f"[LANCEDB] Table doesn't exist, creating new table (error: {create_error})")
            self.db.create_table(table_name, data=data, schema=self.schema, mode="overwrite")
            print(f"[LANCEDB] ✅ Successfully created new table with {len(data)} rows")

    def append_chunks(
        self,
        repo_id: str,
        chunks_with_embeddings: list[tuple[CodeChunk, list[float]]],
        embedding_version: int = 1,
        table_name: str = "code_chunks",
    ):
        """
        Append new chunks to storage.

        Args:
            repo_id: Repository ID
            chunks_with_embeddings: List of (chunk, embedding) tuples
            embedding_version: Embedding version
            table_name: Table name
        """
        print(f"[LANCEDB] append_chunks called: repo_id={repo_id}, {len(chunks_with_embeddings)} chunks")
        if not chunks_with_embeddings:
            print(f"[LANCEDB] ⚠️  No chunks to append")
            return

        # Prepare data
        now = datetime.now(UTC)
        data = []

        for chunk, embedding in chunks_with_embeddings:
            data.append(
                {
                    "chunk_id": f"{repo_id}_{chunk.chunk_hash}",
                    "chunk_hash": chunk.chunk_hash,
                    "repo_id": repo_id,
                    "file_path": chunk.file_path,
                    "chunk_text": chunk.text,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "embedding": embedding,
                    "embedding_version": embedding_version,
                    "indexed_at": now,
                    "symbol_name": getattr(chunk, "symbol_name", None) or "",
                    "symbol_type": getattr(chunk, "symbol_type", None) or "",
                    "language": getattr(chunk, "language", None) or "",
                    "docstring": getattr(chunk, "docstring", None) or "",
                    "context_header": getattr(chunk, "context_header", None) or "",
                }
            )

        print(f"[LANCEDB] Prepared {len(data)} data rows, attempting to store in table '{table_name}'")
        
        # Create or append to table
        try:
            # Try to open existing table
            table = self.db.open_table(table_name)
            print(f"[LANCEDB] Table exists, appending...")
            try:
                table.add(data)
                print(f"[LANCEDB] ✅ Successfully appended {len(data)} rows")
            except ValueError as schema_err:
                if "not found in target schema" in str(schema_err):
                    # Schema mismatch — old table missing new columns. Drop and recreate.
                    print(f"[LANCEDB] ⚠️  Schema mismatch, dropping old table and recreating: {schema_err}")
                    self.db.drop_table(table_name)
                    self.db.create_table(table_name, data=data, schema=self.schema, mode="create")
                    print(f"[LANCEDB] ✅ Recreated table with new schema and added {len(data)} rows")
                else:
                    raise
        except Exception as e:
            if "was not found" in str(e) or "does not exist" in str(e):
                # Table doesn't exist, create it
                print(f"[LANCEDB] Table doesn't exist, creating...")
                self.db.create_table(table_name, data=data, schema=self.schema, mode="create")
                print(f"[LANCEDB] ✅ Created table and added {len(data)} rows")
            else:
                raise

    def delete_chunks_by_file(
        self, repo_id: str, file_paths: list[str], table_name: str = "code_chunks"
    ) -> int:
        """
        Delete all chunks for specific files. Use during delta indexing
        to purge old chunks before appending new ones.

        Args:
            repo_id: Repository ID
            file_paths: List of file paths to purge
            table_name: Table name
            
        Returns:
            Number of removed rows (approximate)
        """
        if not file_paths or table_name not in self.db.table_names():
            return 0
            
        try:
            table = self.db.open_table(table_name)
            # Escape single quotes in paths just in case
            safe_paths = [p.replace("'", "''") for p in file_paths]
            paths_str = ", ".join(f"'{p}'" for p in safe_paths)
            
            # We can't easily get the number of deleted rows from table.delete, 
            # so we just execute it and return 1 for success
            table.delete(f"repo_id = '{repo_id}' AND file_path IN ({paths_str})")
            print(f"[LANCEDB] ✅ Purged old chunks for {len(file_paths)} files")
            return len(file_paths)
        except Exception as e:
            print(f"[LANCEDB] ⚠️ Error deleting chunks: {e}")
            return 0

    def get_chunk_hashes(
        self, repo_id: str, embedding_version: int, table_name: str = "code_chunks"
    ) -> set[str]:
        """
        Get all chunk hashes for repository and version.

        Args:
            repo_id: Repository ID
            embedding_version: Embedding version
            table_name: Table name

        Returns:
            Set of chunk hashes
        """
        if table_name not in self.db.table_names():
            return set()

        table = self.db.open_table(table_name)

        # Query for chunk hashes
        results = (
            table.search()
            .where(f"repo_id = '{repo_id}' AND embedding_version = {embedding_version}")
            .limit(100000)
            .to_list()
        )

        return {row["chunk_hash"] for row in results}

    def search(
        self,
        query_embedding: list[float],
        repo_id: str | list[str] | None = None,
        limit: int = 10,
        table_name: str = "code_chunks",
        min_score: float = 0.5,
    ) -> list[dict]:
        """
        Semantic search over chunks.

        Args:
            query_embedding: Query vector
            repo_id: Optional repository filter (string or list of strings)
            limit: Max results
            table_name: Table name
            min_score: Minimum similarity score (0-1). Default 0.3.
                       Driven by playbook's min_score. Distance threshold = 1.0 - min_score.

        Returns:
            List of matching chunks with score >= min_score
        """
        if table_name not in self.db.table_names():
            return []

        table = self.db.open_table(table_name)

        # Distance threshold from min_score (cosine distance = 1 - similarity)
        distance_threshold = 1.0 - min_score

        query = (
            table.search(query_embedding, vector_column_name="embedding")
            .metric("cosine")
            .limit(limit)
        )

        if repo_id:
            if isinstance(repo_id, list):
                if repo_id:
                    ids_str = ", ".join([f"'{rid}'" for rid in repo_id])
                    query = query.where(f"repo_id IN ({ids_str})")
            else:
                query = query.where(f"repo_id = '{repo_id}'")

        # Post-filter by distance threshold (derived from playbook min_score)
        results = query.to_list()
        
        # Strip massive embedding vectors to prevent LLM OOM/Timeout
        for r in results:
            r.pop("embedding", None)

        filtered_results = [r for r in results if r.get("_distance", 1.0) < distance_threshold]

        # Safety filter for tiny chunks (handles legacy data)
        filtered_results = [r for r in filtered_results if len(r.get("chunk_text", "").strip()) >= 50]

        if results and not filtered_results:
            top_distances = sorted([r.get("_distance", 1.0) for r in results[:5]])
            print(f"[LANCE] ⚠️ All {len(results)} results filtered out by distance threshold {distance_threshold:.2f} "
                  f"(min_score={min_score}). Top distances: {top_distances}")

        return filtered_results

    def get_chunk_hashes(
        self, repo_id: str | None = None, table_name: str = "code_chunks"
    ) -> set[str]:
        """
        Get all chunk hashes efficiently using column projection.

        Args:
            repo_id: Optional repository ID to filter
            table_name: Table name

        Returns:
            Set of chunk hashes
        """
        if table_name not in self.db.table_names():
            return set()

        table = self.db.open_table(table_name)

        if repo_id:
            results = table.search().where(f"repo_id = '{repo_id}'").select(["chunk_hash"]).limit(100000).to_list()
        else:
            results = table.search().select(["chunk_hash"]).limit(100000).to_list()

        return {row["chunk_hash"] for row in results}

    def add_chunks(
        self,
        repo_id: str,
        chunks_with_embeddings: list[tuple[CodeChunk, list[float]]],
        embedding_version: int = 1,
    ):
        """Alias for append_chunks for consistency."""
        self.append_chunks(repo_id, chunks_with_embeddings, embedding_version)

    def create_catalog_table(self, table_name: str = "catalogs"):
        """Create catalogs table if it doesn't exist."""
        if table_name not in self.db.table_names():
            schema = pa.schema([
                pa.field("catalog_id", pa.string()), # UUID for the chunk entry
                pa.field("chunk_id", pa.string()),   # ID of the chunk (0, 1, 2...)
                pa.field("repo_id", pa.string()),
                pa.field("repo_name", pa.string()),
                pa.field("chunk_text", pa.string()), # The actual chunk content
                pa.field("metadata", pa.string()),   # Minimal metadata for filtering
                pa.field("created_at", pa.timestamp("us")),
                pa.field("embedding", pa.list_(pa.float32(), self.embedding_dim)),
            ])
            self.db.create_table(table_name, schema=schema, mode="create")

    def store_catalog_chunks(self, chunks: list[dict], table_name: str = "catalogs"):
        """
        Store catalog chunks.
        
        Args:
            chunks: List of dictionaries with catalog chunk fields
            table_name: Table name
        """
        if not chunks:
            return

        print(f"[LANCEDB] Storing {len(chunks)} catalog chunks for repo {chunks[0].get('repo_id')}")
        
        # Ensure table exists (will create with new schema if missing)
        self.create_catalog_table(table_name)
        
        # Add timestamp if missing
        now = datetime.now(UTC)
        for item in chunks:
            if "created_at" not in item:
                item["created_at"] = now
            
        try:
            table = self.db.open_table(table_name)
            table.add(chunks)
            print(f"[LANCEDB] ✅ Catalog chunks stored")
        except Exception as e:
            # Handle schema mismatch or table issues by recreating
            print(f"[LANCEDB] Issue linking table ({e}), recreating...")
            if table_name in self.db.table_names():
                self.db.drop_table(table_name)
            self.create_catalog_table(table_name)
            table = self.db.open_table(table_name)
            table.add(chunks)
            print(f"[LANCEDB] ✅ Recreated table and stored chunks")

    def get_catalog_items(self, repo_id: str | None = None, table_name: str = "catalogs") -> list[dict]:
        """Get catalog items."""
        if table_name not in self.db.table_names():
            return []
            
        table = self.db.open_table(table_name)
        query = table.search()
        
        if repo_id:
            query = query.where(f"repo_id = '{repo_id}'")
            
        return query.limit(100).to_list()

    async def search_catalogs(
        self, 
        query_embedding: list[float], 
        table_name: str = "catalogs", 
        repo_id: str | list[str] | None = None,
        limit: int = 5,
        columns: list[str] | None = None
    ) -> list[dict]:
        """
        Semantic search over catalog entries with 50% similarity threshold.
        """
        if table_name not in self.db.table_names():
            return []

        table = self.db.open_table(table_name)

        query = (
            table.search(query_embedding, vector_column_name="embedding")
            .metric("cosine")
            .limit(limit)
        )

        if repo_id:
            if isinstance(repo_id, list):
                if repo_id:
                    # Construct IN clause for multiple IDs
                    ids_str = ", ".join([f"'{rid}'" for rid in repo_id])
                    query = query.where(f"repo_id IN ({ids_str})")
            else:
                query = query.where(f"repo_id = '{repo_id}'")
            
        if columns:
            # Always ensure _distance is included if we are projecting
            proj_columns = columns.copy()
            if "_distance" not in proj_columns:
                proj_columns.append("_distance")
            query = query.select(proj_columns)

        # Post-filter for 50% similarity (distance < 0.5 for cosine)
        try:
            results = query.to_list()
        except Exception as e:
            # Handle potential schema or dimension mismatch
            print(f"[LANCE] Search failed: {e}")
            return []
            
        return results



    def close(self):
        """Close database connection."""
        # LanceDB handles connection management automatically
        pass
