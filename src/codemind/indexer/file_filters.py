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
    # VB.NET
    ".vb",
    ".vbs",
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
    ".nim",
    ".cr",     # Crystal
    ".jl",     # Julia
    ".ml",     # OCaml
    ".mli",
    # Web / Markup
    ".html",
    ".htm",
    ".jsp",
    ".jspx",
    ".aspx",
    ".cshtml",  # Razor
    ".razor",
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
    # Version control
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    # Python
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    ".eggs",
    ".tox",
    ".nox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".pytype",
    "site-packages",
    # JavaScript / Node
    "node_modules",
    ".next",
    ".nuxt",
    ".output",
    ".turbo",
    "bower_components",
    ".parcel-cache",
    ".cache",
    # Java / JVM
    "target",
    ".gradle",
    ".mvn",
    # .NET
    "bin",
    "obj",
    "packages",
    ".vs",
    # Rust
    # (also uses "target", already listed)
    # Go
    "vendor",
    # Build artifacts
    "build",
    "dist",
    "out",
    "output",
    "_build",
    "cmake-build-debug",
    "cmake-build-release",
    # IDE / Editor
    ".idea",
    ".vscode",
    ".eclipse",
    ".settings",
    ".project",
    # Coverage / Reports
    "coverage",
    "htmlcov",
    ".nyc_output",
    # Documentation build
    "_site",
    "site",
    ".docusaurus",
    # Container / Infra
    ".terraform",
    ".serverless",
    # Misc generated
    "generated",
    "auto-generated",
    ".generated",
    "migrations",       # DB migrations (often auto-generated)
    "__snapshots__",
    "__mocks__",
    "__fixtures__",
}

# File patterns to ignore (matched against filename)
IGNORED_PATTERNS = {
    # Python compiled
    ".pyc",
    ".pyo",
    ".pyd",
    # Native binaries / libraries
    ".so",
    ".dll",
    ".dylib",
    ".o",
    ".a",
    ".lib",
    ".obj",
    ".exe",
    # Java
    ".class",
    ".jar",
    ".war",
    ".ear",
    # .NET
    ".nupkg",
    # Python packaging
    ".egg-info",
    ".egg",
    ".whl",
    # Source maps
    ".map",
    ".js.map",
    ".css.map",
    # Minified files (noise, not human-written)
    ".min.js",
    ".min.css",
    ".bundle.js",
    # Lock files (auto-generated, extremely large)
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Pipfile.lock",
    "poetry.lock",
    "Gemfile.lock",
    "composer.lock",
    "Cargo.lock",
    "go.sum",
    "packages.lock.json",
    # Images / media (binary)
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".mp3",
    ".mp4",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    # Archives
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".rar",
    ".7z",
    # Database
    ".db",
    ".sqlite",
    ".sqlite3",
    # Misc
    ".DS_Store",
    "Thumbs.db",
    ".log",
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
