"""
Incremental embedding generation.

Uses SentenceTransformers with configurable embedding models.
Token-aware truncation prevents OOM from oversized sequences.
"""

import logging
import os
import numpy as np
from sentence_transformers import SentenceTransformer

from .chunker import CodeChunk

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """Generates embeddings using configurable SentenceTransformer models."""

    def __init__(
        self,
        model_name: str | None = None,
        max_tokens: int | None = None,
        batch_size: int | None = None,
        query_prefix: str | None = None,
        version: int = 3
    ):
        """
        Initialize embedding generator with configurable parameters.

        Args:
            model_name: SentenceTransformers model name (default: from EMBEDDING_MODEL env)
            max_tokens: Max sequence length (default: from EMBEDDING_MAX_TOKENS env)
            batch_size: Batch size for encoding (default: from EMBEDDING_BATCH_SIZE env)
            query_prefix: Query instruction prefix (default: from EMBEDDING_QUERY_PREFIX env)
            version: Embedding version for tracking
        """
        # Read from env with defaults
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
        self.max_tokens = max_tokens or int(os.getenv("EMBEDDING_MAX_TOKENS", "512"))
        self.batch_size = batch_size or int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
        self.query_prefix = query_prefix or os.getenv(
            "EMBEDDING_QUERY_PREFIX",
            "Represent this sentence for searching relevant passages: "
        )
        self.version = version
        
        # Load model (supports both HuggingFace names and local paths)
        # Examples:
        #   - "BAAI/bge-base-en-v1.5" (HuggingFace)
        #   - "/path/to/local/model" (local directory)
        #   - "sentence-transformers/all-MiniLM-L6-v2" (HuggingFace)
        logger.info(f"[EMBEDDING] Loading model: {self.model_name}")
        self.model = SentenceTransformer(self.model_name)
        self.model.max_seq_length = self.max_tokens
        
        # Auto-detect embedding dimensions
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        
        logger.info(
            f"[EMBEDDING] Model loaded: {self.model_name} "
            f"({self.embedding_dim}d, max_tokens={self.max_tokens}, batch_size={self.batch_size})"
        )

    def generate_embeddings(
        self, chunks: list[CodeChunk], existing_hashes: set[str] | None = None
    ) -> list[tuple[CodeChunk, np.ndarray]]:
        """
        Generate embeddings for chunks.

        Args:
            chunks: Code chunks to embed
            existing_hashes: Set of chunk hashes that already have embeddings

        Returns:
            List of (chunk, embedding) tuples for NEW chunks only
        """
        if existing_hashes is None:
            existing_hashes = set()

        new_chunks = [c for c in chunks if c.chunk_hash not in existing_hashes]
        if not new_chunks:
            return []

        # BGE: no prefix needed for documents
        texts = [chunk.text for chunk in new_chunks]

        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            batch_emb = self.model.encode(
                batch,
                batch_size=self.batch_size,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            all_embeddings.extend(batch_emb)
            logger.info(f"[EMBEDDING] Processed {min(i + self.batch_size, len(texts))}/{len(texts)} chunks")

        return list(zip(new_chunks, all_embeddings, strict=False))

    def encode_query(self, query: str) -> list[float]:
        """
        Encode a search query with instruction prefix.

        Many embedding models (e.g., BGE) require an instruction prefix for queries
        but not for documents. The prefix is configurable via EMBEDDING_QUERY_PREFIX.
        """
        prefixed = f"{self.query_prefix}{query}"
        embedding = self.model.encode(
            [prefixed], normalize_embeddings=True
        )
        return embedding[0].tolist()

    def get_embedding_dim(self) -> int:
        """Get embedding dimensionality."""
        return self.embedding_dim
