"""
Playbook execution tools - Universal code retrieval and structural discovery.
"""

import fnmatch
import inspect
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
import traceback

from codemind.storage.database import CatalogStore
from codemind.storage.bm25_storage import BM25Storage
from codemind.playbooks.catalog_entry_writer import CatalogEntryWriter, normalize_catalog_params
from codemind.storage.models import RepositoryManifest

# Max chars returned for repo file reads (protect LLM context)
_DEFAULT_REPO_READ_MAX_CHARS = int(os.getenv("CODEMIND_REPO_READ_MAX_CHARS", "200000"))

# Text search (grep_search): skip noisy dirs; max bytes per file to scan
_SEARCH_SKIP_DIRS = frozenset({
    "node_modules",
    ".git",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "target",
    ".gradle",
    "Pods",
    ".next",
    "coverage",
})
_SEARCH_MAX_FILE_BYTES = int(os.getenv("CODEMIND_SEARCH_MAX_FILE_BYTES", str(2 * 1024 * 1024)))


class PlaybookTools:
    """
    Universal code retrieval tool for playbooks.
    
    Playbooks use these tools to discover architectural patterns, trace paths,
    and fetch code content for analysis.
    
    READ-ONLY operations on:
    - Repository checkout on disk (primary for read_file / list_repo_directory)
    - Graphify graph (structure, file discovery)
    - LanceDB (semantic search and catalog vectors when enabled)
    - SQLite (catalog full content)
    """
    
    def __init__(self, lance_storage, graph_service, embedder, db=None):
        """
        Initialize playbook tools.
        
        Args:
            lance_storage: LanceDBStorage instance
            graph_service: GraphQueryService instance
            embedder: Embedder for query encoding
            db: Database instance (for SQLite access)
        """
        self.lance = lance_storage
        self.graph = graph_service
        self.embedder = embedder
        self.db = db
        self.bm25 = BM25Storage()
        self._catalog_writer = CatalogEntryWriter(lance_storage, embedder, db)

    @staticmethod
    def _rrf_fuse(lanes: list[list[dict]], *, k: int = 60) -> list[dict]:
        """Reciprocal rank fusion over heterogeneous retrieval lanes."""
        score_map: dict[str, float] = {}
        exemplar: dict[str, dict] = {}
        reasons: dict[str, set[str]] = {}

        for lane in lanes:
            for rank, row in enumerate(lane, start=1):
                key = str(row.get("chunk_hash") or "") or (
                    f"{row.get('file_path','')}:{row.get('start_line',0)}:{row.get('end_line',0)}"
                )
                if not key:
                    continue
                score_map[key] = score_map.get(key, 0.0) + (1.0 / float(k + rank))
                if key not in exemplar:
                    exemplar[key] = dict(row)
                why = str(row.get("lane") or "").strip()
                if why:
                    reasons.setdefault(key, set()).add(why)

        fused = []
        for key, score in score_map.items():
            row = dict(exemplar.get(key, {}))
            row["rrf_score"] = float(score)
            row["score"] = float(score)
            row["provenance"] = sorted(reasons.get(key, set()))
            fused.append(row)
        fused.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return fused

    def _get_repo_root_sync(self, repo_id: str) -> Path | None:
        """Resolve filesystem root for a repo_id via RepositoryManifest."""
        if not self.db or not repo_id:
            return None
        session = getattr(self.db, "get_session", lambda: None)()
        if not session:
            return None
        try:
            manifest = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()
            if manifest and manifest.repo_path:
                return Path(manifest.repo_path).resolve()
        finally:
            if hasattr(session, "close"):
                session.close()
        return None

    def _repo_graphify_out_dir(self, repo_id: str) -> Path:
        """Resolve canonical graphify-out directory for an indexed repo_id."""
        base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
        repos_base = Path(os.getenv("CODEMIND_REPOS_PATH", os.path.join(base_default, "repos")))
        return repos_base / repo_id / "graphify-out"

    def _repo_graph_path(self, repo_id: str) -> Path:
        """Resolve canonical graph.json path for an indexed repo_id."""
        return self._repo_graphify_out_dir(repo_id) / "graph.json"

    @staticmethod
    def _is_under_root(candidate: Path, root: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _safe_file_under_repo(self, repo_root: Path, relative: str) -> Path | None:
        """Join relative path to repo root; return path only if it exists as a file and stays under root."""
        rel = (relative or "").replace("\\", "/").lstrip("/")
        if not rel:
            return None
        candidate = (repo_root / rel).resolve()
        if not PlaybookTools._is_under_root(candidate, repo_root):
            return None
        return candidate if candidate.is_file() else None

    def _safe_dir_under_repo(self, repo_root: Path, relative: str) -> Path | None:
        """Resolve a directory under repo root (must stay inside root)."""
        rel = (relative or ".").replace("\\", "/").strip()
        if rel in (".", ""):
            candidate = repo_root.resolve()
        else:
            candidate = (repo_root / rel.lstrip("/")).resolve()
        if not PlaybookTools._is_under_root(candidate, repo_root):
            return None
        return candidate if candidate.is_dir() else None

    @staticmethod
    def _resolve_mirror_root(params: dict) -> Path | None:
        """Resolve mirror root from execution context params."""
        mirror = params.get("_mirror_root")
        if not mirror:
            return None
        try:
            p = Path(str(mirror)).resolve()
            return p if p.exists() and p.is_dir() else None
        except Exception:
            return None

    @staticmethod
    def _read_text_file_slice(
        path: Path,
        start_line: int | None,
        end_line: int | None,
        max_chars: int,
    ) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if start_line is not None or end_line is not None:
            s = max(0, (start_line - 1) if start_line else 0)
            e = end_line if end_line is not None else len(lines)
            lines = lines[s:e]
        text = "".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[Truncated]"
        return text

    @staticmethod
    def _search_includes_match(rel_posix: str, basename: str, includes: list[str]) -> bool:
        """Glob filter (e.g. *.py); empty *includes* means all scannable files."""
        if not includes:
            return True
        for pat in includes:
            p = (pat or "").strip()
            if not p:
                continue
            if fnmatch.fnmatch(rel_posix, p) or fnmatch.fnmatch(basename, p):
                return True
            if "/" not in p and fnmatch.fnmatch(rel_posix, f"**/{p}"):
                return True
        return False

    @staticmethod
    def _python_repo_text_search(
        repo_root: Path,
        regex: re.Pattern[str],
        includes: list[str],
        max_match_lines: int,
    ) -> tuple[str, int, bool]:
        """Pure-Python recursive search; output lines ``relpath:lineno:text`` (no subprocess)."""
        root = repo_root.resolve()
        matches: list[str] = []
        truncated = False
        max_bytes = _SEARCH_MAX_FILE_BYTES

        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SEARCH_SKIP_DIRS]
            for fname in filenames:
                if len(matches) >= max_match_lines:
                    truncated = True
                    break
                fpath = Path(dirpath) / fname
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if st.st_size > max_bytes:
                    continue
                try:
                    rel = fpath.resolve().relative_to(root)
                except ValueError:
                    continue
                rel_s = rel.as_posix()
                base = rel.name
                if not PlaybookTools._search_includes_match(rel_s, base, includes):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        for line_no, line in enumerate(fh, start=1):
                            if regex.search(line):
                                matches.append(f"{rel_s}:{line_no}:{line.rstrip('\r\n')}")
                                if len(matches) >= max_match_lines:
                                    truncated = True
                                    break
                except OSError:
                    continue
                if truncated:
                    break
            if truncated:
                break

        count = len(matches)
        if count == 0:
            return "", 0, False
        out = "\n".join(matches)
        if truncated:
            out += (
                f"\n\n...[Output truncated at {max_match_lines} matches; "
                "narrow includes or query.]"
            )
        return out, count, truncated

    def _pick_graph_file_match(self, file_path: str, graph_hits: list[dict]) -> str | None:
        """Choose the best graph file_path string for a user-provided path hint."""
        if not graph_hits:
            return None
        fp_norm = file_path.replace("\\", "/")
        # Exact match
        for f in graph_hits:
            p = (f.get("file_path") or "").replace("\\", "/")
            if p == fp_norm:
                return f.get("file_path")
        # Suffix match (user passed partial path)
        for f in graph_hits:
            p = (f.get("file_path") or "").replace("\\", "/")
            if p.endswith(fp_norm) or fp_norm.endswith(p):
                return f.get("file_path")
        # Basename match preference
        import os as _os

        base = _os.path.basename(fp_norm)
        for f in graph_hits:
            p = (f.get("file_path") or "").replace("\\", "/")
            if p.endswith("/" + base) or _os.path.basename(p) == base:
                return f.get("file_path")
        return graph_hits[0].get("file_path")

    async def _get_default_latest_repos(self) -> list[str]:
        """Fetch the most recently indexed repo_id for each repository."""
        if not self.db:
            return []
            
        session = getattr(self.db, "get_session", lambda: None)()
        if not session:
            return []
            
        try:
            # Query all manifests. This works consistently across MongoBackend/DatabaseManager
            manifests = session.query(RepositoryManifest).all()
            
            latest_by_repo = {}
            for m in manifests:
                # Group by URL if available, otherwise fallback to local path
                key = m.repo_url if m.repo_url else m.repo_path
                current_latest = latest_by_repo.get(key)
                if not current_latest or m.last_indexed_at > current_latest.last_indexed_at:
                    latest_by_repo[key] = m
                    
            return [m.repo_id for m in latest_by_repo.values()]
        except Exception as e:
            print(f"[TOOLS] Error fetching default repos: {e}")
            return []
        finally:
            if hasattr(session, 'close'):
                session.close()
    
    async def search_codebase(self, params: dict) -> dict:
        """
        Semantic/Vector search across the codebase.
        
        NOTE: This tool is disabled in Graph-First (Zero-Vector) environments.
        Use get_map and search_code instead for discovery.
        
        Args:
            params: {
                queries: list[str] - Search queries to try
                repo_id: str | list[str] - Repository ID or IDs
                limit: int - Max results (default: 10)
                mode: str - "semantic" or "hybrid" (default: semantic)
                file_types: list[str] - File extensions to filter (.py, .js)
                graph_filters: dict - Additional graph filters
            }
            
        Returns:
            {
                results: list[{file_path, chunk_text, score, line_start, line_end}],
                count: int,
                queries_used: list[str]
            }
        """
        try:
            vector_disabled = (
                not self.embedder
                or getattr(self.embedder, "provider_type", None) == "none"
            )
            
            mode = params.get("mode", "semantic")
            repo_id = params.get("repo_id")
            if not repo_id and mode != "catalog":
                return {
                    "success": False,
                    "error": "repo_id is required for search_codebase",
                    "results": [],
                    "count": 0,
                    "queries_used": [],
                }

            queries = params.get("queries", [])
            limit = params.get("limit", 10)
            if vector_disabled and mode == "semantic":
                mode = "hybrid"
            
            file_types = params.get("file_types", [])
            graph_filters = params.get("graph_filters", {})
            min_score = params.get("min_score", 0.0)
            
            # Fallback: if queries is empty but there's a single "query" param
            if not queries and "query" in params:
                queries = [params["query"]]
            
            # Validate
            limit = max(1, min(1000, limit))
            
            if not queries:
                return {
                    "error": "No queries provided",
                    "results": [],
                    "count": 0,
                    "queries_used": []
                }
            
            if not self.embedder and mode == "semantic":
                return {
                    "error": "No embedder available",
                    "results": [],
                    "count": 0,
                    "queries_used": []
                }
            
            # Special handle for catalog mode
            if mode == "catalog":
                final_results = await self._search_catalogs_internal(
                    queries=queries,
                    repo_id=repo_id,
                    limit=limit,
                    min_score=min_score
                )
                
                return {
                    "success": True, 
                    "results": final_results, 
                    "count": len(final_results),
                    "queries_used": queries,
                    "min_score": min_score
                }

            semantic_lane: list[dict] = []
            bm25_lane: list[dict] = []
            structural_lane: list[dict] = []
            for query in queries:
                query_text = str(query or "").strip()
                if not query_text:
                    continue

                # Semantic lane
                if not vector_disabled:
                    query_emb = self.embedder.encode_query(query_text)
                    sem = self.lance.search(
                        query_emb,
                        repo_id=repo_id,
                        limit=limit * 2,
                        min_score=min_score,
                    )
                    for r in sem:
                        file_path = str(r.get("file_path", ""))
                        if file_types and not any(file_path.endswith(ft) for ft in file_types):
                            continue
                        if "_distance" in r and "score" not in r:
                            r["score"] = 1.0 - float(r.get("_distance", 1.0))
                        r["lane"] = "semantic"
                        semantic_lane.append(r)

                # BM25 lexical lane
                bm25_rows = self.bm25.search(
                    query=query_text,
                    repo_id=repo_id if isinstance(repo_id, str) else None,
                    limit=limit * 2,
                    file_types=file_types,
                )
                for r in bm25_rows:
                    r["lane"] = "bm25"
                    bm25_lane.append(r)

                # Structural lane from graph symbols
                if isinstance(repo_id, str):
                    syms = self.graph.find_symbol_by_name(repo_id, query_text, None) or []
                    for s in syms[: max(4, limit)]:
                        fp = str(s.get("file_path") or "")
                        if not fp:
                            continue
                        if file_types and not any(fp.endswith(ft) for ft in file_types):
                            continue
                        structural_lane.append(
                            {
                                "file_path": fp,
                                "chunk_hash": f"symbol:{s.get('name','')}:{fp}:{s.get('start_line',0)}",
                                "chunk_text": f"Symbol match: {s.get('name','')} ({s.get('type','')})",
                                "start_line": int(s.get("start_line", 0) or 0),
                                "end_line": int(s.get("end_line", 0) or 0),
                                "score": 0.65,
                                "lane": "structural",
                            }
                        )

            if mode == "hybrid":
                final_results = self._rrf_fuse(
                    [semantic_lane, bm25_lane, structural_lane]
                )[:limit]
            elif mode == "bm25":
                final_results = sorted(
                    bm25_lane, key=lambda x: float(x.get("score", 0.0)), reverse=True
                )[:limit]
            else:
                final_results = sorted(
                    semantic_lane, key=lambda x: float(x.get("score", 0.0)), reverse=True
                )[:limit]

            if min_score > 0:
                final_results = [
                    r for r in final_results if float(r.get("score", 0.0)) >= float(min_score)
                ][:limit]
            
            # Final safeguard to ensure embeddings never leak to the LLM
            for r in final_results:
                r.pop("embedding", None)
            
            return {
                "success": True,
                "results": final_results,
                "count": len(final_results),
                "queries_used": queries,
                "min_score": min_score,
                "vector_disabled": vector_disabled,
            }
        
        except Exception as e:
            print(f"[TOOLS] search_codebase error: {e}")
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "results": [],
                "count": 0,
                "queries_used": queries if 'queries' in locals() else []
            }

    async def read_file(self, params: dict) -> dict:
        """Read a specific file's content (or a line range).

        Prefers the **repository checkout on disk** (``RepositoryManifest.repo_path``),
        using the Graphify graph to resolve ambiguous paths. Falls back to LanceDB
        chunks only when embeddings are enabled and the file is not on disk.

        Args:
            params: {
                repo_id: str,
                file_path: str,
                start_line: int (optional),
                end_line: int (optional)
            }
        """
        try:
            file_path = params["file_path"]
            start_line = params.get("start_line")
            end_line = params.get("end_line")
            repo_id = params["repo_id"]
            max_chars = int(params.get("max_chars") or _DEFAULT_REPO_READ_MAX_CHARS)
            mirror_root = self._resolve_mirror_root(params)
            prefer_mirror_reads = bool(params.get("_prefer_mirror_reads", False))

            repo_root = self._get_repo_root_sync(repo_id)
            resolved_rel: str | None = None
            disk_path: Path | None = None
            source_kind = "filesystem"
            mirror_miss_fallback = False

            if repo_root:
                graph_hits = self.graph.find_files_by_pattern(repo_id, pattern=file_path)
                if not graph_hits:
                    import os as _os

                    base = _os.path.basename(file_path.replace("\\", "/"))
                    if base:
                        graph_hits = self.graph.find_files_by_pattern(repo_id, pattern=base)

                if graph_hits:
                    resolved_rel = self._pick_graph_file_match(file_path, graph_hits)
                    if resolved_rel:
                        if prefer_mirror_reads and mirror_root:
                            disk_path = self._safe_file_under_repo(mirror_root, resolved_rel)
                            if disk_path:
                                source_kind = "mirror_filesystem"
                            else:
                                mirror_miss_fallback = True
                        if not disk_path:
                            disk_path = self._safe_file_under_repo(repo_root, resolved_rel)

                if not disk_path:
                    rel_candidate = file_path.replace("\\", "/").lstrip("/")
                    if prefer_mirror_reads and mirror_root:
                        disk_path = self._safe_file_under_repo(mirror_root, rel_candidate)
                        if disk_path:
                            source_kind = "mirror_filesystem"
                        else:
                            mirror_miss_fallback = True
                    if not disk_path:
                        disk_path = self._safe_file_under_repo(
                            repo_root, rel_candidate
                        )

                if disk_path and disk_path.is_file():
                    content = self._read_text_file_slice(
                        disk_path, start_line, end_line, max_chars=max_chars
                    )
                    return {
                        "success": True,
                        "file_path": resolved_rel or file_path,
                        "absolute_path": str(disk_path),
                        "content": content,
                        "source": source_kind,
                        "chunks": 1,
                        "mirror_root": str(mirror_root) if mirror_root else None,
                        "mirror_preferred": prefer_mirror_reads,
                        "mirror_fallback_to_repo": mirror_miss_fallback and source_kind != "mirror_filesystem",
                    }

            # Legacy: LanceDB chunk reassembly when index + embedder exist
            if (
                self.embedder
                and self.lance
                and getattr(self.embedder, "provider_type", None) != "none"
            ):
                files = self.graph.find_files_by_pattern(repo_id, pattern=file_path)
                if not files:
                    return {
                        "error": f"File not found on disk or in graph: {file_path}",
                        "content": "",
                        "success": False,
                    }

                query_emb = self.embedder.encode_query(f"file content {file_path}")
                results = self.lance.search(query_emb, repo_id=repo_id, limit=50)

                file_chunks = [r for r in results if file_path in r.get("file_path", "")]
                file_chunks.sort(key=lambda r: r.get("start_line", 0))

                if start_line and end_line:
                    file_chunks = [
                        r
                        for r in file_chunks
                        if r.get("start_line", 0) <= end_line
                        and r.get("end_line", 0) >= start_line
                    ]

                content = "\n".join(r.get("chunk_text", "") for r in file_chunks)
                return {
                    "success": True,
                    "file_path": file_path,
                    "content": content,
                    "source": "lancedb",
                    "chunks": len(file_chunks),
                }

            return {
                "error": (
                    f"File not found or repo checkout unavailable for repo_id={repo_id!r}: "
                    f"{file_path}"
                ),
                "content": "",
                "success": False,
            }
        except Exception as e:
            return {"error": str(e), "content": "", "success": False}

    async def get_file_outline(self, params: dict) -> dict:
        """Get structural outline (AST) of a file showing classes, methods, and functions.
        
        Args:
            params: {
                repo_id: str,
                file_path: str
            }
        """
        try:
            repo_id = params["repo_id"]
            file_path = params["file_path"]
            
            # Use the Graphify adapter to get the extracted Tree-Sitter AST context
            outline = self.graph.get_file_context(repo_id, file_path)
            
            # Format nicely for the LLM
            lines = [f"File Outline: {outline['file_path']}", "=" * 40]
            
            if framework_imports := outline.get("imports"):
                lines.append(f"Imports: {', '.join(framework_imports[:10])}" + ("..." if len(framework_imports) > 10 else ""))
                
            if classes := outline.get("classes"):
                lines.append("\nClasses:")
                for c in classes:
                    lines.append(f"  class {c['name']} (Lines {c['start_line']}-{c.get('end_line', '?')})")
                    # List its methods
                    methods = [m for m in outline.get("methods", []) if m["class"] == c["name"]]
                    for m in methods:
                        lines.append(f"    - method {m['name']} (Lines {m['start_line']}-{m.get('end_line', '?')})")
            
            if functions := outline.get("functions"):
                lines.append("\nTop-level Functions:")
                for f in functions:
                    lines.append(f"  func {f['name']} (Lines {f['start_line']}-{f.get('end_line', '?')})")
                    
            if not classes and not functions:
                lines.append("\n(No classes or functions detected. Might be a script, config, or UI template.)")
                
            return {
                "success": True, 
                "outline": "\n".join(lines)
            }
        except Exception as e:
            return {"error": str(e), "outline": ""}

    async def search_symbol(self, params: dict) -> dict:
        """Find a symbol (class/function) by name.
        
        Args:
            params: {
                repo_id: str,
                name: str,
                symbol_type: str (optional: "Class" or "Function")
            }
        """
        try:
            repo_id = params.get("repo_id")
            if not repo_id:
                return {"error": "repo_id is required for search_symbol", "symbols": [], "count": 0}
                
            name = params["name"]
            symbol_type = params.get("symbol_type")

            results = self.graph.find_symbol_by_name(repo_id, name, symbol_type)
            return {"success": True, "symbols": results, "count": len(results)}
        except Exception as e:
            return {"error": str(e), "symbols": [], "count": 0}

    async def get_callers(self, params: dict) -> dict:
        """Find all functions that call a given function.
        
        Args:
            params: {repo_id: str, function_name: str}
        """
        try:
            repo_id = params["repo_id"]
            func_name = params["function_name"]
            callers = self.graph.get_callers(repo_id, func_name)
            return {"success": True, "callers": callers, "count": len(callers)}
        except Exception as e:
            return {"error": str(e), "callers": [], "count": 0}

    async def get_callees(self, params: dict) -> dict:
        """Find all functions called by a given function.
        
        Args:
            params: {repo_id: str, function_name: str}
        """
        try:
            repo_id = params["repo_id"]
            func_name = params["function_name"]
            callees = self.graph.get_callees(repo_id, func_name)
            return {"success": True, "callees": callees, "count": len(callees)}
        except Exception as e:
            return {"error": str(e), "callees": [], "count": 0}

    async def get_dependencies(self, params: dict) -> dict:
        """Get files imported by a file, or files that import it.
        
        Args:
            params: {
                repo_id: str,
                file_path: str,
                direction: str ("imports" or "imported_by")
            }
        """
        try:
            repo_id = params["repo_id"]
            file_path = params["file_path"]
            direction = params.get("direction", "imports")

            if direction == "imported_by":
                deps = self.graph.get_dependents(repo_id, file_path)
            else:
                deps = self.graph.get_dependency_chain(repo_id, file_path)

            return {"success": True, "dependencies": deps, "count": len(deps)}
        except Exception as e:
            return {"error": str(e), "dependencies": [], "count": 0}

    async def list_files(self, params: dict) -> dict:
        """List files in a repository matching a pattern.
        
        Args:
            params: {
                repo_id: str,
                pattern: str (optional),
                file_type: str (optional, e.g. ".py")
            }
        """
        try:
            repo_id = params.get("repo_id") or params.get("repo_id", "")
            if not repo_id:
                return {
                    "error": "repo_id is required for list_files. Use list_file_system for non-repo paths.",
                    "files": [],
                    "count": 0,
                }
            pattern = params.get("pattern")
            file_type = params.get("file_type")

            files = self.graph.find_files_by_pattern(repo_id, pattern=pattern, file_type=file_type)
            cap = max(1, min(int(os.getenv("CODEMIND_LIST_FILES_CAP", "500")), 5000))
            truncated = len(files) > cap
            if truncated:
                files = files[:cap]
            out = {"success": True, "files": files, "count": len(files)}
            if truncated:
                out["truncated"] = True
                out["note"] = f"Results capped at {cap} files; narrow pattern or file_type."
            if len(files) == 0:
                out["hint"] = (
                    "No files matched in the graph index. "
                    "The graph may not have File nodes for this pattern. "
                    "Try: list_repo_directory (walks the actual filesystem), "
                    "grep_search (searches raw source text), or "
                    "get_map (shows architecture hubs regardless of file index)."
                )
            return out
        except Exception as e:
            return {"error": str(e), "files": [], "count": 0}

    async def list_repo_directory(self, params: dict) -> dict:
        """List files and subdirectories under a path in the repository checkout.

        Uses ``RepositoryManifest.repo_path`` (same root as ``search_code`` / ``grep_search``). Paths are
        relative to the repo root and constrained to stay inside it.

        Args:
            params: {
                repo_id: str,
                relative_path: str (optional, default "."),
                recursive: bool (optional, default False),
                max_depth: int (optional, default 4 when recursive),
                max_entries: int (optional, cap listed items, default 300),
                include_dotfiles: bool (optional, default False),
            }
        """
        try:
            repo_id = params["repo_id"]
            relative_path = (params.get("relative_path") or ".").strip()
            recursive = bool(params.get("recursive", False))
            max_depth = max(1, min(int(params.get("max_depth", 4)), 12))
            max_entries = max(1, min(int(params.get("max_entries", 300)), 2000))
            include_dotfiles = bool(params.get("include_dotfiles", False))
            mirror_root = self._resolve_mirror_root(params)
            prefer_mirror_reads = bool(params.get("_prefer_mirror_reads", False))

            repo_root = self._get_repo_root_sync(repo_id)
            if not repo_root:
                return {
                    "error": f"Physical repository path not found for {repo_id}",
                    "entries": [],
                    "count": 0,
                }

            listing_root = repo_root
            listing_mode = "repo"
            mirror_fallback = False
            if prefer_mirror_reads and mirror_root:
                candidate = self._safe_dir_under_repo(mirror_root, relative_path)
                if candidate:
                    listing_root = mirror_root
                    listing_mode = "mirror"
                    base = candidate
                else:
                    mirror_fallback = True
                    base = self._safe_dir_under_repo(repo_root, relative_path)
            else:
                base = self._safe_dir_under_repo(repo_root, relative_path)
            if not base:
                return {
                    "error": f"Directory not found or outside repo: {relative_path!r}",
                    "entries": [],
                    "count": 0,
                }

            entries: list[dict] = []

            def rel_to_repo(p: Path) -> str:
                return str(p.resolve().relative_to(listing_root.resolve())).replace("\\", "/")

            if not recursive:
                for item in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                    if not include_dotfiles and item.name.startswith("."):
                        continue
                    entries.append(
                        {
                            "path": rel_to_repo(item),
                            "type": "directory" if item.is_dir() else "file",
                        }
                    )
                    if len(entries) >= max_entries:
                        break
                return {
                    "success": True,
                    "repo_id": repo_id,
                    "base": rel_to_repo(base),
                    "entries": entries,
                    "count": len(entries),
                    "truncated": len(entries) >= max_entries,
                    "listing_mode": listing_mode,
                    "mirror_root": str(mirror_root) if mirror_root else None,
                    "mirror_fallback_to_repo": mirror_fallback and listing_mode != "mirror",
                }

            # Recursive walk with depth limit (breadth-friendly: sort each level)
            stack: list[tuple[Path, int]] = [(base, 0)]
            while stack and len(entries) < max_entries:
                current, depth = stack.pop()
                try:
                    children = sorted(current.iterdir(), key=lambda p: p.name.lower())
                except OSError:
                    continue
                # Process files first, then dirs (dirs pushed in reverse so shallow names pop first)
                dirs: list[Path] = []
                for item in children:
                    if not include_dotfiles and item.name.startswith("."):
                        continue
                    if item.is_file():
                        entries.append(
                            {"path": rel_to_repo(item), "type": "file"}
                        )
                        if len(entries) >= max_entries:
                            break
                    elif item.is_dir():
                        dirs.append(item)
                if len(entries) >= max_entries:
                    break
                if depth + 1 < max_depth:
                    for d in reversed(dirs):
                        stack.append((d, depth + 1))

            return {
                "success": True,
                "repo_id": repo_id,
                "base": rel_to_repo(base),
                "entries": entries,
                "count": len(entries),
                "truncated": len(entries) >= max_entries,
                "listing_mode": listing_mode,
                "mirror_root": str(mirror_root) if mirror_root else None,
                "mirror_fallback_to_repo": mirror_fallback and listing_mode != "mirror",
            }
        except Exception as e:
            return {"error": str(e), "entries": [], "count": 0}

    async def list_file_system(self, params: dict) -> dict:
        """List files directly from the physical host file system bypassing the database.
        
        Args:
            params: {
                path: str (absolute path)
            }
        """
        import os
        try:
            path = params.get("path")
            if not path or not os.path.exists(path):
                return {"error": f"Path not found: {path}", "files": [], "count": 0}
            
            files = []
            if os.path.isfile(path):
                files.append(path)
            else:
                for root, _, filenames in os.walk(path):
                    for name in filenames:
                        files.append(os.path.join(root, name))
            
            # Cap at 500 files for agent context safety
            if len(files) > 500:
                files = files[:500]
                
            return {"success": True, "files": files, "count": len(files)}
        except Exception as e:
            return {"error": str(e), "files": [], "count": 0}

    async def read_file_system(self, params: dict) -> dict:
        """Read file content directly from the physical host file system.
        
        Args:
            params: {
                path: str (absolute path)
            }
        """
        import os
        try:
            path = params.get("path")
            if not path or not os.path.isfile(path):
                return {"error": f"File not found: {path}", "content": ""}
            
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                
            # Basic truncation if file is absurdly huge (protect LLM context)
            if len(content) > 100000:
                content = content[:100000] + "...[Truncated]"
                
            return {
                "success": True,
                "file_path": path,
                "content": content,
            }
        except Exception as e:
            return {"error": str(e), "content": ""}

    async def write_file_system(self, params: dict) -> dict:
        """Write content directly to the physical host file system.
        
        Args:
            params: {
                path or file_path: str (accepts either key),
                content: str
            }
        """
        import os
        try:
            # Accept both 'path' and 'file_path' — models often use file_path
            path = params.get("path") or params.get("file_path")
            content = params.get("content", "")
            mirrored_from = params.get("_mirrored_from_path")
            
            if not path:
                return {"error": "Path cannot be empty (provide 'path' or 'file_path')"}
                
            # Ensure folder structure exists systematically
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
                
            return {
                "success": True,
                "file_path": path,
                "bytes_written": len(content),
                "mirrored_from_path": mirrored_from,
            }
        except Exception as e:
            return {"error": str(e)}

    async def search_code(self, params: dict) -> dict:
        """Surgical text search (pure Python regex scan over UTF-8 files under the repo checkout).

        Best used in Phase C after narrowing down to specific files/communities.
        
        Args:
            params: {
                query: str,
                queries: list[str] (optional),
                repo_id: str (optional),
                includes: list[str] (optional, e.g. ["*.py", "auth/*"]),
                limit: int (optional, default: 500)
            }
        """
        return await self.grep_search(params)

    async def search_bm25(self, params: dict) -> dict:
        """True lexical retrieval using SQLite FTS5 BM25 ranking."""
        repo_id = params.get("repo_id")
        if not repo_id:
            return {"error": "repo_id is required for search_bm25", "results": [], "count": 0}
        queries = params.get("queries") or []
        if not queries and params.get("query"):
            queries = [params.get("query")]
        if not queries:
            return {"error": "No query or queries provided", "results": [], "count": 0}
        try:
            limit = max(1, min(int(params.get("limit", 20)), 200))
        except (TypeError, ValueError):
            limit = 20
        file_types = params.get("file_types") or []
        if file_types and not isinstance(file_types, list):
            file_types = [str(file_types)]

        merged: dict[str, dict] = {}
        for q in [str(x) for x in queries if str(x).strip()]:
            rows = self.bm25.search(
                query=q,
                repo_id=repo_id,
                limit=limit * 2,
                file_types=file_types,
            )
            for row in rows:
                key = str(row.get("chunk_hash") or "") or (
                    f"{row.get('file_path','')}:{row.get('start_line',0)}:{row.get('end_line',0)}"
                )
                if not key:
                    continue
                existing = merged.get(key)
                row["lane"] = "bm25"
                if not existing or float(row.get("score", 0.0)) > float(existing.get("score", 0.0)):
                    merged[key] = row
        results = sorted(
            merged.values(),
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )[:limit]
        return {
            "success": True,
            "results": results,
            "count": len(results),
            "queries_used": queries,
        }

    async def get_map(self, params: dict) -> dict:
        """Phase A: Architecture Mapping (The 'GPS').

        Produces a **reading roadmap**: high-degree nodes and entry points are hints for
        *what to read next* and *where to trace*, before opening files. Use this to
        drive direction; pair with trace/caller tools to order investigation.
        """
        repo_id = params.get("repo_id")
        if not repo_id:
            return {"error": "repo_id is required for get_map"}

        limit = params.get("limit")
        if limit is not None:
            try:
                limit = max(5, min(int(limit), 50))
            except (TypeError, ValueError):
                limit = 15
        else:
            limit = 15
        return self.graph.get_architecture_map(repo_id, limit=limit)

    async def trace_path(self, params: dict) -> dict:
        """Phase B: Targeted Exploration (Trace Hierarchy).

        Refines the map into a **concrete sequence** of symbols/files to read along the
        path from `start` to `end`. Use the returned path to prioritize `read_file`
        calls in order, not alphabetically or by guesswork.
        """
        repo_id = params.get("repo_id")
        start = params.get("start")
        end = params.get("end")
        
        if not all([repo_id, start, end]):
            return {"error": "Missing parameters: repo_id, start, or end required."}
            
        path = self.graph.trace_connectivity_path(repo_id, start, end)
        return {
            "success": True,
            "path": path,
            "length": len(path)
        }

    async def graphify_query(self, params: dict) -> dict:
        """Run a Graphify traversal query over ``graphify-out/graph.json``.

        Supports BFS (broad context) and DFS (path-focused context), with token budget.
        """
        try:
            repo_id = (params.get("repo_id") or "").strip()
            if not repo_id:
                return {"success": False, "error": "Missing required parameter: repo_id"}

            question = (params.get("question") or "").strip()
            if not question:
                return {"success": False, "error": "Missing required parameter: question"}

            mode = (params.get("mode") or ("dfs" if params.get("dfs") else "bfs")).lower()
            if mode not in {"bfs", "dfs"}:
                mode = "bfs"

            depth = max(1, min(int(params.get("depth", 2)), 6))
            budget = max(200, min(int(params.get("budget", 5000)), 20000))
            graph_path = str(self._repo_graph_path(repo_id))

            from codemind.graphify.serve import _load_graph, _score_nodes, _bfs, _dfs, _subgraph_to_text

            G = _load_graph(graph_path)
            terms = [t.lower() for t in question.split() if len(t) > 2]
            scored = _score_nodes(G, terms)
            start_nodes = [nid for _, nid in scored[:5]]
            if not start_nodes:
                return {
                    "success": True,
                    "question": question,
                    "mode": mode,
                    "depth": depth,
                    "budget": budget,
                    "context": "No matching nodes found.",
                    "start_nodes": [],
                    "node_count": 0,
                    "edge_count": 0,
                }

            nodes, edges = (_dfs if mode == "dfs" else _bfs)(G, start_nodes, depth)
            context = _subgraph_to_text(G, nodes, edges, token_budget=budget)
            start_labels = [G.nodes[n].get("label", n) for n in start_nodes]

            return {
                "success": True,
                "question": question,
                "mode": mode,
                "depth": depth,
                "budget": budget,
                "start_nodes": start_labels,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "context": context,
                "repo_id": repo_id,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def graphify_path(self, params: dict) -> dict:
        """Find shortest path between two concepts in Graphify graph."""
        try:
            repo_id = (params.get("repo_id") or "").strip()
            if not repo_id:
                return {"success": False, "error": "Missing required parameter: repo_id"}

            source = (params.get("source") or "").strip()
            target = (params.get("target") or "").strip()
            if not source or not target:
                return {"success": False, "error": "Missing required parameters: source, target"}

            max_hops = max(1, min(int(params.get("max_hops", 8)), 20))
            graph_path = str(self._repo_graph_path(repo_id))

            import networkx as nx
            from codemind.graphify.serve import _load_graph, _score_nodes

            G = _load_graph(graph_path)
            src_scored = _score_nodes(G, [t.lower() for t in source.split() if len(t) > 1])
            tgt_scored = _score_nodes(G, [t.lower() for t in target.split() if len(t) > 1])
            if not src_scored:
                return {"success": False, "error": f"No node matching source '{source}' found"}
            if not tgt_scored:
                return {"success": False, "error": f"No node matching target '{target}' found"}

            src_nid = src_scored[0][1]
            tgt_nid = tgt_scored[0][1]

            try:
                path_nodes = nx.shortest_path(G, src_nid, tgt_nid)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return {
                    "success": False,
                    "error": (
                        f"No path found between '{G.nodes[src_nid].get('label', src_nid)}' "
                        f"and '{G.nodes[tgt_nid].get('label', tgt_nid)}'"
                    ),
                }

            hops = len(path_nodes) - 1
            if hops > max_hops:
                return {"success": False, "error": f"Path exceeds max_hops={max_hops} ({hops} hops found)"}

            segments = []
            for i in range(len(path_nodes) - 1):
                u, v = path_nodes[i], path_nodes[i + 1]
                edata = G.edges[u, v]
                segments.append(
                    {
                        "from": G.nodes[u].get("label", u),
                        "to": G.nodes[v].get("label", v),
                        "relation": edata.get("relation", ""),
                        "confidence": edata.get("confidence", ""),
                    }
                )

            return {
                "success": True,
                "repo_id": repo_id,
                "hops": hops,
                "nodes": [G.nodes[n].get("label", n) for n in path_nodes],
                "segments": segments,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def graphify_shortest_path(self, params: dict) -> dict:
        """Alias for shortest-path traversal (maps to graphify_path)."""
        return await self.graphify_path(params)

    async def graphify_god_nodes(self, params: dict) -> dict:
        """Return high-degree architectural hubs from Graphify graph."""
        try:
            repo_id = (params.get("repo_id") or "").strip()
            if not repo_id:
                return {"success": False, "error": "Missing required parameter: repo_id"}
            top_n = max(1, min(int(params.get("top_n", 10)), 50))
            graph_path = str(self._repo_graph_path(repo_id))

            from codemind.graphify.serve import _load_graph
            from codemind.graphify.analyze import god_nodes as _god_nodes

            G = _load_graph(graph_path)
            nodes = _god_nodes(G, top_n=top_n)
            return {
                "success": True,
                "repo_id": repo_id,
                "top_n": top_n,
                "count": len(nodes),
                "nodes": nodes,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def graphify_neighbors(self, params: dict) -> dict:
        """Return direct neighbors of a graph node with edge metadata."""
        try:
            repo_id = (params.get("repo_id") or "").strip()
            if not repo_id:
                return {"success": False, "error": "Missing required parameter: repo_id"}

            label = (params.get("label") or "").strip()
            if not label:
                return {"success": False, "error": "Missing required parameter: label"}

            relation_filter = (params.get("relation_filter") or "").strip().lower()
            max_neighbors = max(1, min(int(params.get("max_neighbors", 60)), 500))
            graph_path = str(self._repo_graph_path(repo_id))

            from codemind.graphify.serve import _load_graph, _find_node

            G = _load_graph(graph_path)
            matches = _find_node(G, label)
            if not matches:
                return {"success": False, "error": f"No node matching '{label}' found"}

            node_id = matches[0]
            neighbors: list[dict] = []
            sorted_neighbors = sorted(G.neighbors(node_id), key=lambda n: G.degree(n), reverse=True)
            for neighbor in sorted_neighbors:
                edge = G.edges[node_id, neighbor]
                relation = str(edge.get("relation", ""))
                if relation_filter and relation_filter not in relation.lower():
                    continue
                nmeta = G.nodes[neighbor]
                neighbors.append(
                    {
                        "label": nmeta.get("label", neighbor),
                        "node_id": neighbor,
                        "relation": relation,
                        "confidence": edge.get("confidence", ""),
                        "source_file": nmeta.get("source_file", ""),
                        "community": nmeta.get("community"),
                        "degree": G.degree(neighbor),
                    }
                )
                if len(neighbors) >= max_neighbors:
                    break

            return {
                "success": True,
                "repo_id": repo_id,
                "node": {
                    "id": node_id,
                    "label": G.nodes[node_id].get("label", node_id),
                    "community": G.nodes[node_id].get("community"),
                    "degree": G.degree(node_id),
                },
                "count": len(neighbors),
                "neighbors": neighbors,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def graphify_community(self, params: dict) -> dict:
        """Return nodes in a graph community ordered by connectivity."""
        try:
            repo_id = (params.get("repo_id") or "").strip()
            if not repo_id:
                return {"success": False, "error": "Missing required parameter: repo_id"}

            if "community_id" not in params:
                return {"success": False, "error": "Missing required parameter: community_id"}
            community_id = int(params.get("community_id"))
            max_nodes = max(10, min(int(params.get("max_nodes", 200)), 2000))
            graph_path = str(self._repo_graph_path(repo_id))

            from codemind.graphify.serve import _load_graph

            G = _load_graph(graph_path)
            members = [
                nid for nid, meta in G.nodes(data=True)
                if meta.get("community") is not None and int(meta.get("community")) == community_id
            ]
            if not members:
                return {
                    "success": True,
                    "repo_id": repo_id,
                    "community_id": community_id,
                    "count": 0,
                    "nodes": [],
                }

            ordered = sorted(members, key=lambda n: G.degree(n), reverse=True)
            rows: list[dict] = []
            for nid in ordered[:max_nodes]:
                meta = G.nodes[nid]
                rows.append(
                    {
                        "id": nid,
                        "label": meta.get("label", nid),
                        "source_file": meta.get("source_file", ""),
                        "source_location": meta.get("source_location", ""),
                        "degree": G.degree(nid),
                    }
                )
            return {
                "success": True,
                "repo_id": repo_id,
                "community_id": community_id,
                "count": len(rows),
                "total_in_community": len(members),
                "nodes": rows,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def graphify_explain(self, params: dict) -> dict:
        """Explain a graph concept by node details + neighbors."""
        try:
            repo_id = (params.get("repo_id") or "").strip()
            if not repo_id:
                return {"success": False, "error": "Missing required parameter: repo_id"}

            term = (params.get("term") or params.get("label") or "").strip()
            if not term:
                return {"success": False, "error": "Missing required parameter: term"}

            include_neighbors = bool(params.get("include_neighbors", True))
            max_neighbors = max(1, min(int(params.get("max_neighbors", 25)), 200))
            graph_path = str(self._repo_graph_path(repo_id))

            from codemind.graphify.serve import _load_graph, _find_node

            G = _load_graph(graph_path)
            matches = _find_node(G, term)
            if not matches:
                return {"success": False, "error": f"No node matching '{term}' found"}

            node_id = matches[0]
            data = G.nodes[node_id]
            result = {
                "success": True,
                "repo_id": repo_id,
                "node": {
                    "id": node_id,
                    "label": data.get("label", node_id),
                    "source_file": data.get("source_file", ""),
                    "source_location": data.get("source_location", ""),
                    "community": data.get("community"),
                    "degree": G.degree(node_id),
                },
                "alternatives": [G.nodes[n].get("label", n) for n in matches[1:6]],
            }

            if include_neighbors:
                neighbors = []
                for neighbor in G.neighbors(node_id):
                    edge = G.edges[node_id, neighbor]
                    neighbors.append(
                        {
                            "label": G.nodes[neighbor].get("label", neighbor),
                            "relation": edge.get("relation", ""),
                            "confidence": edge.get("confidence", ""),
                        }
                    )
                    if len(neighbors) >= max_neighbors:
                        break
                result["neighbors"] = neighbors
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def graphify_add(self, params: dict) -> dict:
        """Ingest URL content into corpus (Python-only, no shell execution)."""
        try:
            url = (params.get("url") or "").strip()
            if not url:
                return {"success": False, "error": "Missing required parameter: url"}

            target_dir = Path(params.get("target_dir", "./raw"))
            author = params.get("author")
            contributor = params.get("contributor")
            update_graph = bool(params.get("update_graph", False))
            graph_root = params.get("graph_root", ".")
            deep_mode = bool(params.get("deep_mode", False))

            from codemind.graphify.ingest import ingest

            out_path = ingest(url, target_dir, author=author, contributor=contributor)
            result = {
                "success": True,
                "url": url,
                "saved_path": str(out_path),
                "updated_graph": False,
            }

            if update_graph:
                # Graph generation is owned by indexing flow.
                # Keep backward-compatible params but do not execute CLI commands here.
                result["warning"] = (
                    "update_graph is deprecated/no-op: graph generation runs during indexing only."
                )
                result["graph_root_ignored"] = graph_root
                result["deep_mode_ignored"] = deep_mode

            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def graphify_run(self, params: dict) -> dict:
        """Post-index graph artifacts from existing graph for a repo_id.

        Note: graph extraction/generation happens during indexing. This tool only
        regenerates derived outputs (report/html/obsidian) from existing graph.json.
        """
        try:
            import json
            from networkx.readwrite import json_graph

            from codemind.graphify.cluster import cluster, score_all
            from codemind.graphify.analyze import god_nodes, surprising_connections, suggest_questions
            from codemind.graphify.report import generate
            from codemind.graphify.export import to_json, to_html, to_obsidian

            repo_id = (params.get("repo_id") or "").strip()
            if not repo_id:
                return {"success": False, "error": "Missing required parameter: repo_id"}

            no_viz = bool(params.get("no_viz", False))
            obsidian = bool(params.get("obsidian", False))
            obsidian_dir = params.get("obsidian_dir")
            repo_root = self._get_repo_root_sync(repo_id)
            out_dir = self._repo_graphify_out_dir(repo_id)
            graph_path = self._repo_graph_path(repo_id)
            report_path = out_dir / "GRAPH_REPORT.md"
            html_path = out_dir / "graph.html"
            out_dir.mkdir(parents=True, exist_ok=True)
            if not graph_path.exists():
                return {
                    "success": False,
                    "error": (
                        f"Graph not found for repo_id={repo_id}. "
                        "Graph generation runs during indexing only."
                    ),
                }

            raw = json.loads(graph_path.read_text(encoding="utf-8"))
            try:
                G = json_graph.node_link_graph(raw, edges="links")
            except TypeError:
                G = json_graph.node_link_graph(raw)
            G.graph["hyperedges"] = raw.get("hyperedges", [])

            detection = {
                "files": {"code": [], "document": [], "paper": [], "image": []},
                "total_files": G.number_of_nodes(),
                "total_words": 0,
                "warning": "Artifacts regenerated from indexed graph (no extraction run).",
            }

            communities = cluster(G)
            cohesion = score_all(G, communities)
            labels = {cid: f"Community {cid}" for cid in communities}
            gods = god_nodes(G)
            surprises = surprising_connections(G, communities)
            questions = suggest_questions(G, communities, labels)
            token_cost = {"input": 0, "output": 0}

            report = generate(
                G,
                communities,
                cohesion,
                labels,
                gods,
                surprises,
                detection,
                token_cost,
                str(repo_root or out_dir.parent),
                suggested_questions=questions,
            )
            report_path.write_text(report, encoding="utf-8")
            to_json(G, communities, str(graph_path))

            html_generated = False
            html_error = None
            if not no_viz:
                try:
                    to_html(G, communities, str(html_path), community_labels=labels)
                    html_generated = True
                except Exception as e:
                    html_error = str(e)

            obsidian_generated = False
            obsidian_output = None
            if obsidian:
                obsidian_output = str(Path(obsidian_dir).expanduser().resolve()) if obsidian_dir else str((out_dir / "obsidian").resolve())
                to_obsidian(
                    G,
                    communities,
                    obsidian_output,
                    community_labels=labels,
                    cohesion=cohesion,
                )
                obsidian_generated = True

            result = {
                "success": True,
                "repo_id": repo_id,
                "repo_path": str(repo_root) if repo_root else None,
                "graph_path": str(graph_path),
                "report_path": str(report_path),
                "nodes": G.number_of_nodes(),
                "edges": G.number_of_edges(),
                "communities": len(communities),
                "directed": G.is_directed(),
                "html_generated": html_generated,
                "obsidian_generated": obsidian_generated,
                "obsidian_dir": obsidian_output,
            }
            if html_error:
                result["html_error"] = html_error
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def grep_search(self, params: dict) -> dict:
        """Find matches across the repo checkout using pure Python (``re`` over UTF-8 text files).

        No ``grep`` or ``rg`` binaries required—suitable for headless/server deployments.
        Patterns use Python regex syntax (see :mod:`re`), not GNU grep/Rust regex.

        Args:
            params: {
                query: str,
                repo_id: str (optional),
                includes: list[str] (optional, e.g. ["*.py"]),
                limit: int (optional, max match lines),
            }
        """
        try:
            from codemind.storage.models import RepositoryManifest

            repo_id = params.get("repo_id")
            if not repo_id:
                return {"error": "repo_id is required for search_code", "results": "", "count": 0}

            query = params.get("query")
            queries = params.get("queries")

            if queries and isinstance(queries, list):
                escaped_queries = [re.escape(str(q)) for q in queries if q]
                if not escaped_queries:
                    return {"error": "No valid queries provided", "results": "", "count": 0}
                search_pattern = f"({'|'.join(escaped_queries)})"
            elif query:
                # Natural-language fallback: users/models often emit
                # "A OR B OR C" instead of regex alternation "(A|B|C)".
                # Convert this common pattern to a safe escaped alternation.
                raw_query = str(query).strip()
                if re.search(r"\s+\bOR\b\s+", raw_query, flags=re.IGNORECASE):
                    parts = [
                        p.strip()
                        for p in re.split(r"\s+\bOR\b\s+", raw_query, flags=re.IGNORECASE)
                        if p and p.strip()
                    ]
                    if len(parts) > 1:
                        search_pattern = f"({'|'.join(re.escape(p) for p in parts)})"
                    else:
                        search_pattern = raw_query
                else:
                    search_pattern = raw_query
            else:
                return {"error": "No query or queries provided for grep", "results": "", "count": 0}

            includes = params.get("includes") or []
            if includes and not isinstance(includes, list):
                includes = [str(includes)]
            mirror_root = self._resolve_mirror_root(params)
            prefer_mirror_reads = bool(params.get("_prefer_mirror_reads", False))

            repo_path = None
            if self.db:
                session = getattr(self.db, "get_session", lambda: None)()
                if session:
                    manifest = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()
                    if manifest and manifest.repo_path:
                        repo_path = manifest.repo_path
                    if hasattr(session, "close"):
                        session.close()

            if not repo_path:
                return {"error": f"Physical repository path not found for {repo_id}", "results": "", "count": 0}

            scan_root = Path(repo_path)
            mirror_mode = False
            mirror_fallback = False
            if prefer_mirror_reads and mirror_root:
                if mirror_root.exists() and mirror_root.is_dir():
                    scan_root = mirror_root
                    mirror_mode = True
                else:
                    mirror_fallback = True

            try:
                max_lines = max(10, min(int(params.get("limit", 500)), 5000))
            except (TypeError, ValueError):
                max_lines = 500

            try:
                regex = re.compile(search_pattern)
            except re.error as ex:
                return {"error": f"Invalid regex: {ex}", "results": "", "count": 0}

            output, count, _truncated = PlaybookTools._python_repo_text_search(
                scan_root, regex, includes, max_lines
            )

            if count == 0:
                return {
                    "success": True,
                    "results": "No matches found.",
                    "count": 0,
                    "search_pattern": search_pattern,
                    "search_root": str(scan_root),
                    "mirror_mode": mirror_mode,
                    "mirror_fallback_to_repo": mirror_fallback and not mirror_mode,
                }

            return {
                "success": True,
                "results": output,
                "count": count,
                "search_pattern": search_pattern,
                "search_root": str(scan_root),
                "mirror_mode": mirror_mode,
                "mirror_fallback_to_repo": mirror_fallback and not mirror_mode,
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Grep execution failed: {str(e)}", "results": "", "count": 0}

    async def _search_catalogs_internal(
        self, 
        queries: list[str], 
        repo_id: str | list[str] | None = None, 
        limit: int = 10,
        min_score: float = 0.5,
        ai_rerank: bool = False
    ) -> list[dict]:
        """
        Internal high-quality catalog search:
        1. Process all queries
        2. Maximize score per repository
        3. Fetch full context from SQLite for top N repositories
        """
        from codemind.storage.lancedb_storage import lance_cosine_distance_to_similarity

        repo_candidates = {} # repo_id -> {score, chunk_item}
        
        for query in queries:
            q_emb = self.embedder.encode_query(query)
            # Use a high enough candidate limit to ensure we find diverse results
            _catalog_hits = self.lance.search_catalogs(
                q_emb,
                repo_id=repo_id,
                limit=limit * 5,
                columns=["repo_id", "repo_name", "chunk_text", "metadata"],
            )
            candidate_chunks = (
                await _catalog_hits if inspect.isawaitable(_catalog_hits) else _catalog_hits
            )

            for item in candidate_chunks:
                rid = item['repo_id']
                if item.get("score") is not None:
                    try:
                        score = float(item["score"])
                    except (TypeError, ValueError):
                        score = 0.0
                else:
                    score = lance_cosine_distance_to_similarity(
                        item.get("_distance", item.get("distance"))
                    )
                if score < min_score:
                    continue
                    
                if rid not in repo_candidates or score > repo_candidates[rid]['score']:
                    repo_candidates[rid] = {
                        "score": score,
                        "item": item
                    }

        # Sort and take top N
        sorted_repos = sorted(
            repo_candidates.items(), 
            key=lambda x: x[1]['score'], 
            reverse=True
        )
        final_repos = sorted_repos[:limit]
        
        results = []
        for rid, candidate in final_repos:
            score = candidate['score']
            item = candidate['item']
            
            # Fetch FULL content from SQLite
            full_content = ""
            metadata = {}
            if self.db:
                try:
                    from sqlalchemy import create_engine
                    from sqlalchemy.orm import sessionmaker, Session
                    
                    # Handle both Database wrapper and raw Engine
                    session = None
                    if hasattr(self.db, "get_session"):
                        session = self.db.get_session()
                    elif hasattr(self.db, "connect"):
                        # It's likely an Engine
                        session = sessionmaker(bind=self.db)()
                    
                    if session:
                        with session:
                            cat_entry = session.query(CatalogStore).filter_by(repo_id=rid).first()
                            if cat_entry:
                                full_content = cat_entry.content
                                metadata = cat_entry.metadata_json or {}
                            else:
                                full_content = item.get("chunk_text") or item.get("result", "")
                    else:
                        full_content = item.get("chunk_text") or item.get("result", "")
                except Exception as e:
                    full_content = item.get("chunk_text") or item.get("result", "")
            else:
                full_content = item.get("chunk_text") or item.get("result", "")

            import json
            try:
                content_obj = json.loads(full_content)
            except:
                content_obj = {}

            # Build rich text with all fields explicitly surfaced
            repo_name = content_obj.get("repo_name", metadata.get("repo_name", item.get("repo_name", rid)))
            parts = [
                f"CATALOG ENTRY: {repo_name}",
                f"Relevance Score: {score:.2f}",
            ]

            # Identity & Metadata
            if metadata.get("repo_url"): parts.append(f"Repository URL: {metadata['repo_url']}")
            if metadata.get("org"): parts.append(f"Organization: {metadata['org']}")
            if metadata.get("category"): parts.append(f"Category: {metadata['category']}")

            # Tech Detail
            if metadata.get("architecture"): parts.append(f"Architecture: {metadata['architecture']}")
            if metadata.get("tech_stack"): parts.append(f"Tech Stack: {metadata['tech_stack']}")
            if metadata.get("specification"): parts.append(f"Specification: {metadata['specification']}")
            
            topics = metadata.get("topics", [])
            if topics: parts.append(f"Topics: {', '.join(topics)}")

            # Quality
            quality = metadata.get("quality_score", 0)
            if quality: parts.append(f"Quality Score: {quality}/100")
            pros = metadata.get("pros", [])
            if pros: parts.append(f"Pros: {'; '.join(pros)}")
            cons = metadata.get("cons", [])
            if cons: parts.append(f"Cons: {'; '.join(cons)}")

            rich_text = "\n".join(parts)

            results.append({
                "file_path": f"catalog://{rid}",
                "chunk_text": rich_text,
                "score": score,
                "start_line": 0,
                "end_line": 0,
                "repo_id": rid,
                "repo_name": repo_name,
                "metadata": json.dumps(metadata)
            })
        
        # Supplement with proposed/qualified entries from SQLite (not in LanceDB)
        existing_rids = {r["repo_id"] for r in results}
        if self.db:
            try:
                import json as _json
                with self.db.get_session() as session:
                    proposed_entries = session.query(CatalogStore).filter(
                        CatalogStore.status.in_(["proposed", "qualified"])
                    ).all()
                    for entry in proposed_entries:
                        if entry.repo_id in existing_rids:
                            continue
                        # Keyword match: check if any query word appears in repo_name, source_gap, or content
                        entry_text = f"{entry.repo_name or ''} {entry.source_gap or ''} {entry.content or ''}".lower()
                        match_score = 0.0
                        for q in queries:
                            q_words = q.lower().split()
                            matched_words = sum(1 for w in q_words if w in entry_text)
                            word_score = matched_words / max(len(q_words), 1)
                            match_score = max(match_score, word_score * 0.5)  # Cap at 0.5 for keyword matches
                        
                        if match_score < 0.1:
                            continue
                        
                        try:
                            content_obj = _json.loads(entry.content) if entry.content else {}
                        except:
                            content_obj = {}
                        
                        meta = entry.metadata_json or {}
                        meta["status"] = entry.status
                        meta["source_gap"] = entry.source_gap
                        
                        repo_name = entry.repo_name or entry.repo_id
                        parts = [
                            f"CATALOG ENTRY: {repo_name} [PROPOSED]",
                            f"Status: {entry.status}",
                            f"Relevance Score: {match_score:.2f}",
                        ]
                        if entry.source_gap:
                            parts.append(f"Source Gap: {entry.source_gap}")
                        desc = content_obj.get("description", "")
                        if desc:
                            parts.append(f"Description: {desc}")
                        if meta.get("tech_stack"):
                            parts.append(f"Tech Stack: {meta['tech_stack']}")
                        
                        results.append({
                            "file_path": f"catalog://{entry.repo_id}",
                            "chunk_text": "\n".join(parts),
                            "score": match_score,
                            "start_line": 0,
                            "end_line": 0,
                            "repo_id": entry.repo_id,
                            "repo_name": repo_name,
                            "metadata": _json.dumps(meta),
                            "status": entry.status,
                        })
                        existing_rids.add(entry.repo_id)
            except Exception as e:
                print(f"[WARN] Failed to supplement with proposed entries: {e}")
        
        # Re-sort by score and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]

        # ── AI LLM RE-RANKING (intent-aware, top vector hits only) ───
        if ai_rerank and results:
            try:
                from codemind.llm.factory import get_chat_model
                from codemind.playbooks.structured_schemas import CatalogRerankOutput
                from langchain_core.messages import SystemMessage, HumanMessage
                import json as _json

                rerank_pool = results[:5]
                allowed_ids = {r["repo_id"] for r in rerank_pool}
                pool_by_id = {r["repo_id"]: r for r in rerank_pool}

                rerank_payload = []
                for r in rerank_pool:
                    r_meta = _json.loads(r.get("metadata", "{}")) if r.get("metadata") else {}
                    rerank_payload.append({
                        "repo_id": r["repo_id"],
                        "repo_name": r.get("repo_name", ""),
                        "business_functionalities": r_meta.get("business_functionalities", []),
                        "category": r_meta.get("category", ""),
                        "taxonomy_labels": r_meta.get("taxonomy_labels", []),
                        "technical_capabilities": r_meta.get("technical_capabilities", []),
                        "tech_stack": r_meta.get("tech_stack", ""),
                        "architecture": r_meta.get("architecture", ""),
                        "summary_high_level": (r_meta.get("summary_high_level") or "")[:400],
                        "preview": r.get("chunk_text", "")[:300],
                    })

                query_str = queries[0] if isinstance(queries, list) else queries
                sys_prompt = (
                    "You re-rank catalog components that were already retrieved by vector search. "
                    "Candidates are ONLY the JSON objects provided (each maps to one real component by repo_id).\n\n"
                    "Step 1 — Infer the user's actual intent in plain language (query_intent).\n"
                    "Step 2 — Classify intent_axis: "
                    "'business' if the query is mainly about outcomes, domains, capabilities, compliance, or product fit; "
                    "'technical' if mainly about stack, runtime, frameworks, APIs, deployment, or engineering constraints; "
                    "'hybrid' if both matter materially.\n"
                    "Step 3 — Score every candidate repo_id from the list. "
                    "business_relevance_score: 0-100 from business_functionalities, category, taxonomy_labels, summary. "
                    "technology_fit_score: 0-100 from tech_stack, architecture, technical_capabilities, and technical cues in the query. "
                    "If a dimension is not relevant to the query, use 50 for that dimension (neutral).\n"
                    "Step 4 — final_score MUST follow intent_axis: "
                    "business → final_score = business_relevance_score; "
                    "technical → final_score = technology_fit_score; "
                    "hybrid → final_score = ROUND(business_relevance_score * 0.5 + technology_fit_score * 0.5).\n"
                    "Step 5 — Ordering: sort items by final_score descending; ties by stronger primary dimension for that axis.\n"
                    "Relevance filter (omit from items): "
                    "if intent_axis is business, omit when business_relevance_score < 20; "
                    "if technical, omit when technology_fit_score < 20; "
                    "if hybrid, omit when final_score < 20.\n"
                    "Never invent repo_id values; only use repo_id strings from Candidates."
                )
                user_msg = (
                    f"User Query: '{query_str}'\n\n"
                    "Return structured output: query_intent, intent_axis, intent_confidence, and items.\n"
                    f"Candidates (top vector hits):\n{_json.dumps(rerank_payload, indent=2)}"
                )

                print(f"[TOOLS] Initiating LLM re-rank for {len(rerank_pool)} top vector hit(s)...")
                model = get_chat_model()
                llm = model.with_structured_output(CatalogRerankOutput)

                output = llm.invoke([
                    SystemMessage(content=sys_prompt),
                    HumanMessage(content=user_msg),
                ])

                axis = getattr(output, "intent_axis", None) or "hybrid"
                if axis not in ("business", "technical", "hybrid"):
                    axis = "hybrid"

                def _canonical_final(b: int, t: int) -> int:
                    if axis == "business":
                        return int(b)
                    if axis == "technical":
                        return int(t)
                    return int(round(b * 0.5 + t * 0.5))

                def _keep_rerank_item(item) -> bool:
                    if getattr(item, "repo_id", None) not in allowed_ids:
                        return False
                    fin = _canonical_final(item.business_relevance_score, item.technology_fit_score)
                    if axis == "business":
                        return item.business_relevance_score >= 20
                    if axis == "technical":
                        return item.technology_fit_score >= 20
                    return fin >= 20

                filtered_items = [it for it in output.items if _keep_rerank_item(it)]
                seen: set[str] = set()
                ordered_items = []
                for it in filtered_items:
                    if it.repo_id in allowed_ids and it.repo_id not in seen:
                        seen.add(it.repo_id)
                        ordered_items.append(it)

                q_intent = getattr(output, "query_intent", "") or ""
                ic = getattr(output, "intent_confidence", 70)
                try:
                    ic = int(ic)
                except (TypeError, ValueError):
                    ic = 70
                ic = max(0, min(100, ic))

                final_results: list[dict] = []
                for r_item in ordered_items:
                    r = pool_by_id.get(r_item.repo_id)
                    if not r:
                        continue
                    r = dict(r)
                    fin = _canonical_final(
                        r_item.business_relevance_score,
                        r_item.technology_fit_score,
                    )
                    r["score"] = fin / 100.0
                    meta = _json.loads(r.get("metadata", "{}")) if r.get("metadata") else {}
                    meta["ai_business_score"] = r_item.business_relevance_score
                    meta["ai_tech_score"] = r_item.technology_fit_score
                    meta["ai_final_score"] = fin
                    meta["ai_insight"] = r_item.reasoning
                    meta["ai_query_intent"] = q_intent
                    meta["ai_intent_axis"] = axis
                    meta["ai_intent_confidence"] = ic
                    if getattr(r_item, "intent_alignment", None):
                        meta["ai_intent_alignment"] = r_item.intent_alignment
                    r["metadata"] = _json.dumps(meta)
                    final_results.append(r)

                for r in rerank_pool:
                    if r["repo_id"] not in {x["repo_id"] for x in final_results}:
                        rr = dict(r)
                        meta = _json.loads(rr.get("metadata", "{}")) if rr.get("metadata") else {}
                        meta["ai_query_intent"] = q_intent
                        meta["ai_intent_axis"] = axis
                        meta["ai_intent_confidence"] = ic
                        meta["ai_rerank_skipped"] = True
                        rr["metadata"] = _json.dumps(meta)
                        final_results.append(rr)

                tail = results[5:]
                merged = final_results + [dict(x) for x in tail]
                merged.sort(key=lambda x: x["score"], reverse=True)
                results = merged
                print(f"[TOOLS] LLM re-rank complete (axis={axis}). {len(final_results)} in top pool adjusted; {len(results)} total.")

            except Exception as e:
                print(f"[TOOLS] AI Re-ranking failed (non-fatal, falling back to dense vectors): {e}")
                import traceback
                traceback.print_exc()

        # ── Track search popularity ──────────────────────────────────
        # Increment search_count (+1) and popularity_points (+1) for
        # every catalog item that appeared in these results.
        if self.db and results:
            try:
                returned_rids = [r["repo_id"] for r in results if r.get("repo_id")]
                with self.db.get_session() as session:
                    for rid in returned_rids:
                        entry = session.query(CatalogStore).filter_by(repo_id=rid).first()
                        if entry:
                            entry.search_count = (entry.search_count or 0) + 1
                            entry.popularity_points = (entry.popularity_points or 0) + 1
                    session.commit()
            except Exception as e:
                print(f"[TOOLS] Popularity tracking failed (non-fatal): {e}")
        # ─────────────────────────────────────────────────────────────
            
        return results

    async def search_catalogs(self, params: dict) -> dict:
        """Search across all catalogs with optional repository filter."""
        print(f"[TOOLS] search_catalogs called with params: {params}")
        try:
            from ..storage.database import CatalogStore
            query = params.get("query", "")
            if not query and "queries" in params:
                query = params["queries"]
            
            if not query:
                return {"error": "No query provided", "results": [], "count": 0}
            
            queries = [query] if isinstance(query, str) else query
            repo_id = params.get("repo_id")
            limit = params.get("limit", 10)
            min_score = params.get("min_score", 0.5)
            
            if not self.embedder:
                return {"error": "No embedder available", "results": [], "count": 0}
            
            results = await self._search_catalogs_internal(
                queries=queries,
                repo_id=repo_id,
                limit=limit,
                min_score=min_score,
                ai_rerank=params.get("ai_rerank", False)
            )
            
            return {
                "success": True, 
                "results": results, 
                "count": len(results),
                "queries_used": queries
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e), "results": [], "count": 0}

    @staticmethod
    def _normalize_catalog_params(params: dict) -> dict:
        """Delegate to shared normalizer (also used by ``CatalogEntryWriter``)."""
        return normalize_catalog_params(params)

    async def save_catalog_entry(self, params: dict) -> dict:
        """Save or update a catalog entry (delegates to ``CatalogEntryWriter``).

        The ``generate_catalog`` playbook persists via the executor instead of
        this tool; the tool remains for APIs, tests, and other playbooks.
        """
        params = self._normalize_catalog_params(params)
        return await self._catalog_writer.persist(params)

    async def execute_tool(self, tool_name: str, params: dict) -> dict:
        """Dispatch a tool call by name.
        
        Args:
            tool_name: One of the registered tool names
            params: Tool-specific parameters
            
        Returns:
            Tool result dict
        """
        tools = {
            "get_map": self.get_map,
            "trace_path": self.trace_path,
            "graphify_query": self.graphify_query,
            "graphify_shortest_path": self.graphify_shortest_path,
            "graphify_path": self.graphify_path,
            "graphify_explain": self.graphify_explain,
            "graphify_neighbors": self.graphify_neighbors,
            "graphify_community": self.graphify_community,
            "graphify_god_nodes": self.graphify_god_nodes,
            "graphify_add": self.graphify_add,
            "graphify_run": self.graphify_run,
            "search_code": self.search_code,
            "search_bm25": self.search_bm25,
            "search_codebase": self.search_codebase,
            "read_file": self.read_file,
            "list_file_system": self.list_file_system,
            "read_file_system": self.read_file_system,
            "write_file_system": self.write_file_system,
            "get_file_outline": self.get_file_outline,
            "search_symbol": self.search_symbol,
            "get_callers": self.get_callers,
            "get_callees": self.get_callees,
            "get_dependencies": self.get_dependencies,
            "list_files": self.list_files,
            "list_repo_directory": self.list_repo_directory,
            "search_catalogs": self.search_catalogs,
            "save_catalog_entry": self.save_catalog_entry,
        }

        if tool_name not in tools:
            return {"error": f"Unknown tool: {tool_name}. Available: {list(tools.keys())}"}

        return await tools[tool_name](params)

    @staticmethod
    def get_tool_descriptions() -> list[dict]:
        """Return tool descriptions for LLM prompting."""
        return [
            {
                "name": "get_map",
                "description": (
                    "Phase A: Architectural GPS—your reading roadmap. Use FIRST. "
                    "High-degree nodes and entry points prioritize *what to read next* before opening files."
                ),
                "parameters": "repo_id (str), limit (int, optional, default 15)"
            },
            {
                "name": "trace_path",
                "description": (
                    "Phase B: Turn the map into an ordered path between two symbols/files. "
                    "Use the path to sequence read_file calls along the real dependency/call chain."
                ),
                "parameters": "repo_id (str), start (str), end (str)"
            },
            {
                "name": "graphify_query",
                "description": "Graphify query over graph.json with BFS/DFS and token budget (maps to /graphify query ... [--dfs] [--budget]).",
                "parameters": "repo_id (str), question (str), mode ('bfs'|'dfs', optional), depth (int, optional), budget (int, optional)"
            },
            {
                "name": "graphify_path",
                "description": "Find shortest path between two graph concepts (maps to /graphify path-like traversal).",
                "parameters": "repo_id (str), source (str), target (str), max_hops (int, optional)"
            },
            {
                "name": "graphify_shortest_path",
                "description": "Alias for graph shortest path between two concepts (same behavior as graphify_path).",
                "parameters": "repo_id (str), source (str), target (str), max_hops (int, optional)"
            },
            {
                "name": "graphify_explain",
                "description": "Explain a graph node with metadata and neighbors (maps to /graphify explain).",
                "parameters": "repo_id (str), term (str), include_neighbors (bool, optional), max_neighbors (int, optional)"
            },
            {
                "name": "graphify_neighbors",
                "description": "Get direct neighbors of a graph node with relation/confidence metadata.",
                "parameters": "repo_id (str), label (str), relation_filter (str, optional), max_neighbors (int, optional)"
            },
            {
                "name": "graphify_community",
                "description": "List members of one graph community (ordered by degree) for focused analysis slices.",
                "parameters": "repo_id (str), community_id (int), max_nodes (int, optional)"
            },
            {
                "name": "graphify_god_nodes",
                "description": "Return top high-degree architectural hubs (god nodes) for this repository graph.",
                "parameters": "repo_id (str), top_n (int, optional)"
            },
            {
                "name": "graphify_add",
                "description": "Ingest URL content (paper/tweet/video/web) into corpus; graph generation is indexing-only.",
                "parameters": "url (str), target_dir (str, optional), author (str, optional), contributor (str, optional), update_graph (bool, optional), deep_mode (bool, optional)"
            },
            {
                "name": "graphify_run",
                "description": "Regenerate graph-derived artifacts (report/html/obsidian) for an indexed repo_id using existing graph.json.",
                "parameters": "repo_id (str), no_viz (bool, optional), obsidian (bool, optional), obsidian_dir (str, optional)"
            },
            {
                "name": "search_code",
                "description": (
                    "Phase C: Surgical text search (Python regex scan; no external grep). "
                    "Use ONLY after narrowing down to specific modules/files via graph tools."
                ),
                "parameters": "query (str), repo_id (str, optional), includes (list[str], optional), limit (int, optional)"
            },
            {
                "name": "search_bm25",
                "description": (
                    "True lexical retrieval using SQLite FTS5 BM25 ranking over indexed chunks. "
                    "Use for exact-term relevance ranked better than regex grep."
                ),
                "parameters": "query (str) or queries (list[str]), repo_id (str), file_types (list[str], optional), limit (int, optional)"
            },
            {
                "name": "read_file",
                "description": (
                    "Read a file after the graph (get_map / trace_path / callers) has directed you here. "
                    "Do not use for exploratory browsing—follow the roadmap."
                ),
                "parameters": "file_path (str), repo_id (str), start_line (int, optional), end_line (int, optional)"
            },
            {
                "name": "get_file_outline",
                "description": (
                    "AST-level outline for one file (classes, methods, imports, line ranges). "
                    "Use before read_file on large modules—like an IDE outline / Claude Code file peek."
                ),
                "parameters": "repo_id (str), file_path (str)"
            },
            {
                "name": "search_symbol",
                "description": "Find a class or function by name. Useful for mapping graph node IDs back to files.",
                "parameters": "name (str), repo_id (str), symbol_type ('Class'|'Function', optional)"
            },
            {
                "name": "get_callers",
                "description": "Find all functions that call a given function. Shows who depends on it.",
                "parameters": "function_name (str), repo_id (str)"
            },
            {
                "name": "get_callees",
                "description": "Find all functions called by a given function. Shows what it depends on.",
                "parameters": "function_name (str), repo_id (str)"
            },
            {
                "name": "get_dependencies",
                "description": "Get file-level import dependencies. direction='imports' for what this file uses, 'imported_by' for what uses this file.",
                "parameters": "file_path (str), repo_id (str), direction ('imports'|'imported_by')"
            },
            {
                "name": "list_files",
                "description": (
                    "Graph-backed file discovery: substring or glob (e.g. ``*service*.py``) on indexed paths, "
                    "plus optional file_type (e.g. ``.py``). Prefer over blind directory walks when the graph is fresh."
                ),
                "parameters": "repo_id (str), pattern (str, optional), file_type (str, optional)"
            },
            {
                "name": "list_repo_directory",
                "description": "List files and folders under a path in the repo checkout on disk (manifest path). Use to browse directories when graph coverage is incomplete.",
                "parameters": "repo_id (str), relative_path (str, optional), recursive (bool, optional), max_entries (int, optional)"
            },
        ]
