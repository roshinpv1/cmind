"""
Incremental embedding generation.

Uses SentenceTransformers with BGE for code embeddings.
Token-aware truncation prevents OOM from oversized sequences.
"""

import logging
import numpy as np
from sentence_transformers import SentenceTransformer

from .chunker import CodeChunk

logger = logging.getLogger(__name__)

# BGE query instruction (documents need no prefix)
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbeddingGenerator:
    """Generates embeddings using BAAI/bge-base-en-v1.5 (768d)."""

    MAX_TOKENS = 512
    BATCH_SIZE = 32  # BGE is lightweight enough for larger batches

    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5", version: int = 3):
        """
        Initialize embedding generator.

        Args:
            model_name: SentenceTransformers model name
            version: Embedding version for tracking
        """
        self.model_name = model_name
        self.version = version
        self.model = SentenceTransformer(model_name)
        self.model.max_seq_length = self.MAX_TOKENS

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
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            batch_emb = self.model.encode(
                batch,
                batch_size=self.BATCH_SIZE,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            all_embeddings.extend(batch_emb)
            logger.info(f"[EMBEDDING] Processed {min(i + self.BATCH_SIZE, len(texts))}/{len(texts)} chunks")

        return list(zip(new_chunks, all_embeddings, strict=False))

    def encode_query(self, query: str) -> list[float]:
        """
        Encode a search query with BGE instruction prefix.

        BGE requires an instruction prefix for queries (but not documents).
        """
        prefixed = f"{BGE_QUERY_INSTRUCTION}{query}"
        embedding = self.model.encode(
            [prefixed], normalize_embeddings=True
        )
        return embedding[0].tolist()

    def get_embedding_dim(self) -> int:
        """Get embedding dimensionality."""
        return self.model.get_sentence_embedding_dimension()
