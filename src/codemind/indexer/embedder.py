"""
Incremental embedding generation.

Supports:
- Local: SentenceTransformers (default)
- Apigee: Enterprise embedding API
"""

import logging
import os
import numpy as np
import asyncio
import httpx
import uuid
import datetime
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional

# Conditional import to avoid hard dependency if using Apigee only
try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

from .chunker import CodeChunk
try:
    from codemind.llm.token_manager import ApigeeTokenManager
except ImportError:
    ApigeeTokenManager = None

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""
    
    @abstractmethod
    def get_embedding_dim(self) -> int:
        pass
        
    @abstractmethod
    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        pass


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local embedding using SentenceTransformers."""
    
    def __init__(self, model_name: str, max_tokens: int):
        if not HAS_SENTENCE_TRANSFORMERS:
            raise ImportError("sentence-transformers not installed. Required for local embeddings.")
            
        import torch
        device = "cpu"
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
            
        logger.info(f"[EMBEDDING] Loading local model: {model_name} on device: {device}")
        self.model = SentenceTransformer(model_name, device=device)
        self.model.max_seq_length = max_tokens
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        
    def get_embedding_dim(self) -> int:
        return self.embedding_dim
        
    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        return self.model.encode(
            texts,
            batch_size=len(texts),
            show_progress_bar=False,
            normalize_embeddings=True,
        )


class ApigeeEmbeddingProvider(EmbeddingProvider):
    """Apigee-hosted embedding API."""
    
    def __init__(self, model_name: str):
        if not ApigeeTokenManager:
            raise ImportError("ApigeeTokenManager not available")
            
        self.model_name = model_name
        self.token_manager = ApigeeTokenManager()
        self.base_url = os.environ.get("ENTERPRISE_BASE_URL")
        
        # Apigee specific headers/config
        self.wf_client_id = os.environ.get("WF_CLIENT_ID")
        self.wf_api_key = os.environ.get("WF_API_KEY")
        self.wf_use_case_id = os.environ.get("WF_USE_CASE_ID")
        
        if not all([self.base_url, self.wf_client_id, self.wf_api_key, self.wf_use_case_id]):
             logger.warning("[EMBEDDING] Apigee configuration incomplete. Check env vars.")

        # Use EMBEDDING_DIMENSION (same env var as LanceDB schema) for consistency
        self.embedding_dim = int(os.getenv("EMBEDDING_DIMENSION", "768"))
        logger.info(f"[EMBEDDING] Initialized Apigee provider for model {model_name} (assumed dim: {self.embedding_dim})")

    def get_embedding_dim(self) -> int:
        return self.embedding_dim
        
    async def _get_embeddings_async(self, texts: List[str]) -> List[np.ndarray]:
        """Async call to Apigee."""
        token = await self.token_manager.get_token()
        
        url = f"{self.base_url}/v1/embeddings"
        
        headers = {
            "x-wf-request-date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "Authorization": f"Bearer {token}",
            "x-request-id": str(uuid.uuid4()),
            "x-correlation-id": str(uuid.uuid4()),
            "X-WF-client-id": self.wf_client_id,
            "X-WF-api-key": self.wf_api_key,
            "X-WF-usecase-id": self.wf_use_case_id,
            "Content-Type": "application/json"
        }
        
        # Replace newlines as recommended by OpenAI for embeddings
        cleaned_texts = [t.replace("\n", " ") for t in texts]
        
        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": self.model_name,
                    "input": cleaned_texts
                }
            )
            
            if response.status_code == 401:
                 # Retry logic could go here similar to LLM driver
                 pass
                 
            response.raise_for_status()
            data = response.json()
            
            # Extract embeddings
            # OpenAI format: data: [{embedding: [...], index: 0}, ...]
            results = sorted(data["data"], key=lambda x: x["index"])
            return [np.array(item["embedding"]) for item in results]

    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        """Synchronous wrapper for async call."""
        try:
            return asyncio.run(self._get_embeddings_async(texts))
        except RuntimeError:
            # If we are already in an event loop (e.g. jupyter), asyncio.run fails.
            # But here we are likely in a thread pool (background task) which might NOT have a loop.
            # Or if we are in main thread loop...
            # If we are in a running loop, we should use that loop?
            # Creating a new loop is safer for threaded usage.
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._get_embeddings_async(texts))
            finally:
                loop.close()


class RemoteEmbeddingProvider(EmbeddingProvider):
    """Remote embedding API (OpenAI-compatible)."""
    
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.base_url = os.environ.get("EMBEDDING_API_URL")
        self.api_key = os.environ.get("EMBEDDING_API_KEY")
        
        if not self.base_url:
            raise ValueError("EMBEDDING_API_URL must be set for remote provider")
            
        # Auto-detect dimension via probe request, fall back to EMBEDDING_DIMENSION env
        self.embedding_dim = self._detect_dimension()
        logger.info(f"[EMBEDDING] Initialized Remote provider for model {model_name} at {self.base_url} (dim: {self.embedding_dim})")

    def _detect_dimension(self) -> int:
        """Detect embedding dimension by sending a probe request to the remote API."""
        try:
            import httpx as _httpx
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            with _httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json={"model": self.model_name, "input": "dimension probe"},
                )
                resp.raise_for_status()
                dim = len(resp.json()["data"][0]["embedding"])
                logger.info(f"[EMBEDDING] Auto-detected remote embedding dim: {dim}")
                return dim
        except Exception as e:
            fallback = int(os.getenv("EMBEDDING_DIMENSION", "768"))
            logger.warning(f"[EMBEDDING] Could not auto-detect dim ({e}), falling back to {fallback}")
            return fallback

    def get_embedding_dim(self) -> int:
        return self.embedding_dim
        
    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        """Synchronous embedding call using httpx.Client (works in both sync and async contexts)."""
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        # Replace newlines as recommended by OpenAI for embeddings
        cleaned_texts = [t.replace("\n", " ") for t in texts]
        
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={
                    "model": self.model_name,
                    "input": cleaned_texts
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Extract embeddings (OpenAI format)
            results = sorted(data["data"], key=lambda x: x["index"])
            return [np.array(item["embedding"]) for item in results]


class EmbeddingGenerator:
    """Generates embeddings using configurable provider (Local or Apigee)."""

    def __init__(
        self,
        model_name: str | None = None,
        max_tokens: int | None = None,
        batch_size: int | None = None,
        query_prefix: str | None = None,
        version: int = 3
    ):
        # Read from env with defaults
        self.provider_type = os.getenv("EMBEDDING_PROVIDER", "local").lower()
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
        self.max_tokens = max_tokens or int(os.getenv("EMBEDDING_MAX_TOKENS", "512"))
        self.batch_size = batch_size or int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
        self.query_prefix = query_prefix or os.getenv(
            "EMBEDDING_QUERY_PREFIX",
            "Represent this sentence for searching relevant passages: "
        )
        self.version = version
        
        logger.info(f"[EMBEDDING] Initializing {self.provider_type} provider for {self.model_name}")
        
        if self.provider_type == "apigee":
            self.provider = ApigeeEmbeddingProvider(self.model_name)
        elif self.provider_type == "remote":
            self.provider = RemoteEmbeddingProvider(self.model_name)
        elif self.provider_type == "local":
            self.provider = LocalEmbeddingProvider(self.model_name, self.max_tokens)
        elif os.environ.get("EMBEDDING_API_URL"):
            # Fallback: use remote if API URL is set and provider type is unrecognized
            logger.info(f"[EMBEDDING] Unknown provider '{self.provider_type}', using remote (EMBEDDING_API_URL is set)")
            self.provider = RemoteEmbeddingProvider(self.model_name)
        else:
            self.provider = LocalEmbeddingProvider(self.model_name, self.max_tokens)
            
        self.embedding_dim = self.provider.get_embedding_dim()

    def generate_embeddings(
        self, chunks: list[CodeChunk], existing_hashes: set[str] | None = None
    ) -> list[tuple[CodeChunk, np.ndarray]]:
        """Generate embeddings for chunks."""
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
            
            try:
                batch_emb = self.provider.encode_batch(batch)
                all_embeddings.extend(batch_emb)
                
                # Log progress every 10 batches or if it's the last one
                if (i // self.batch_size) % 10 == 0 or (i + self.batch_size >= len(texts)):
                    logger.info(f"[EMBEDDING] Processed {min(i + self.batch_size, len(texts))}/{len(texts)} chunks ({(min(i + self.batch_size, len(texts))/len(texts))*100:.1f}%)")
            except Exception as e:
                logger.error(f"[EMBEDDING] Batch failed: {e}")
                raise

        return list(zip(new_chunks, all_embeddings, strict=False))

    def encode_query(self, query: str) -> list[float]:
        """Encode a search query with instruction prefix."""
        prefixed = f"{self.query_prefix}{query}"
        
        embeddings = self.provider.encode_batch([prefixed])
        if len(embeddings) == 0:
            return []
            
        return embeddings[0].tolist()

    def encode_document(self, text: str) -> list[float]:
        """Encode a document text (no query prefix)."""
        embeddings = self.provider.encode_batch([text])
        if len(embeddings) == 0:
            return []
            
        return embeddings[0].tolist()

    def get_embedding_dim(self) -> int:
        """Get embedding dimensionality."""
        return self.embedding_dim
