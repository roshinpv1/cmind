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
    ".vue",
    ".svelte",
    # Go
    ".go",
    # Rust
    ".rs",
    # Java/Kotlin/Scala
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".groovy",
    ".gradle",
    # C/C++
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".hxx",
    # C#/.NET
    ".cs",
    ".csx",
    ".fs",
    ".fsx",
    # Other languages
    ".rb",
    ".php",
    ".swift",
    ".m",
    ".mm",
    ".dart",
    ".ex",
    ".exs",
    ".erl",
    ".hs",
    ".lua",
    ".pl",
    ".pm",
    ".r",
    ".R",
    ".zig",
    ".v",
    ".clj",
    ".cljs",
    # Web / Markup
    ".html",
    ".htm",
    ".jsp",
    ".jspx",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".graphql",
    ".gql",
    # Data / Query
    ".sql",
    ".proto",
    # Config / Documentation
    ".md",
    ".rst",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".xml",
    ".ini",
    ".cfg",
    ".env",
    ".properties",
    # Build / DevOps
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".tf",
    ".hcl",
    ".dockerfile",
    # Makefile has no extension — handled separately
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

# Extensionless filenames that should be indexed
KNOWN_FILENAMES = {
    "Makefile",
    "Dockerfile",
    "Jenkinsfile",
    "Gemfile",
    "Rakefile",
    "Vagrantfile",
    "Procfile",
    "Brewfile",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
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

    # Check extension OR known filename
    if file_path.suffix.lower() not in CODE_EXTENSIONS and file_path.name not in KNOWN_FILENAMES:
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
