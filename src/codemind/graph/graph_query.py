"""
Graph query service for code structure navigation.

Provides high-level query methods over the Kuzu graph database.
"""

from typing import Any
import re

from .graph_db import KuzuGraphAdapter

# Pattern to strip data/repos/{name}/{branch}/ prefix from LanceDB file paths
# e.g. "data/repos/promptshield/main/backend/main.py" → "backend/main.py"
_REPO_PATH_PREFIX = re.compile(r'^data/repos/[^/]+/[^/]+/')


class GraphQueryService:
    """Service for querying code structure using Kuzu graph."""

    def __init__(self, graph_db: KuzuGraphAdapter):
        """Initialize graph query service."""
        self.graph = graph_db

    def _normalize_path(self, file_path: str) -> str:
        """Normalize a file path by stripping the data/repos/{name}/{branch}/ prefix.
        
        LanceDB stores full paths like 'data/repos/promptshield/main/backend/main.py'
        but Kuzu stores relative paths like 'backend/main.py'.
        """
        return _REPO_PATH_PREFIX.sub('', file_path)

    def _execute(self, query: str, params: dict | None = None) -> list[list]:
        """Execute a Cypher query and return all rows."""
        result = self.graph._execute(query, params)
        return self.graph._result_to_list(result)

    def find_files_by_pattern(
        self, repo_id: str, pattern: str | None = None, file_type: str | None = None
    ) -> list[dict]:
        """Find files matching a pattern or file type."""
        repo_node_id = f"repo:{repo_id}"
        
        if pattern and file_type:
            rows = self._execute(
                'MATCH (r:Repository {id: $rid})-[:CONTAINS]->(f:File) '
                'WHERE f.path CONTAINS $pattern AND f.path ENDS WITH $ext '
                'RETURN f.path, f.name, f.language',
                {"rid": repo_node_id, "pattern": pattern, "ext": file_type}
            )
        elif pattern:
            rows = self._execute(
                'MATCH (r:Repository {id: $rid})-[:CONTAINS]->(f:File) '
                'WHERE f.path CONTAINS $pattern '
                'RETURN f.path, f.name, f.language',
                {"rid": repo_node_id, "pattern": pattern}
            )
        elif file_type:
            rows = self._execute(
                'MATCH (r:Repository {id: $rid})-[:CONTAINS]->(f:File) '
                'WHERE f.path ENDS WITH $ext '
                'RETURN f.path, f.name, f.language',
                {"rid": repo_node_id, "ext": file_type}
            )
        else:
            rows = self._execute(
                'MATCH (r:Repository {id: $rid})-[:CONTAINS]->(f:File) '
                'RETURN f.path, f.name, f.language',
                {"rid": repo_node_id}
            )

        return [
            {"file_path": row[0], "name": row[1], "language": row[2]}
            for row in rows
        ]

    def get_classes_in_file(self, repo_id: str, file_path: str) -> list[dict]:
        """Get all classes defined in a file."""
        file_path = self._normalize_path(file_path)
        file_node_id = f"file:{repo_id}:{file_path}"

        rows = self._execute(
            'MATCH (f:File {id: $fid})-[:DECLARES]->(c:Class) '
            'RETURN c.name, c.start_line, c.end_line',
            {"fid": file_node_id}
        )
        return [
            {"name": row[0], "start_line": row[1], "end_line": row[2]}
            for row in rows
        ]

    def get_functions_in_class(self, repo_id: str, class_name: str) -> list[dict]:
        """Get all functions/methods in a class."""
        rows = self._execute(
            'MATCH (c:Class)-[:HAS_METHOD]->(fn:Function) '
            'WHERE c.repo_id = $rid AND c.name = $name '
            'RETURN fn.name, fn.file_path, fn.start_line, fn.end_line',
            {"rid": repo_id, "name": class_name}
        )
        return [
            {"name": row[0], "file_path": row[1], 
             "start_line": row[2], "end_line": row[3]}
            for row in rows
        ]

    def find_symbol_by_name(
        self, repo_id: str, name: str, symbol_type: str | None = None
    ) -> list[dict]:
        """Find classes or functions by name (partial match)."""
        results = []
        name_lower = name.lower()

        if symbol_type is None or symbol_type.lower() in ("class", "interface", "struct"):
            rows = self._execute(
                'MATCH (c:Class) '
                'WHERE c.repo_id = $rid AND lower(c.name) CONTAINS $name '
                'RETURN c.name, c.file_path, c.start_line, c.end_line, "Class"',
                {"rid": repo_id, "name": name_lower}
            )
            results.extend([
                {"name": r[0], "file_path": r[1], "start_line": r[2],
                 "end_line": r[3], "type": r[4]}
                for r in rows
            ])

        if symbol_type is None or symbol_type.lower() in ("function", "method"):
            rows = self._execute(
                'MATCH (fn:Function) '
                'WHERE fn.repo_id = $rid AND lower(fn.name) CONTAINS $name '
                'RETURN fn.name, fn.file_path, fn.start_line, fn.end_line, '
                'CASE WHEN fn.parent_class <> "" THEN "Method" ELSE "Function" END',
                {"rid": repo_id, "name": name_lower}
            )
            results.extend([
                {"name": r[0], "file_path": r[1], "start_line": r[2],
                 "end_line": r[3], "type": r[4]}
                for r in rows
            ])

        return results

    def get_file_context(self, repo_id: str, file_path: str) -> dict[str, Any]:
        """Get structural context for a file (classes, functions)."""
        file_path = self._normalize_path(file_path)
        file_node_id = f"file:{repo_id}:{file_path}"

        # Classes in file
        class_rows = self._execute(
            'MATCH (f:File {id: $fid})-[:DECLARES]->(c:Class) '
            'RETURN c.name, c.start_line, c.end_line',
            {"fid": file_node_id}
        )
        classes = [
            {"name": r[0], "start_line": r[1], "end_line": r[2]}
            for r in class_rows
        ]

        # Functions in file (top-level)
        func_rows = self._execute(
            'MATCH (f:File {id: $fid})-[:DECLARES_FUNC]->(fn:Function) '
            'RETURN fn.name, fn.start_line, fn.end_line',
            {"fid": file_node_id}
        )
        functions = [
            {"name": r[0], "start_line": r[1], "end_line": r[2]}
            for r in func_rows
        ]

        # Methods in classes for this file
        method_rows = self._execute(
            'MATCH (f:File {id: $fid})-[:DECLARES]->(c:Class)-[:HAS_METHOD]->(fn:Function) '
            'RETURN c.name, fn.name, fn.start_line, fn.end_line',
            {"fid": file_node_id}
        )
        methods = [
            {"class": r[0], "name": r[1], "start_line": r[2], "end_line": r[3]}
            for r in method_rows
        ]

        # Imports from this file
        import_rows = self._execute(
            'MATCH (f:File {id: $fid})-[:IMPORTS]->(dep:File) '
            'RETURN dep.path',
            {"fid": file_node_id}
        )
        imports = [r[0] for r in import_rows]

        return {
            "file_path": file_path,
            "classes": classes,
            "functions": functions,
            "methods": methods,
            "imports": imports,
        }

    def filter_by_structure(
        self, repo_id: str, filters: dict[str, Any]
    ) -> list[str]:
        """Get list of file paths matching structural filters."""
        file_paths = set()

        # Filter by file type extension
        file_type = filters.get("file_type")
        if file_type:
            results = self.find_files_by_pattern(repo_id, file_type=file_type)
            file_paths.update(r["file_path"] for r in results)

        # Filter by class name
        class_name = filters.get("class_name")
        if class_name:
            rows = self._execute(
                'MATCH (c:Class) '
                'WHERE c.repo_id = $rid AND lower(c.name) CONTAINS $name '
                'RETURN c.file_path',
                {"rid": repo_id, "name": class_name.lower()}
            )
            file_paths.update(r[0] for r in rows)

        # Filter by function name
        func_name = filters.get("function_name")
        if func_name:
            rows = self._execute(
                'MATCH (fn:Function) '
                'WHERE fn.repo_id = $rid AND lower(fn.name) CONTAINS $name '
                'RETURN fn.file_path',
                {"rid": repo_id, "name": func_name.lower()}
            )
            file_paths.update(r[0] for r in rows)

        # Filter by pattern (path substring)
        pattern = filters.get("pattern")
        if pattern:
            results = self.find_files_by_pattern(repo_id, pattern=pattern)
            file_paths.update(r["file_path"] for r in results)

        return list(file_paths)

    # ── Call Graph Queries ───────────────────────────────────────────────

    def get_callers(self, repo_id: str, func_name: str) -> list[dict]:
        """Find all functions that call the given function."""
        rows = self._execute(
            'MATCH (caller:Function)-[c:CALLS]->(fn:Function) '
            'WHERE fn.repo_id = $rid AND fn.name = $name '
            'RETURN caller.name, caller.file_path, c.line',
            {"rid": repo_id, "name": func_name}
        )
        return [
            {"name": r[0], "file_path": r[1], "line": r[2]}
            for r in rows
        ]

    def get_callees(self, repo_id: str, func_name: str) -> list[dict]:
        """Find all functions called by the given function."""
        rows = self._execute(
            'MATCH (fn:Function)-[c:CALLS]->(callee:Function) '
            'WHERE fn.repo_id = $rid AND fn.name = $name '
            'RETURN callee.name, callee.file_path, c.line',
            {"rid": repo_id, "name": func_name}
        )
        return [
            {"name": r[0], "file_path": r[1], "line": r[2]}
            for r in rows
        ]

    # ── Dependency Queries ───────────────────────────────────────────────

    def get_dependency_chain(self, repo_id: str, file_path: str) -> list[dict]:
        """Get all files imported by this file (direct dependencies)."""
        file_path = self._normalize_path(file_path)
        file_node_id = f"file:{repo_id}:{file_path}"
        rows = self._execute(
            'MATCH (f:File {id: $fid})-[i:IMPORTS]->(dep:File) '
            'RETURN dep.path, i.module_name',
            {"fid": file_node_id}
        )
        return [
            {"file_path": r[0], "module_name": r[1]}
            for r in rows
        ]

    def get_file_dependents(self, repo_id: str, file_path: str) -> list[dict]:
        """Get all files that import this file."""
        file_path = self._normalize_path(file_path)
        file_node_id = f"file:{repo_id}:{file_path}"
        rows = self._execute(
            'MATCH (dep:File)-[i:IMPORTS]->(f:File {id: $fid}) '
            'RETURN dep.path, i.module_name',
            {"fid": file_node_id}
        )
        return [
            {"file_path": r[0], "module_name": r[1]}
            for r in rows
        ]

    def get_dependents(self, repo_id: str, file_path: str) -> list[dict]:
        """Wrapper for get_file_dependents to match interface."""
        return self.get_file_dependents(repo_id, file_path)

    # ── Impact Analysis ──────────────────────────────────────────────────

    def get_impact_radius(self, repo_id: str, symbol_name: str) -> list[dict]:
        """Get all symbols/files affected by changing this symbol.
        
        Uses multi-hop graph traversal — this is where Kuzu shines over SQLite.
        """
        results = []

        # Direct callers (1 hop)
        caller_rows = self._execute(
            'MATCH (caller:Function)-[:CALLS]->(fn:Function) '
            'WHERE fn.repo_id = $rid AND fn.name = $name '
            'RETURN caller.name, caller.file_path, 1 AS depth, "calls" AS relation',
            {"rid": repo_id, "name": symbol_name}
        )
        results.extend([
            {"name": r[0], "file_path": r[1], "depth": r[2], "relation": r[3]}
            for r in caller_rows
        ])

        # Transitive callers (2-3 hops) — Kuzu handles this natively
        transitive_rows = self._execute(
            'MATCH (caller:Function)-[:CALLS*2..3]->(fn:Function) '
            'WHERE fn.repo_id = $rid AND fn.name = $name '
            'RETURN DISTINCT caller.name, caller.file_path',
            {"rid": repo_id, "name": symbol_name}
        )
        for r in transitive_rows:
            if not any(x["name"] == r[0] and x["file_path"] == r[1] for x in results):
                results.append({
                    "name": r[0], "file_path": r[1], 
                    "depth": 2, "relation": "transitive_call"
                })

        # Files that depend on the file containing this symbol
        func_rows = self._execute(
            'MATCH (fn:Function) WHERE fn.repo_id = $rid AND fn.name = $name '
            'RETURN fn.file_path',
            {"rid": repo_id, "name": symbol_name}
        )
        for func_row in func_rows:
            file_path = func_row[0]
            dep_rows = self.get_file_dependents(repo_id, file_path)
            for dep in dep_rows:
                if not any(x.get("file_path") == dep["file_path"] for x in results):
                    results.append({
                        "name": dep["file_path"].split("/")[-1],
                        "file_path": dep["file_path"],
                        "depth": 1,
                        "relation": "imports"
                    })

        return results

    # ── Class Hierarchy ──────────────────────────────────────────────────

    def get_class_hierarchy(self, repo_id: str, class_name: str) -> dict:
        """Get parents and children (subclasses)."""
        # Parents (superclasses) — multi-hop
        parent_rows = self._execute(
            'MATCH (c:Class)-[:INHERITS_FROM*1..5]->(parent:Class) '
            'WHERE c.repo_id = $rid AND c.name = $name '
            'RETURN parent.name, parent.file_path',
            {"rid": repo_id, "name": class_name}
        )
        parents = [
            {"name": r[0], "file_path": r[1]}
            for r in parent_rows
        ]

        # Children (subclasses)
        child_rows = self._execute(
            'MATCH (child:Class)-[:INHERITS_FROM]->(c:Class) '
            'WHERE c.repo_id = $rid AND c.name = $name '
            'RETURN child.name, child.file_path',
            {"rid": repo_id, "name": class_name}
        )
        children = [
            {"name": r[0], "file_path": r[1]}
            for r in child_rows
        ]

        # Implementations
        impl_rows = self._execute(
            'MATCH (impl:Class)-[:IMPLEMENTS]->(c:Class) '
            'WHERE c.repo_id = $rid AND c.name = $name '
            'RETURN impl.name, impl.file_path',
            {"rid": repo_id, "name": class_name}
        )
        implementations = [
            {"name": r[0], "file_path": r[1]}
            for r in impl_rows
        ]

        return {
            "class_name": class_name,
            "parents": parents,
            "children": children,
            "implementations": implementations,
        }

    # ── API Queries ──────────────────────────────────────────────────────

    def get_api_endpoints(self, repo_id: str) -> list[dict]:
        """Get all API endpoints and their handlers."""
        rows = self._execute(
            'MATCH (a:API)-[:HANDLED_BY]->(fn:Function) '
            'WHERE a.repo_id = $rid '
            'RETURN a.method, a.route, fn.name, fn.file_path',
            {"rid": repo_id}
        )
        return [
            {"method": r[0], "route": r[1], 
             "handler": r[2], "file_path": r[3]}
            for r in rows
        ]
