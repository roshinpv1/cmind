"""
Incremental embedding generation.

Uses SentenceTransformers to generate embeddings only for changed chunks.
"""

import numpy as np
from sentence_transformers import SentenceTransformer

from .chunker import CodeChunk


class EmbeddingGenerator:
    """Generates embeddings for code chunks."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", version: int = 1):
        """
        Initialize embedding generator.

        Args:
            model_name: SentenceTransformers model name
            version: Embedding version for tracking
        """
        self.model_name = model_name
        self.version = version
        self.model = SentenceTransformer(model_name)

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

        # Filter to only new chunks
        new_chunks = [c for c in chunks if c.chunk_hash not in existing_hashes]

        if not new_chunks:
            return []

        # Generate embeddings in batch
        texts = [chunk.text for chunk in new_chunks]
        embeddings = self.model.encode(texts, show_progress_bar=False)

        return list(zip(new_chunks, embeddings, strict=False))  # Same length guaranteed

    def get_embedding_dim(self) -> int:
        """Get embedding dimensionality."""
        return self.model.get_sentence_embedding_dimension()
