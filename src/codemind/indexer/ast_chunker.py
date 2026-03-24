"""
AST-aware code chunking.

Chunks code at function/class boundaries instead of arbitrary character offsets.
Falls back to character-based chunking for unsupported languages.
Includes contextual enrichment and token-aware sizing.
"""

import hashlib
import logging
from pathlib import Path

from .ast_extractor import ASTExtractor, ASTExtractionResult
from .chunker import CodeChunk

logger = logging.getLogger(__name__)


class ASTChunker:
    """Chunks code at AST boundaries (function/class level).
    
    Produces one chunk per function/class. Large symbols are further split.
    Non-symbol code (imports, module-level) gets a separate chunk.
    Falls back to character-based chunking for files without AST support.
    """

    def __init__(self, max_chunk_chars: int | None = None, overlap_lines: int = 2, min_chunk_chars: int = 50):
        """
        Initialize AST chunker.

        Args:
            max_chunk_chars: Max characters per chunk. If None, derived from
                             EMBEDDING_MAX_TOKENS env var (tokens × 4 chars/token).
                             Defaults to 2048 (512 tokens × 4).
            overlap_lines: Lines of overlap for split large symbols
            min_chunk_chars: Minimum characters for a chunk to be kept
        """
        import os
        if max_chunk_chars is None:
            max_tokens = int(os.getenv("EMBEDDING_MAX_TOKENS", "512"))
            max_chunk_chars = max_tokens * 4  # ~4 chars/token; token-verified trim-back handles dense outliers
        self.max_chunk_chars = max_chunk_chars
        self.max_tokens = int(os.getenv("EMBEDDING_MAX_TOKENS", "512"))
        self.overlap_lines = overlap_lines
        self.min_chunk_chars = min_chunk_chars
        self.ast_extractor = ASTExtractor()

        # Use shared token counter (tiktoken with fallback)
        from codemind.llm.token_counter import count_tokens as _count_tokens, CHARS_PER_TOKEN
        self._count_tokens_fn = _count_tokens
        self._chars_per_token = CHARS_PER_TOKEN

    # Languages where tree-sitter AST parsing is pointless (no functions/classes)
    # and can even hang (e.g., tree_sitter_markdown on large README files).
    # These skip straight to fast char-based chunking.
    _SKIP_AST_LANGUAGES = {"markdown", "json", "yaml", "toml", "html", "css", "sql", "hcl"}

    def chunk_file(self, file_path: Path | str, content: str | None = None) -> list[CodeChunk]:
        file_path = Path(file_path)
        """
        Chunk a file using AST boundaries when possible.

        Args:
            file_path: Path to file
            content: File content (if None, reads from file)

        Returns:
            List of AST-aware code chunks with context headers
        """
        if content is None:
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                return []

        if not content.strip():
            return []

        language = self.ast_extractor.detect_language(file_path)

        # Skip AST for non-code languages (markdown, JSON, etc.) — they have
        # no functions/classes and tree-sitter can hang on them (e.g., README.md)
        if not language or language in self._SKIP_AST_LANGUAGES:
            logger.debug("[CHUNKER] char-fallback (skip-AST lang): %s [%s]", file_path.name, language)
            return self._char_chunk(file_path, content, language=language)

        result = self.ast_extractor.extract(file_path, language)
        if not result.success or not result.symbols:
            # AST failed or no symbols — fall back
            logger.debug("[CHUNKER] char-fallback (no symbols): %s [%s]", file_path.name, language)
            return self._char_chunk(file_path, content, language=language)

        logger.debug("[CHUNKER] AST chunking: %s [%s] (%d symbols)", file_path.name, language, len(result.symbols))
        return self._ast_chunk(file_path, content, result.symbols, language, result)

    def _token_count(self, text: str) -> int:
        """Count tokens using shared tiktoken counter."""
        return self._count_tokens_fn(text)

    def _exceeds_limit_fast(self, text: str) -> bool:
        """Fast char-only check — O(1), no tiktoken.
        
        Use this in tight inner loops where calling tiktoken on every
        iteration would be O(n²). The char limit is a conservative
        approximation (~4 chars/token).
        """
        return len(text) > self.max_chunk_chars

    def _exceeds_limit(self, text: str) -> bool:
        """Full check with tiktoken validation.
        
        Uses BOTH token count and char-based limit to prevent mismatches
        between tiktoken's tokenization and the embedder's char-based
        truncation (max_tokens * 4 chars).
        Call this only at chunk boundaries, not inside tight loops.
        """
        # Always enforce the char-based cap to match embedder's truncation
        if len(text) > self.max_chunk_chars:
            return True
        return self._token_count(text) > self.max_tokens

    def _build_context_header(
        self, file_path: Path, sym, imports: list | None, language: str
    ) -> str:
        """Build a context header for embedding enrichment."""
        lines = []
        lines.append(f"# File: {file_path}")
        if sym:
            if sym.parent:
                lines.append(f"# Class: {sym.parent}")
            kind = sym.type.capitalize()
            sig = f"  [{sym.signature}]" if sym.signature else ""
            lines.append(f"# {kind}: {sym.name}{sig}")
            if sym.decorators:
                lines.append(f"# Decorators: {', '.join(sym.decorators)}")
            if sym.bases:
                lines.append(f"# Extends: {', '.join(sym.bases)}")
        if imports:
            import_names = [imp.module for imp in imports[:5]]
            lines.append(f"# Imports: {', '.join(import_names)}")
        if language:
            lines.append(f"# Language: {language}")
        lines.append("# ---")
        return "\n".join(lines)

    def _ast_chunk(
        self, file_path: Path, content: str,
        symbols: list, language: str,
        ast_result: ASTExtractionResult | None = None
    ) -> list[CodeChunk]:
        """Create chunks at symbol boundaries with context enrichment."""
        lines = content.split("\n")
        chunks: list[CodeChunk] = []
        covered_lines: set[int] = set()
        imports = ast_result.imports if ast_result else None

        # Build a map of parent class → symbols for context
        parent_map: dict[str, list] = {}
        for sym in symbols:
            if sym.parent:
                parent_map.setdefault(sym.parent, []).append(sym)

        # Sort symbols by start line
        sorted_symbols = sorted(symbols, key=lambda s: s.start_line)

        for sym in sorted_symbols:
            start = sym.start_line - 1  # 0-indexed
            end = sym.end_line  # exclusive
            if start < 0 or end > len(lines):
                continue

            symbol_lines = lines[start:end]
            symbol_text = "\n".join(symbol_lines)

            if len(symbol_text.strip()) < self.min_chunk_chars:
                # Skip tiny symbols (often malformed or empty)
                pass
            elif self._exceeds_limit(symbol_text):
                # Split large symbol
                sub_chunks = self._split_large_symbol(
                    file_path, symbol_text, start, sym, language
                )
                # Add context headers to sub-chunks (only if it fits)
                ctx = self._build_context_header(file_path, sym, imports, language)
                for sc in sub_chunks:
                    enriched = ctx + "\n" + sc.text
                    if not self._exceeds_limit(enriched):
                        sc.context_header = ctx
                        sc.text = enriched
                    else:
                        sc.context_header = ""  # Too large, skip header
                chunks.extend(sub_chunks)
            else:
                ctx = self._build_context_header(file_path, sym, imports, language)
                enriched_text = ctx + "\n" + symbol_text
                # If enriched text exceeds limit, use raw text instead
                if self._exceeds_limit(enriched_text):
                    enriched_text = symbol_text
                    ctx = ""
                chunk_hash = self._hash(symbol_text)  # Hash on raw code, not header
                chunks.append(CodeChunk(
                    text=enriched_text,
                    chunk_hash=chunk_hash,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    start_byte=sum(len(lines[i]) + 1 for i in range(start)),
                    end_byte=sum(len(lines[i]) + 1 for i in range(end)),
                    file_path=str(file_path),
                    symbol_name=sym.name,
                    symbol_type=sym.type,
                    language=language,
                    docstring=getattr(sym, 'docstring', None),
                    context_header=ctx,
                ))

            for i in range(start, end):
                covered_lines.add(i)

        # Collect uncovered lines (imports, module-level code)
        uncovered_ranges = self._find_uncovered_ranges(lines, covered_lines)
        for start_idx, end_idx in uncovered_ranges:
            text = "\n".join(lines[start_idx:end_idx])
            if len(text.strip()) < self.min_chunk_chars:
                continue
            if not self._exceeds_limit(text):
                ctx = self._build_context_header(file_path, None, imports, language)
                enriched_text = ctx + "\n" + text
                if self._exceeds_limit(enriched_text):
                    enriched_text = text
                    ctx = ""
                chunk_hash = self._hash(text)
                chunks.append(CodeChunk(
                    text=enriched_text,
                    chunk_hash=chunk_hash,
                    start_line=start_idx + 1,
                    end_line=end_idx,
                    start_byte=sum(len(lines[i]) + 1 for i in range(start_idx)),
                    end_byte=sum(len(lines[i]) + 1 for i in range(end_idx)),
                    file_path=str(file_path),
                    symbol_name=None,
                    symbol_type="module",
                    language=language,
                    context_header=ctx,
                ))
            else:
                # Module-level code exceeds max — split it
                sub_chunks = self._char_chunk_text(
                    file_path, text, base_line=start_idx, language=language
                )
                chunks.extend(sub_chunks)

        # Sort by line number
        chunks.sort(key=lambda c: c.start_line)
        return chunks

    # Symbols beyond this line count are capped to prevent pathological splits
    MAX_SYMBOL_LINES = 10_000

    def _split_large_symbol(
        self, file_path: Path, text: str,
        base_line: int, sym, language: str
    ) -> list[CodeChunk]:
        """Split a large symbol into smaller chunks.
        
        Optimized: uses fast char-length checks in the inner loop (O(n))
        instead of calling tiktoken on every iteration (was O(n²)).
        A single tiktoken validation pass trims the final boundary if needed.
        """
        lines = text.split("\n")
        chunks = []
        i = 0
        part = 0
        # Reserve ~20% budget for context header that will be prepended later
        header_budget = int(self.max_chunk_chars * 0.2)
        effective_max = self.max_chunk_chars - header_budget

        # Cap extremely large symbols to prevent runaway processing
        if len(lines) > self.MAX_SYMBOL_LINES:
            logger.warning(
                "[CHUNKER] Symbol %s has %d lines (cap=%d), truncating",
                sym.name, len(lines), self.MAX_SYMBOL_LINES
            )
            lines = lines[:self.MAX_SYMBOL_LINES]

        while i < len(lines):
            end = i + 1  # Always take at least one line
            char_count = len(lines[i])

            # Phase 1: Fast char-based growth — O(1) per line, no tiktoken
            while end < len(lines):
                added = len(lines[end]) + 1  # +1 for newline
                if char_count + added > effective_max:
                    break
                char_count += added
                end += 1

            # Phase 2: Token-verified trim-back — only fires for dense code
            chunk_text = "\n".join(lines[i:end])
            while end > i + 1 and self._token_count(chunk_text) > self.max_tokens:
                end -= 1
                chunk_text = "\n".join(lines[i:end])

            if len(chunk_text.strip()) >= self.min_chunk_chars:
                chunk_hash = self._hash(chunk_text)
                chunks.append(CodeChunk(
                    text=chunk_text,
                    chunk_hash=chunk_hash,
                    start_line=base_line + i + 1,
                    end_line=base_line + end,
                    start_byte=0,  # Approximate
                    end_byte=0,
                    file_path=str(file_path),
                    symbol_name=f"{sym.name}__part{part}",
                    symbol_type=sym.type,
                    language=language,
                ))

            i = max(i + 1, end - self.overlap_lines)
            part += 1

        return chunks

    def _find_uncovered_ranges(
        self, lines: list[str], covered: set[int]
    ) -> list[tuple[int, int]]:
        """Find contiguous ranges of uncovered lines."""
        ranges = []
        start = None

        for i in range(len(lines)):
            if i not in covered:
                if start is None:
                    start = i
            else:
                if start is not None:
                    ranges.append((start, i))
                    start = None

        if start is not None:
            ranges.append((start, len(lines)))

        return ranges

    def _char_chunk(
        self, file_path: Path, content: str, language: str | None
    ) -> list[CodeChunk]:
        """Fallback character-based chunking with fast char-based inner loop."""
        lines = content.split("\n")
        chunks = []
        i = 0

        while i < len(lines):
            end = i + 1
            char_count = len(lines[i])

            # Fast char-based growth
            while end < len(lines):
                added = len(lines[end]) + 1
                if char_count + added > self.max_chunk_chars:
                    break
                char_count += added
                end += 1

            chunk_text = "\n".join(lines[i:end])

            # Token-verified trim-back — only fires for dense code
            while end > i + 1 and self._token_count(chunk_text) > self.max_tokens:
                end -= 1
                chunk_text = "\n".join(lines[i:end])

            # Handle single lines that exceed max_chunk_chars — truncate
            if len(chunk_text) > self.max_chunk_chars and end == i + 1:
                chunk_text = chunk_text[:self.max_chunk_chars]

            if len(chunk_text.strip()) >= self.min_chunk_chars:
                chunk_hash = self._hash(chunk_text)
                chunks.append(CodeChunk(
                    text=chunk_text,
                    chunk_hash=chunk_hash,
                    start_line=i + 1,
                    end_line=end,
                    start_byte=sum(len(lines[j]) + 1 for j in range(i)),
                    end_byte=sum(len(lines[j]) + 1 for j in range(end)),
                    file_path=str(file_path),
                    symbol_name=None,
                    symbol_type=None,
                    language=language,
                ))

            # Advance — always move forward, overlap only when safe
            i = max(i + 1, end - 3) if end < len(lines) else end

        return chunks

    def _char_chunk_text(
        self, file_path: Path, text: str, base_line: int, language: str | None
    ) -> list[CodeChunk]:
        """Split a text block into chunks with fast char-based inner loop."""
        lines = text.split("\n")
        chunks = []
        i = 0

        while i < len(lines):
            end = i + 1
            char_count = len(lines[i])

            # Fast char-based growth
            while end < len(lines):
                added = len(lines[end]) + 1
                if char_count + added > self.max_chunk_chars:
                    break
                char_count += added
                end += 1

            chunk_text = "\n".join(lines[i:end])

            # Token-verified trim-back — only fires for dense code
            while end > i + 1 and self._token_count(chunk_text) > self.max_tokens:
                end -= 1
                chunk_text = "\n".join(lines[i:end])

            # Handle single lines that exceed max_chunk_chars — truncate
            if len(chunk_text) > self.max_chunk_chars and end == i + 1:
                chunk_text = chunk_text[:self.max_chunk_chars]

            if len(chunk_text.strip()) >= self.min_chunk_chars:
                chunk_hash = self._hash(chunk_text)
                chunks.append(CodeChunk(
                    text=chunk_text,
                    chunk_hash=chunk_hash,
                    start_line=base_line + i + 1,
                    end_line=base_line + end,
                    start_byte=0,  # Approximate
                    end_byte=0,
                    file_path=str(file_path),
                    symbol_name=None,
                    symbol_type="module",
                    language=language,
                ))

            # Advance — always move forward, overlap only when safe
            i = max(i + 1, end - 3) if end < len(lines) else end

        return chunks

    def _hash(self, text: str) -> str:
        """Compute deterministic hash."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
