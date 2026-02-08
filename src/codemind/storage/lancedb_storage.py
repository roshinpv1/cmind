"""
LanceDB append-only storage for code embeddings.

Stores chunks, embeddings, and metadata immutably.
"""

from datetime import UTC, datetime
from pathlib import Path

import lancedb
import pyarrow as pa

from codemind.indexer.chunker import CodeChunk


class LanceDBStorage:
    """Append-only vector storage using LanceDB."""

    def __init__(self, db_path: str | Path = "data/lancedb"):
        """
        Initialize LanceDB storage.

        Args:
            db_path: Path to LanceDB directory
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))

        # Define schema
        self.schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("chunk_hash", pa.string()),
                pa.field("repo_id", pa.string()),
                pa.field("file_path", pa.string()),
                pa.field("chunk_text", pa.string()),
                pa.field("start_line", pa.int32()),
                pa.field("end_line", pa.int32()),
                pa.field("embedding", pa.list_(pa.float32(), 768)),  # nomic-embed-code
                pa.field("embedding_version", pa.int32()),
                pa.field("indexed_at", pa.timestamp("us")),
                pa.field("symbol_name", pa.string()),   # Function/class name (AST chunk)
                pa.field("symbol_type", pa.string()),   # "function", "class", "method", "module"
                pa.field("language", pa.string()),       # Programming language
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
        repo_id: str | None = None,
        limit: int = 10,
        table_name: str = "code_chunks",
    ) -> list[dict]:
        """
        Semantic search over chunks.

        Args:
            query_embedding: Query vector
            repo_id: Optional repository filter
            limit: Max results
            table_name: Table name

        Returns:
            List of matching chunks with scores
        """
        if table_name not in self.db.table_names():
            return []

        table = self.db.open_table(table_name)

        query = table.search(query_embedding, vector_column_name="embedding").limit(limit)

        if repo_id:
            query = query.where(f"repo_id = '{repo_id}'")

        return query.to_list()

    def get_all_chunks(
        self, repo_id: str | None = None, table_name: str = "code_chunks"
    ) -> list[dict]:
        """
        Get all chunks, optionally filtered by repo.

        Args:
            repo_id: Optional repository ID to filter
            table_name: Table name

        Returns:
            List of chunk dictionaries
        """
        if table_name not in self.db.table_names():
            return []

        table = self.db.open_table(table_name)

        if repo_id:
            results = table.search().where(f"repo_id = '{repo_id}'").limit(100000).to_list()
        else:
            results = table.search().limit(100000).to_list()

        return results

    def add_chunks(
        self,
        repo_id: str,
        chunks_with_embeddings: list[tuple[CodeChunk, list[float]]],
        embedding_version: int = 1,
    ):
        """Alias for append_chunks for consistency."""
        self.append_chunks(repo_id, chunks_with_embeddings, embedding_version)

    def close(self):
        """Close database connection."""
        # LanceDB handles connection management automatically
        pass
