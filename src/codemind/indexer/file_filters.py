"""
File filtering for incremental indexing.

Determines which files should be indexed based on extension,
size, binary detection, and ignore patterns.
"""

from pathlib import Path

# Code file extensions that should be indexed
CODE_EXTENSIONS = {
    # Python
    ".py",
    ".pyi",
    # JavaScript/TypeScript
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    # Go
    ".go",
    # Rust
    ".rs",
    # Java/Kotlin/Scala
    ".java",
    ".kt",
    ".kts",
    ".scala",
    # C/C++
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".hxx",
    # Other languages
    ".rb",
    ".php",
    ".swift",
    ".m",
    ".mm",
    # Config/Documentation
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".xml",
    ".sh",
    ".bash",
    ".zsh",
}

# Directories to always ignore
IGNORED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".env",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "target",  # Rust
    "bin",
    "obj",  # .NET
}

# File patterns to ignore
IGNORED_PATTERNS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".dylib",
    ".egg-info",
    ".class",
    ".o",
    ".a",
}

# Maximum file size to index (1 MB)
MAX_FILE_SIZE_BYTES = 1024 * 1024


def should_index_file(file_path: Path, max_size: int = MAX_FILE_SIZE_BYTES) -> bool:
    """
    Determine if a file should be indexed.

    Args:
        file_path: Path to check
        max_size: Maximum file size in bytes

    Returns:
        True if file should be indexed, False otherwise
    """
    # Must exist and be a file
    if not file_path.exists() or not file_path.is_file():
        return False

    # Check extension
    if file_path.suffix.lower() not in CODE_EXTENSIONS:
        return False

    # Check for ignored patterns
    if any(pattern in file_path.name for pattern in IGNORED_PATTERNS):
        return False

    # Check file size
    try:
        if file_path.stat().st_size > max_size:
            return False
    except OSError:
        return False

    return True


def should_index_directory(dir_path: Path) -> bool:
    """
    Determine if a directory should be traversed.

    Args:
        dir_path: Directory path to check

    Returns:
        True if directory should be traversed, False otherwise
    """
    if not dir_path.is_dir():
        return False

    # Check if directory name is in ignored list
    if dir_path.name in IGNORED_DIRS:
        return False

    # Ignore hidden directories (starting with .)
    if dir_path.name.startswith(".") and dir_path.name not in {".github"}:
        return False

    return True
