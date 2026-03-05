"""
AST-aware code chunking.

Chunks code at function/class boundaries instead of arbitrary character offsets.
Falls back to character-based chunking for unsupported languages.
"""

import hashlib
from pathlib import Path

from .ast_extractor import ASTExtractor
from .chunker import CodeChunk


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
            max_chunk_chars = max_tokens * 4  # ~4 chars per token
        self.max_chunk_chars = max_chunk_chars
        self.overlap_lines = overlap_lines
        self.min_chunk_chars = min_chunk_chars
        self.ast_extractor = ASTExtractor()

    # Max file size to chunk (500 KB) — very large files stall the pipeline
    MAX_CHUNK_FILE_SIZE = 500 * 1024

    def chunk_file(self, file_path: Path | str, content: str | None = None) -> list[CodeChunk]:
        file_path = Path(file_path)
        """
        Chunk a file using AST boundaries when possible.

        Args:
            file_path: Path to file
            content: File content (if None, reads from file)

        Returns:
            List of AST-aware code chunks
        """
        if content is None:
            try:
                # Skip very large files early
                size = file_path.stat().st_size
                if size > self.MAX_CHUNK_FILE_SIZE:
                    return []
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                return []

        if not content.strip():
            return []

        language = self.ast_extractor.detect_language(file_path)
        if not language:
            # Unsupported language — fall back to character-based
            return self._char_chunk(file_path, content, language=None)

        result = self.ast_extractor.extract(file_path, language)
        if not result.success or not result.symbols:
            # AST failed or no symbols — fall back
            return self._char_chunk(file_path, content, language=language)

        return self._ast_chunk(file_path, content, result.symbols, language)

    def _ast_chunk(
        self, file_path: Path, content: str,
        symbols: list, language: str
    ) -> list[CodeChunk]:
        """Create chunks at symbol boundaries."""
        lines = content.split("\n")
        chunks: list[CodeChunk] = []
        covered_lines: set[int] = set()

        # Pre-compute cumulative byte offsets O(n) instead of O(n²)
        byte_offsets = [0] * (len(lines) + 1)
        for i, line in enumerate(lines):
            byte_offsets[i + 1] = byte_offsets[i] + len(line) + 1

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
            elif len(symbol_text) > self.max_chunk_chars:
                # Split large symbol
                sub_chunks = self._split_large_symbol(
                    file_path, symbol_text, start, sym, language
                )
                chunks.extend(sub_chunks)
            else:
                chunk_hash = self._hash(symbol_text)
                chunks.append(CodeChunk(
                    text=symbol_text,
                    chunk_hash=chunk_hash,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    start_byte=byte_offsets[start],
                    end_byte=byte_offsets[min(end, len(lines))],
                    file_path=str(file_path),
                    symbol_name=sym.name,
                    symbol_type=sym.type,
                    language=language,
                ))

            for i in range(start, end):
                covered_lines.add(i)

        # Collect uncovered lines (imports, module-level code)
        uncovered_ranges = self._find_uncovered_ranges(lines, covered_lines)
        for start_idx, end_idx in uncovered_ranges:
            text = "\n".join(lines[start_idx:end_idx])
            if len(text.strip()) < self.min_chunk_chars:
                continue
            if len(text) <= self.max_chunk_chars:
                chunk_hash = self._hash(text)
                chunks.append(CodeChunk(
                    text=text,
                    chunk_hash=chunk_hash,
                    start_line=start_idx + 1,
                    end_line=end_idx,
                    start_byte=byte_offsets[start_idx],
                    end_byte=byte_offsets[min(end_idx, len(lines))],
                    file_path=str(file_path),
                    symbol_name=None,
                    symbol_type="module",
                    language=language,
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

    def _split_large_symbol(
        self, file_path: Path, text: str,
        base_line: int, sym, language: str
    ) -> list[CodeChunk]:
        """Split a large symbol into smaller chunks."""
        lines = text.split("\n")
        chunks = []
        i = 0
        part = 0

        while i < len(lines):
            end = i
            char_count = 0
            while end < len(lines) and char_count < self.max_chunk_chars:
                char_count += len(lines[end]) + 1
                end += 1

            chunk_lines = lines[i:end]
            chunk_text = "\n".join(chunk_lines)
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

    def _char_chunk_text(
        self, file_path: Path, text: str, base_line: int = 0,
        language: str | None = None
    ) -> list[CodeChunk]:
        """Split text into chunks that each fit within max_chunk_chars."""
        lines = text.split("\n")
        chunks = []
        i = 0

        while i < len(lines):
            # Greedily collect lines until hitting max_chunk_chars
            end = i
            char_count = 0
            while end < len(lines):
                line_len = len(lines[end]) + 1  # +1 for newline
                if char_count + line_len > self.max_chunk_chars and end > i:
                    break
                char_count += line_len
                end += 1

            chunk_text = "\n".join(lines[i:end])
            if len(chunk_text.strip()) >= self.min_chunk_chars:
                chunk_hash = self._hash(chunk_text)
                chunks.append(CodeChunk(
                    text=chunk_text,
                    chunk_hash=chunk_hash,
                    start_line=base_line + i + 1,
                    end_line=base_line + end,
                    start_byte=0,
                    end_byte=0,
                    file_path=str(file_path),
                    symbol_name=None,
                    symbol_type=None,
                    language=language,
                ))

            # Advance with overlap
            i = end - self.overlap_lines if end < len(lines) else end

        return chunks

    def _char_chunk(
        self, file_path: Path, content: str, language: str | None
    ) -> list[CodeChunk]:
        """Fallback character-based chunking."""
        return self._char_chunk_text(file_path, content, base_line=0, language=language)

    def _hash(self, text: str) -> str:
        """Compute deterministic hash."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
