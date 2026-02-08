"""
Import resolver — resolves import statements to file paths within a repository.

Maps import module names to actual files in the repo, enabling IMPORTS
graph edges between files.
"""

from pathlib import Path


class ImportResolver:
    """Resolve import statements to file paths within a repository.
    
    Builds a file index on initialization and uses per-language resolution
    rules to map import module names to actual files.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize import resolver.

        Args:
            repo_path: Root path of the repository
        """
        self.repo_path = Path(repo_path)
        self._file_index: dict[str, Path] = {}
        self._module_index: dict[str, Path] = {}
        self._build_indices()

    def _build_indices(self):
        """Build file and module indices for fast lookup."""
        try:
            for path in self.repo_path.rglob("*"):
                if path.is_file() and not any(
                    part.startswith(".") or part == "node_modules" or part == "__pycache__"
                    or part == "venv" or part == ".venv" or part == "vendor"
                    for part in path.parts
                ):
                    rel = path.relative_to(self.repo_path)
                    self._file_index[str(rel)] = path

                    # Build module name index
                    # Python: codemind/indexer/chunker.py → codemind.indexer.chunker
                    if path.suffix == ".py":
                        module_parts = list(rel.with_suffix("").parts)
                        if module_parts[-1] == "__init__":
                            module_parts = module_parts[:-1]
                        if module_parts:
                            module_name = ".".join(module_parts)
                            self._module_index[module_name] = rel

                    # JS/TS: src/utils/helper.ts → src/utils/helper
                    elif path.suffix in (".js", ".ts", ".jsx", ".tsx", ".mjs"):
                        module_key = str(rel.with_suffix(""))
                        self._module_index[module_key] = rel
                        # Also without src/ prefix
                        parts = list(rel.parts)
                        if parts and parts[0] == "src":
                            alt_key = str(Path(*parts[1:]).with_suffix(""))
                            self._module_index[alt_key] = rel

                    # Java: com/example/Foo.java → com.example.Foo
                    elif path.suffix == ".java":
                        module_name = ".".join(rel.with_suffix("").parts)
                        self._module_index[module_name] = rel

        except Exception:
            pass  # Graceful degradation

    def resolve(self, module_name: str, language: str, source_file: Path | None = None) -> str | None:
        """
        Resolve an import module name to a relative file path.

        Args:
            module_name: The module/package name from the import statement
            language: Programming language
            source_file: The file containing the import (for relative imports)

        Returns:
            Relative file path within repo, or None if external/not found
        """
        if not module_name:
            return None

        if language == "python":
            return self._resolve_python(module_name, source_file)
        elif language in ("javascript", "typescript"):
            return self._resolve_js(module_name, source_file)
        elif language == "go":
            return self._resolve_go(module_name)
        elif language == "java":
            return self._resolve_java(module_name)
        elif language == "rust":
            return self._resolve_rust(module_name)
        elif language == "c_sharp":
            return self._resolve_csharp(module_name)
        else:
            return self._resolve_generic(module_name)

    def _resolve_python(self, module_name: str, source_file: Path | None = None) -> str | None:
        """Resolve Python import (e.g., 'codemind.indexer.chunker')."""
        # Direct match
        if module_name in self._module_index:
            return str(self._module_index[module_name])

        # Try as package (look for __init__.py)
        parts = module_name.split(".")
        for i in range(len(parts), 0, -1):
            partial = ".".join(parts[:i])
            if partial in self._module_index:
                return str(self._module_index[partial])

        # Relative import resolution
        if source_file:
            source_dir = Path(source_file).parent
            # Try relative path
            for suffix in [".py", "/__init__.py"]:
                candidate = module_name.replace(".", "/") + suffix
                full_path = source_dir / candidate
                try:
                    rel = full_path.relative_to(self.repo_path)
                    if str(rel) in self._file_index:
                        return str(rel)
                except ValueError:
                    pass

        return None  # External import

    def _resolve_js(self, module_name: str, source_file: Path | None = None) -> str | None:
        """Resolve JS/TS import (e.g., './utils' or '@/components/Button')."""
        # Skip npm packages
        if not module_name.startswith(".") and not module_name.startswith("@/") and not module_name.startswith("~/"):
            return None

        if source_file:
            source_dir = Path(source_file).parent
            # Relative path resolution
            clean_module = module_name.lstrip("./").replace("@/", "src/").replace("~/", "")

            for ext in ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"]:
                candidate = clean_module + ext
                # Try from source file directory
                full_path = source_dir / candidate
                try:
                    rel = full_path.relative_to(self.repo_path)
                    if str(rel) in self._file_index:
                        return str(rel)
                except ValueError:
                    pass

                # Try from repo root
                if candidate in self._file_index:
                    return candidate

        # Try module index
        clean = module_name.lstrip("./")
        if clean in self._module_index:
            return str(self._module_index[clean])

        return None

    def _resolve_go(self, module_name: str) -> str | None:
        """Resolve Go import (e.g., 'github.com/user/repo/pkg')."""
        # Only resolve internal packages (relative to repo)
        parts = module_name.split("/")
        # Try progressively shorter paths
        for i in range(len(parts)):
            subpath = "/".join(parts[i:])
            # Check if any file exists under this path
            for file_path in self._file_index:
                if file_path.startswith(subpath + "/") or file_path == subpath + ".go":
                    return file_path
        return None

    def _resolve_java(self, module_name: str) -> str | None:
        """Resolve Java import (e.g., 'com.example.utils.Helper')."""
        if module_name in self._module_index:
            return str(self._module_index[module_name])

        # Try parent package (import com.example.* → com/example/)
        parts = module_name.rsplit(".", 1)
        if len(parts) == 2 and parts[1] == "*":
            parent = parts[0].replace(".", "/")
            for file_path in self._file_index:
                if file_path.startswith(parent + "/"):
                    return file_path
        return None

    def _resolve_rust(self, module_name: str) -> str | None:
        """Resolve Rust use (e.g., 'crate::module::Type')."""
        # Only resolve crate-local imports
        if not module_name.startswith("crate") and not module_name.startswith("self") and not module_name.startswith("super"):
            return None

        parts = module_name.replace("crate::", "").replace("self::", "").replace("super::", "../").split("::")
        path = "/".join(parts)

        for ext in [".rs", "/mod.rs", "/lib.rs"]:
            candidate = f"src/{path}{ext}"
            if candidate in self._file_index:
                return candidate
        return None

    def _resolve_csharp(self, module_name: str) -> str | None:
        """Resolve C# using (e.g., 'MyApp.Services.Auth')."""
        # C# namespaces don't always map 1:1 to files
        # Try direct mapping
        path = module_name.replace(".", "/")
        for ext in [".cs"]:
            candidate = path + ext
            if candidate in self._file_index:
                return candidate

        # Try matching file by last segment
        last_part = module_name.split(".")[-1]
        for file_path in self._file_index:
            if file_path.endswith(f"/{last_part}.cs") or file_path == f"{last_part}.cs":
                return file_path
        return None

    def _resolve_generic(self, module_name: str) -> str | None:
        """Generic fallback resolution."""
        # Try direct file index match
        for file_path in self._file_index:
            basename = Path(file_path).stem
            if basename == module_name or module_name.endswith(basename):
                return file_path
        return None
