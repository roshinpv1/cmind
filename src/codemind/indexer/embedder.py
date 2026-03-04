"""
Incremental embedding generation.

Supports:
- Local: SentenceTransformers (sequential GPU batching)
- Remote: OpenAI-compatible HTTP API (parallel batches via asyncio)
- Apigee: Enterprise embedding API (parallel batches via asyncio)

Parallelism is at the *batch* level for HTTP providers.
Control concurrency with EMBEDDING_MAX_CONCURRENT (default 4).
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

    # Subclasses set this to True if they implement encode_batch_async()
    supports_async: bool = False

    def _truncate_texts(self, texts: List[str], max_tokens: int) -> List[str]:
        """Truncate texts to approximate max_tokens length (safety net).

        The chunker should normally produce texts within limits.
        This is a last-resort safety net for API-based providers
        to prevent token-limit errors. Uses ~4 chars/token estimate.
        """
        char_limit = max_tokens * 4
        result = []
        for t in texts:
            if len(t) > char_limit:
                logger.warning(f"[EMBEDDING] Truncating text from {len(t)} to {char_limit} chars "
                               f"(max_tokens={max_tokens}). Consider adjusting chunker settings.")
                result.append(t[:char_limit])
            else:
                result.append(t)
        return result

    @abstractmethod
    def get_embedding_dim(self) -> int:
        pass

    @abstractmethod
    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        pass

    async def encode_batch_async(self, texts: List[str]) -> List[np.ndarray]:
        """Async version of encode_batch. Default falls back to sync in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.encode_batch, texts)


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local embedding using SentenceTransformers.

    GPU-level parallelism is handled internally by PyTorch — no async needed.
    """

    supports_async = False  # Sequential batching is optimal for GPU

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
    """Apigee-hosted embedding API — supports parallel async batches."""

    supports_async = True

    def __init__(self, model_name: str, max_tokens: int = 512):
        if not ApigeeTokenManager:
            raise ImportError("ApigeeTokenManager not available")

        self.model_name = model_name
        self.max_tokens = max_tokens
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
        logger.info(f"[EMBEDDING] Initialized Apigee provider for model {model_name} "
                    f"(max_tokens={max_tokens}, dim={self.embedding_dim})")

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

        # Truncate to max_tokens and replace newlines
        truncated = self._truncate_texts(texts, self.max_tokens)
        cleaned_texts = [t.replace("\n", " ") for t in truncated]

        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json={"model": self.model_name, "input": cleaned_texts}
            )
            response.raise_for_status()
            data = response.json()

            results = sorted(data["data"], key=lambda x: x["index"])
            return [np.array(item["embedding"]) for item in results]

    async def encode_batch_async(self, texts: List[str]) -> List[np.ndarray]:
        """Async batch encoding — uses native async HTTP."""
        return await self._get_embeddings_async(texts)

    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        """Synchronous wrapper for async call."""
        try:
            return asyncio.run(self._get_embeddings_async(texts))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._get_embeddings_async(texts))
            finally:
                loop.close()


class RemoteEmbeddingProvider(EmbeddingProvider):
    """Remote embedding API (OpenAI-compatible) — supports parallel async batches."""

    supports_async = True

    def __init__(self, model_name: str, max_tokens: int = 512):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.base_url = os.environ.get("EMBEDDING_API_URL")
        self.api_key = os.environ.get("EMBEDDING_API_KEY")

        if not self.base_url:
            raise ValueError("EMBEDDING_API_URL must be set for remote provider")

        # Auto-detect dimension via probe request, fall back to EMBEDDING_DIMENSION env
        self.embedding_dim = self._detect_dimension()
        logger.info(f"[EMBEDDING] Initialized Remote provider for model {model_name} "
                    f"at {self.base_url} (max_tokens={max_tokens}, dim={self.embedding_dim})")

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

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def encode_batch_async(self, texts: List[str]) -> List[np.ndarray]:
        """Async batch encoding using httpx.AsyncClient — enables parallel calls."""
        truncated = self._truncate_texts(texts, self.max_tokens)
        cleaned_texts = [t.replace("\n", " ") for t in truncated]

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers=self._build_headers(),
                json={"model": self.model_name, "input": cleaned_texts},
            )
            response.raise_for_status()
            data = response.json()
            results = sorted(data["data"], key=lambda x: x["index"])
            return [np.array(item["embedding"]) for item in results]

    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        """Synchronous embedding call using httpx.Client (works in both sync and async contexts)."""
        truncated = self._truncate_texts(texts, self.max_tokens)
        cleaned_texts = [t.replace("\n", " ") for t in truncated]

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{self.base_url}/embeddings",
                headers=self._build_headers(),
                json={"model": self.model_name, "input": cleaned_texts},
            )
            response.raise_for_status()
            data = response.json()
            results = sorted(data["data"], key=lambda x: x["index"])
            return [np.array(item["embedding"]) for item in results]


class EmbeddingGenerator:
    """Generates embeddings using configurable provider (Local, Remote, or Apigee).

    For HTTP-based providers (Remote, Apigee), batches are dispatched in parallel
    using asyncio, controlled by EMBEDDING_MAX_CONCURRENT (default 4).
    For the local SentenceTransformers provider, sequential GPU batching is used.
    """

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
        self.max_concurrent = int(os.getenv("EMBEDDING_MAX_CONCURRENT", "4"))
        self.query_prefix = query_prefix or os.getenv(
            "EMBEDDING_QUERY_PREFIX",
            "Represent this sentence for searching relevant passages: "
        )
        self.version = version

        logger.info(f"[EMBEDDING] Initializing {self.provider_type} provider for {self.model_name}")

        if self.provider_type == "apigee":
            self.provider = ApigeeEmbeddingProvider(self.model_name, self.max_tokens)
        elif self.provider_type == "remote":
            self.provider = RemoteEmbeddingProvider(self.model_name, self.max_tokens)
        elif self.provider_type == "local":
            self.provider = LocalEmbeddingProvider(self.model_name, self.max_tokens)
        elif os.environ.get("EMBEDDING_API_URL"):
            # Fallback: use remote if API URL is set and provider type is unrecognized
            logger.info(f"[EMBEDDING] Unknown provider '{self.provider_type}', using remote (EMBEDDING_API_URL is set)")
            self.provider = RemoteEmbeddingProvider(self.model_name, self.max_tokens)
        else:
            self.provider = LocalEmbeddingProvider(self.model_name, self.max_tokens)

        self.embedding_dim = self.provider.get_embedding_dim()

    # ------------------------------------------------------------------
    # Parallel async dispatch (Remote / Apigee only)
    # ------------------------------------------------------------------

    async def _embed_all_async(
        self, batches: List[List[str]]
    ) -> List[List[np.ndarray]]:
        """Dispatch all batches concurrently capped at self.max_concurrent.

        Returns a list of per-batch embedding lists, in original batch order.
        """
        sem = asyncio.Semaphore(self.max_concurrent)

        async def embed_one(batch: List[str], idx: int):
            async with sem:
                logger.debug(f"[EMBEDDING] Dispatching batch {idx + 1}/{len(batches)}")
                return idx, await self.provider.encode_batch_async(batch)

        tasks = [embed_one(batch, i) for i, batch in enumerate(batches)]
        results = await asyncio.gather(*tasks)
        # Re-sort by original index to maintain chunk order
        return [emb for _, emb in sorted(results, key=lambda r: r[0])]

    def _run_parallel(self, batches: List[List[str]]) -> List[np.ndarray]:
        """Run parallel async embedding in a new event loop (safe from worker thread)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an existing event loop (e.g. uvicorn) — run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._embed_all_async(batches))
                batch_results = future.result()
        else:
            batch_results = asyncio.run(self._embed_all_async(batches))

        # Flatten batch results back to a single list of embeddings
        all_embeddings: List[np.ndarray] = []
        for batch_embs in batch_results:
            all_embeddings.extend(batch_embs)
        return all_embeddings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_embeddings(
        self, chunks: list[CodeChunk], existing_hashes: set[str] | None = None
    ) -> list[tuple[CodeChunk, np.ndarray]]:
        """Generate embeddings for chunks, skipping already-indexed ones.

        Uses parallel async dispatch for HTTP providers (Remote / Apigee).
        Uses sequential GPU batching for the local SentenceTransformers provider.
        """
        if existing_hashes is None:
            existing_hashes = set()

        new_chunks = [c for c in chunks if c.chunk_hash not in existing_hashes]
        if not new_chunks:
            return []

        texts = [chunk.text for chunk in new_chunks]

        # Split into batches
        batches = [
            texts[i: i + self.batch_size]
            for i in range(0, len(texts), self.batch_size)
        ]

        if self.provider.supports_async and len(batches) > 1:
            # --- Parallel path (Remote / Apigee) ---
            logger.info(
                f"[EMBEDDING] Dispatching {len(batches)} batches in parallel "
                f"(concurrency={self.max_concurrent}, batch_size={self.batch_size})"
            )
            all_embeddings = self._run_parallel(batches)
            logger.info(f"[EMBEDDING] ✅ Parallel embedding complete: {len(all_embeddings)} embeddings")
        else:
            # --- Sequential path (Local / single batch) ---
            all_embeddings = []
            for i, batch in enumerate(batches):
                try:
                    batch_emb = self.provider.encode_batch(batch)
                    all_embeddings.extend(batch_emb)
                    if i % 10 == 0 or i == len(batches) - 1:
                        done = min((i + 1) * self.batch_size, len(texts))
                        logger.info(
                            f"[EMBEDDING] Processed {done}/{len(texts)} chunks "
                            f"({done / len(texts) * 100:.1f}%)"
                        )
                except Exception as e:
                    logger.error(f"[EMBEDDING] Batch {i + 1} failed: {e}")
                    raise

        return list(zip(new_chunks, all_embeddings, strict=False))

    def encode_query(self, query: str) -> list[float]:
        """Encode a search query with instruction prefix."""
        prefixed = f"{self.query_prefix}{query}"
        embeddings = self.provider.encode_batch([prefixed])
        if not embeddings:
            return []
        return embeddings[0].tolist()

    def encode_document(self, text: str) -> list[float]:
        """Encode a document text (no query prefix)."""
        embeddings = self.provider.encode_batch([text])
        if not embeddings:
            return []
        return embeddings[0].tolist()

    def get_embedding_dim(self) -> int:
        """Get embedding dimensionality."""
        return self.embedding_dim
