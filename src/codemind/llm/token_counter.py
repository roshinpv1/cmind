"""
Centralized token counting using tiktoken.

Provides accurate token counting for all LLM interactions across the system.
Replaces scattered character-based approximations (len//3, len//4, etc.)
with a single, consistent tokenizer.

Usage:
    from codemind.llm.token_counter import count_tokens, truncate_to_tokens

    tokens = count_tokens("Hello world")
    short = truncate_to_tokens(long_text, max_tokens=4096)
"""

import os
import logging

logger = logging.getLogger(__name__)

# ── Singleton Encoder ─────────────────────────────────────────────────────────

_encoder = None
_encoder_initialized = False


def _get_encoder():
    """Lazy-load tiktoken encoder (singleton)."""
    global _encoder, _encoder_initialized
    if _encoder_initialized:
        return _encoder
    _encoder_initialized = True
    try:
        import tiktoken
        encoding_name = os.environ.get("TIKTOKEN_ENCODING", "cl100k_base")
        _encoder = tiktoken.get_encoding(encoding_name)
        logger.info(f"[TOKEN] Initialized tiktoken encoder: {encoding_name}")
    except ImportError:
        logger.warning(
            "[TOKEN] tiktoken not installed — falling back to character-based estimation. "
            "Install with: pip install tiktoken"
        )
        _encoder = None
    except Exception as e:
        logger.warning(f"[TOKEN] Failed to load tiktoken: {e} — using fallback")
        _encoder = None
    return _encoder


# ── Fallback ratio ────────────────────────────────────────────────────────────
# When tiktoken is unavailable, use 4 chars per token (conservative for code).
# This is the ONLY place this ratio is defined — all other code imports from here.
CHARS_PER_TOKEN = 4


# ── Public API ────────────────────────────────────────────────────────────────

def count_tokens(text: str) -> int:
    """Count the number of tokens in text.

    Uses tiktoken for accuracy, falls back to len(text) // CHARS_PER_TOKEN.

    Args:
        text: Input text to tokenize

    Returns:
        Token count (exact with tiktoken, estimated without)
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return len(text) // CHARS_PER_TOKEN


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to fit within max_tokens.

    Uses tiktoken for accurate truncation. With fallback, uses char estimation.

    Args:
        text: Input text
        max_tokens: Maximum token count

    Returns:
        Truncated text that fits within max_tokens
    """
    if not text or max_tokens <= 0:
        return ""
    enc = _get_encoder()
    if enc is not None:
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens])
    # Fallback: char-based
    char_limit = max_tokens * CHARS_PER_TOKEN
    if len(text) <= char_limit:
        return text
    return text[:char_limit]


def tokens_to_chars(n_tokens: int) -> int:
    """Convert a token count to an approximate character count.

    Useful for quick pre-checks to avoid calling count_tokens on huge texts
    that are obviously within budget.

    Args:
        n_tokens: Number of tokens

    Returns:
        Approximate character count
    """
    return n_tokens * CHARS_PER_TOKEN


def get_context_window() -> int:
    """Get the effective context window size from config.

    Reads LLM_CONTEXT_WINDOW env var. If not set, defaults to LLM_MAX_TOKENS * 4
    (conservative heuristic — most models have context >> output limit).

    Returns:
        Context window size in tokens
    """
    explicit = os.environ.get("LLM_CONTEXT_WINDOW")
    if explicit:
        return int(explicit)
    max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "30000"))
    return max_tokens * 4


def get_max_output_tokens() -> int:
    """Get the max output tokens from config.

    Returns:
        Max output tokens
    """
    return int(os.environ.get("LLM_MAX_TOKENS", "30000"))
