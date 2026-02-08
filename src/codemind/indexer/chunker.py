"""
Deterministic code chunking.

Splits code into overlapping chunks with stable hashes.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CodeChunk:
    """Represents a chunk of code."""

    text: str
    chunk_hash: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    file_path: str
    symbol_name: str | None = None   # Function/class name if AST-derived
    symbol_type: str | None = None   # "function", "class", "method"
    language: str | None = None       # Programming language


class CodeChunker:
    """Chunks code files into overlapping segments."""

    def __init__(self, chunk_size: int = 512, overlap: int = 128):
        """
        Initialize chunker.

        Args:
            chunk_size: Target chunk size in characters
            overlap: Overlap between chunks in characters
        """
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_file(self, file_path: Path, content: str | None = None) -> list[CodeChunk]:
        """
        Chunk a code file.

        Args:
            file_path: Path to file
            content: File content (if None, reads from file)

        Returns:
            List of code chunks
        """
        if content is None:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

        chunks = []
        lines = content.split("\n")

        # Chunk by lines to preserve readability
        current_line = 0

        while current_line < len(lines):
            # Determine chunk end
            chunk_lines = []
            chunk_chars = 0
            end_line = current_line

            while end_line < len(lines) and chunk_chars < self.chunk_size:
                chunk_lines.append(lines[end_line])
                chunk_chars += len(lines[end_line]) + 1  # +1 for newline
                end_line += 1

            if not chunk_lines:
                break

            chunk_text = "\n".join(chunk_lines)
            chunk_hash = self._compute_hash(chunk_text)

            # Calculate byte offsets
            start_byte = sum(len(lines[i]) + 1 for i in range(current_line))
            end_byte = start_byte + len(chunk_text)

            chunks.append(
                CodeChunk(
                    text=chunk_text,
                    chunk_hash=chunk_hash,
                    start_line=current_line + 1,  # 1-indexed
                    end_line=end_line,  # 1-indexed
                    start_byte=start_byte,
                    end_byte=end_byte,
                    file_path=str(file_path),
                )
            )

            # Move to next chunk with overlap
            overlap_lines = max(1, int((self.overlap / self.chunk_size) * len(chunk_lines)))
            current_line = end_line - overlap_lines

            # Prevent infinite loop
            if current_line <= end_line - len(chunk_lines):
                current_line = end_line

        return chunks

    def _compute_hash(self, text: str) -> str:
        """Compute deterministic hash of text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
