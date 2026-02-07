"""
Shared utilities for CodeMind.

Provides:
- Content hashing utilities
- Logging configuration
- Common data models
- Helper functions
"""

import hashlib
from pathlib import Path
from typing import Optional


def compute_file_hash(file_path: Path) -> str:
    """
    Compute SHA-256 hash of file content.

    Args:
        file_path: Path to file to hash

    Returns:
        Hexadecimal SHA-256 hash string

    Raises:
        FileNotFoundError: If file does not exist
        PermissionError: If file cannot be read
    """
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def is_binary_file(file_path: Path, chunk_size: int = 8192) -> bool:
    """
    Detect if file is binary by checking for null bytes.

    Args:
        file_path: Path to file to check
        chunk_size: Number of bytes to read for detection

    Returns:
        True if file appears to be binary, False otherwise
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(chunk_size)
            # Check for null bytes (common in binary files)
            return b"\x00" in chunk
    except (OSError, PermissionError):
        # If we can't read it, assume it's binary
        return True


def normalize_path(path: str, base: Path) -> str:
    """
    Normalize path relative to base directory.

    Args:
        path: Absolute or relative path
        base: Base directory to make path relative to

    Returns:
        Forward-slash separated relative path
    """
    abs_path = Path(path).resolve()
    base_path = base.resolve()

    try:
        rel_path = abs_path.relative_to(base_path)
        # Use forward slashes for consistency across platforms
        return str(rel_path).replace("\\", "/")
    except ValueError:
        # Path is not relative to base, return as-is
        return str(abs_path)
