"""
Embedding generation for code indexing and search.

Supports three providers, all configured via environment variables:
  - local   : SentenceTransformers (GPU/MPS/CPU)
  - remote  : Any OpenAI-compatible HTTP endpoint
  - apigee  : Enterprise Apigee-hosted embedding API

Environment variables (all optional, sensible defaults):
  EMBEDDING_PROVIDER        local | remote | apigee  (default: local)
  EMBEDDING_MODEL           Model name/path          (default: BAAI/bge-base-en-v1.5)
  EMBEDDING_MAX_TOKENS      Max tokens per text      (default: 512)
  EMBEDDING_BATCH_SIZE      Texts per batch           (default: 32)
  EMBEDDING_MAX_CONCURRENT  Parallel HTTP batches     (default: 4)
  EMBEDDING_DIMENSION       Embedding dim override    (default: auto-detect)
  EMBEDDING_QUERY_PREFIX    Query instruction prefix
  EMBEDDING_API_URL         Remote endpoint URL
  EMBEDDING_API_KEY         Remote API key
"""

import asyncio
import datetime
import logging
import os
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import List

import httpx
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

try:
    from codemind.llm.token_manager import ApigeeTokenManager
except ImportError:
    ApigeeTokenManager = None

from .chunker import CodeChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4  # conservative estimate, same as ASTChunker


def _truncate_text(text: str, max_tokens: int) -> str:
    """Truncate a text to fit within max_tokens (safety net).

    The upstream ASTChunker should already size chunks correctly.
    This is a last-resort guard against token-limit errors.
    """
    limit = max_tokens * _CHARS_PER_TOKEN
    if len(text) > limit:
        logger.warning(
            f"[EMBEDDING] Truncating text from {len(text)} to {limit} chars "
            f"(max_tokens={max_tokens})"
        )
        return text[:limit]
    return text


def _clean_texts(texts: List[str], max_tokens: int) -> List[str]:
    """Truncate and normalise whitespace for API-based providers."""
    return [_truncate_text(t, max_tokens).replace("\n", " ") for t in texts]


def _run_async(coro):
    """Run an async coroutine from sync context, handling existing event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _parse_openai_response(data: dict) -> List[np.ndarray]:
    """Extract embeddings from an OpenAI-format response."""
    results = sorted(data["data"], key=lambda x: x["index"])
    return [np.array(item["embedding"]) for item in results]


_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # seconds, doubles each retry


async def _post_with_retry(
    post_fn,
    texts: List[str],
    retries: int = _MAX_RETRIES,
) -> List[np.ndarray]:
    """Call post_fn(texts) with retry logic.

    - 400 Bad Request → split batch in half and retry each half (payload too large)
    - 429 / 5xx       → exponential backoff then retry
    - Other errors     → raise immediately
    """
    try:
        return await post_fn(texts)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code

        # --- 400: batch too large → halve and retry ---
        if status == 400 and len(texts) > 1:
            mid = len(texts) // 2
            logger.warning(
                f"[EMBEDDING] 400 Bad Request on batch of {len(texts)} — "
                f"splitting into {mid} + {len(texts) - mid} and retrying"
            )
            left = await _post_with_retry(post_fn, texts[:mid], retries)
            right = await _post_with_retry(post_fn, texts[mid:], retries)
            return left + right

        # --- 429 / 5xx: transient → backoff ---
        if (status == 429 or status >= 500) and retries > 0:
            wait = _RETRY_BACKOFF * (_MAX_RETRIES - retries + 1)
            logger.warning(
                f"[EMBEDDING] {status} error — retrying in {wait:.0f}s "
                f"({retries} retries left)"
            )
            await asyncio.sleep(wait)
            return await _post_with_retry(post_fn, texts, retries - 1)

        raise  # unrecoverable


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class EmbeddingProvider(ABC):
    """Abstract base for all embedding providers."""

    supports_async: bool = False

    @abstractmethod
    def get_embedding_dim(self) -> int:
        ...

    @abstractmethod
    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        ...

    async def encode_batch_async(self, texts: List[str]) -> List[np.ndarray]:
        """Default: run sync encode_batch in a thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.encode_batch, texts)


# ---------------------------------------------------------------------------
# Local (SentenceTransformers)
# ---------------------------------------------------------------------------


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local embedding using SentenceTransformers on GPU/MPS/CPU."""

    supports_async = False

    def __init__(self, model_name: str, max_tokens: int, batch_size: int):
        if not HAS_SENTENCE_TRANSFORMERS:
            raise ImportError(
                "sentence-transformers is required for local embeddings"
            )

        import torch
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

        logger.info(f"[EMBEDDING] Loading local model: {model_name} on {device}")
        self.model = SentenceTransformer(model_name, device=device)
        self.model.max_seq_length = max_tokens
        self.batch_size = batch_size
        self.embedding_dim = self.model.get_sentence_embedding_dimension()

    def get_embedding_dim(self) -> int:
        return self.embedding_dim

    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        return self.model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )


# ---------------------------------------------------------------------------
# Remote (OpenAI-compatible)
# ---------------------------------------------------------------------------


class RemoteEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding API (Ollama, vLLM, OpenAI, etc.)."""

    supports_async = True

    def __init__(self, model_name: str, max_tokens: int):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.base_url = os.environ.get("EMBEDDING_API_URL")
        self.api_key = os.environ.get("EMBEDDING_API_KEY")

        if not self.base_url:
            raise ValueError("EMBEDDING_API_URL must be set for remote provider")

        self.embedding_dim = self._detect_dimension()
        logger.info(
            f"[EMBEDDING] Remote provider: {model_name} at {self.base_url} "
            f"(max_tokens={max_tokens}, dim={self.embedding_dim})"
        )

    def _detect_dimension(self) -> int:
        """Probe the remote API for embedding dimension."""
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers(),
                    json={"model": self.model_name, "input": "dim probe"},
                )
                resp.raise_for_status()
                dim = len(resp.json()["data"][0]["embedding"])
                logger.info(f"[EMBEDDING] Auto-detected dim: {dim}")
                return dim
        except Exception as e:
            fallback = int(os.getenv("EMBEDDING_DIMENSION", "768"))
            logger.warning(f"[EMBEDDING] Dim detection failed ({e}), using {fallback}")
            return fallback

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def get_embedding_dim(self) -> int:
        return self.embedding_dim

    async def _async_post(self, cleaned_texts: List[str]) -> List[np.ndarray]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json={"model": self.model_name, "input": cleaned_texts},
            )
            resp.raise_for_status()
            return _parse_openai_response(resp.json())

    def _sync_post(self, cleaned_texts: List[str]) -> List[np.ndarray]:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json={"model": self.model_name, "input": cleaned_texts},
            )
            response.raise_for_status()
            data = response.json()
            results = sorted(data["data"], key=lambda x: x["index"])
            return [np.array(item["embedding"]) for item in results]

    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        cleaned = _clean_texts(texts, self.max_tokens)
        return _run_async(_post_with_retry(self._async_post, cleaned))

    async def encode_batch_async(self, texts: List[str]) -> List[np.ndarray]:
        cleaned = _clean_texts(texts, self.max_tokens)
        return await _post_with_retry(self._async_post, cleaned)


# ---------------------------------------------------------------------------
# Apigee (Enterprise)
# ---------------------------------------------------------------------------


class ApigeeEmbeddingProvider(EmbeddingProvider):
    """Enterprise Apigee-hosted embedding API."""

    supports_async = True

    def __init__(self, model_name: str, max_tokens: int):
        if not ApigeeTokenManager:
            raise ImportError("ApigeeTokenManager not available")

        self.model_name = model_name
        self.max_tokens = max_tokens
        self.token_manager = ApigeeTokenManager()
        self.base_url = os.environ.get("ENTERPRISE_BASE_URL")
        self.wf_client_id = os.environ.get("WF_CLIENT_ID")
        self.wf_api_key = os.environ.get("WF_API_KEY")
        self.wf_use_case_id = os.environ.get("WF_USE_CASE_ID")

        if not all([self.base_url, self.wf_client_id,
                    self.wf_api_key, self.wf_use_case_id]):
            logger.warning("[EMBEDDING] Apigee configuration incomplete. Check env vars.")

        self.embedding_dim = int(os.getenv("EMBEDDING_DIMENSION", "768"))
        logger.info(
            f"[EMBEDDING] Apigee provider: {model_name} "
            f"(max_tokens={max_tokens}, dim={self.embedding_dim})"
        )

    def _headers(self, token: str) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "x-wf-request-date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "x-request-id": str(uuid.uuid4()),
            "x-correlation-id": str(uuid.uuid4()),
            "X-WF-client-id": self.wf_client_id,
            "X-WF-api-key": self.wf_api_key,
            "X-WF-usecase-id": self.wf_use_case_id,
        }

    def get_embedding_dim(self) -> int:
        return self.embedding_dim

    async def _post_async(self, cleaned: List[str]) -> List[np.ndarray]:
        token = await self.token_manager.get_token()
        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/embeddings",
                headers=self._headers(token),
                json={"model": self.model_name, "input": cleaned},
            )
            resp.raise_for_status()
            return _parse_openai_response(resp.json())

    async def encode_batch_async(self, texts: List[str]) -> List[np.ndarray]:
        cleaned = _clean_texts(texts, self.max_tokens)
        return await _post_with_retry(self._post_async, cleaned)

    def encode_batch(self, texts: List[str]) -> List[np.ndarray]:
        cleaned = _clean_texts(texts, self.max_tokens)
        return _run_async(_post_with_retry(self._post_async, cleaned))


# ---------------------------------------------------------------------------
# Generator (public API)
# ---------------------------------------------------------------------------


class EmbeddingGenerator:
    """High-level API for generating embeddings.

    Reads all config from env vars:
        EMBEDDING_PROVIDER, EMBEDDING_MODEL, EMBEDDING_MAX_TOKENS,
        EMBEDDING_BATCH_SIZE, EMBEDDING_MAX_CONCURRENT, EMBEDDING_QUERY_PREFIX

    Automatically selects parallel dispatch for HTTP providers.
    """

    def __init__(
        self,
        model_name: str | None = None,
        max_tokens: int | None = None,
        batch_size: int | None = None,
        query_prefix: str | None = None,
    ):
        self.provider_type = os.getenv("EMBEDDING_PROVIDER", "local").lower()
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
        self.max_tokens = max_tokens or int(os.getenv("EMBEDDING_MAX_TOKENS", "512"))
        self.batch_size = batch_size or int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
        self.max_concurrent = int(os.getenv("EMBEDDING_MAX_CONCURRENT", "4"))
        self.query_prefix = query_prefix or os.getenv(
            "EMBEDDING_QUERY_PREFIX",
            "Represent this sentence for searching relevant passages: ",
        )

        logger.info(
            f"[EMBEDDING] Initializing provider={self.provider_type} "
            f"model={self.model_name} max_tokens={self.max_tokens} "
            f"batch_size={self.batch_size} max_concurrent={self.max_concurrent}"
        )

        self.provider = self._create_provider()
        self.embedding_dim = self.provider.get_embedding_dim()

    def _create_provider(self) -> EmbeddingProvider:
        if self.provider_type == "apigee":
            return ApigeeEmbeddingProvider(self.model_name, self.max_tokens)
        if self.provider_type == "remote":
            return RemoteEmbeddingProvider(self.model_name, self.max_tokens)
        if self.provider_type == "local":
            return LocalEmbeddingProvider(self.model_name, self.max_tokens, self.batch_size)
        # Auto-detect: if EMBEDDING_API_URL is set, use remote; else local
        if os.environ.get("EMBEDDING_API_URL"):
            logger.info(f"[EMBEDDING] Unknown provider '{self.provider_type}', using remote")
            return RemoteEmbeddingProvider(self.model_name, self.max_tokens)
        return LocalEmbeddingProvider(self.model_name, self.max_tokens, self.batch_size)

    # ------------------------------------------------------------------
    # Batch embedding
    # ------------------------------------------------------------------

    def generate_embeddings(
        self, chunks: list[CodeChunk], existing_hashes: set[str] | None = None
    ) -> list[tuple[CodeChunk, np.ndarray]]:
        """Generate embeddings for new chunks (skips already-indexed ones).

        For HTTP providers: dispatches batches in parallel (up to max_concurrent).
        For local provider: processes batches sequentially (GPU handles parallelism).
        """
        if existing_hashes is None:
            existing_hashes = set()

        new_chunks = [c for c in chunks if c.chunk_hash not in existing_hashes]
        if not new_chunks:
            return []

        texts = [c.text for c in new_chunks]
        batches = [
            texts[i: i + self.batch_size]
            for i in range(0, len(texts), self.batch_size)
        ]

        logger.info(
            f"[EMBEDDING] Embedding {len(texts)} chunks in {len(batches)} batches"
        )

        if self.provider.supports_async and len(batches) > 1:
            all_embeddings = self._parallel_embed(batches)
        else:
            all_embeddings = self._sequential_embed(batches, len(texts))

        logger.info(f"[EMBEDDING] ✅ Done: {len(all_embeddings)} embeddings generated")
        return list(zip(new_chunks, all_embeddings, strict=False))

    def _sequential_embed(
        self, batches: List[List[str]], total: int
    ) -> List[np.ndarray]:
        """Process batches one at a time."""
        all_embeddings: List[np.ndarray] = []
        for i, batch in enumerate(batches):
            embs = self.provider.encode_batch(batch)
            all_embeddings.extend(embs)
            if i % 10 == 0 or i == len(batches) - 1:
                done = len(all_embeddings)
                logger.info(f"[EMBEDDING] {done}/{total} ({done / total * 100:.0f}%)")
        return all_embeddings

    def _parallel_embed(self, batches: List[List[str]]) -> List[np.ndarray]:
        """Dispatch batches concurrently, capped by max_concurrent."""
        logger.info(
            f"[EMBEDDING] Parallel dispatch: {len(batches)} batches, "
            f"concurrency={self.max_concurrent}"
        )

        async def _run():
            sem = asyncio.Semaphore(self.max_concurrent)

            async def _one(idx: int, batch: List[str]):
                async with sem:
                    return idx, await self.provider.encode_batch_async(batch)

            results = await asyncio.gather(
                *[_one(i, b) for i, b in enumerate(batches)]
            )
            # Re-assemble in original order
            ordered = [embs for _, embs in sorted(results, key=lambda r: r[0])]
            flat: List[np.ndarray] = []
            for batch_embs in ordered:
                flat.extend(batch_embs)
            return flat

        return _run_async(_run())

    # ------------------------------------------------------------------
    # Single-text encoding (search queries & documents)
    # ------------------------------------------------------------------

    def encode_query(self, query: str) -> list[float]:
        """Encode a search query with the instruction prefix."""
        prefixed = f"{self.query_prefix}{query}"
        embs = self.provider.encode_batch([prefixed])
        return embs[0].tolist() if len(embs) > 0 else []

    def encode_document(self, text: str) -> list[float]:
        """Encode a document text (no prefix)."""
        embs = self.provider.encode_batch([text])
        return embs[0].tolist() if len(embs) > 0 else []

    def get_embedding_dim(self) -> int:
        return self.embedding_dim
