"""
Graph query service for code structure navigation.

Provides high-level query methods over the SQLite graph database.
"""

from typing import Any

from sqlalchemy import text

from .graph_db import SQLiteGraphAdapter


class GraphQueryService:
    """Service for querying code structure using SQLite graph."""

    def __init__(self, graph_db: SQLiteGraphAdapter):
        """Initialize graph query service."""
        self.graph = graph_db

    def _execute(self, query: str, params: dict | None = None) -> list[Any]:
        """Execute a SQL query and return all rows."""
        with self.graph.db.engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            # Return mappings for easier access (requires SQLAlchemy 1.4+)
            return result.mappings().all()

    def find_files_by_pattern(
        self, repo_id: str, pattern: str | None = None, file_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Find files matching a pattern or file type."""
        try:
            query = """
                SELECT file_path
                FROM graph_nodes
                WHERE type = 'File' AND repo_id = :repo_id
            """
            params = {"repo_id": repo_id}

            if pattern:
                query += " AND file_path LIKE :pattern"
                params["pattern"] = f"%{pattern.replace('*', '%')}%"

            if file_type:
                query += " AND file_path LIKE :file_type"
                # Ensure dot prefix if missing? Assuming input has it usually.
                # If file_type is ".py", LIKE "%.py"
                if not file_type.startswith("%"):
                    params["file_type"] = f"%{file_type}"
                else:
                    params["file_type"] = file_type

            rows = self._execute(query, params)
            return [{"file_path": row["file_path"]} for row in rows]

        except Exception as e:
            print(f"[GRAPH_QUERY] Error finding files: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_classes_in_file(self, repo_id: str, file_path: str) -> list[dict[str, Any]]:
        """Get all classes defined in a file."""
        try:
            query = """
                SELECT target.name as class_name
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE source.type = 'File' 
                  AND source.file_path = :file_path
                  AND source.repo_id = :repo_id
                  AND e.type = 'DECLARES'
                  AND target.type = 'Class'
            """
            params = {"repo_id": repo_id, "file_path": file_path}
            rows = self._execute(query, params)
            return [{"class_name": row["class_name"]} for row in rows]

        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting classes: {e}")
            return []

    def get_functions_in_class(
        self, repo_id: str, class_name: str
    ) -> list[dict[str, Any]]:
        """Get all functions/methods in a class."""
        try:
            query = """
                SELECT target.name as function_name, target.file_path
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE source.type = 'Class'
                  AND source.name = :class_name
                  AND source.repo_id = :repo_id
                  AND e.type = 'HAS_METHOD'
                  AND target.type = 'Function'
            """
            params = {"repo_id": repo_id, "class_name": class_name}
            rows = self._execute(query, params)
            return [{"function_name": row["function_name"], "file_path": row["file_path"]} for row in rows]

        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting functions: {e}")
            return []

    def find_symbol_by_name(
        self, repo_id: str, name: str, symbol_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Find classes or functions by name (partial match)."""
        try:
            params = {"repo_id": repo_id, "name": f"%{name}%"}
            
            base_query = """
                SELECT type, name, file_path
                FROM graph_nodes
                WHERE repo_id = :repo_id AND name LIKE :name
            """
            
            if symbol_type:
                base_query += " AND type = :symbol_type"
                params["symbol_type"] = symbol_type
            else:
                base_query += " AND type IN ('Class', 'Function')"

            rows = self._execute(base_query, params)
            return [{"type": row["type"], "name": row["name"], "file_path": row["file_path"]} for row in rows]

        except Exception as e:
            print(f"[GRAPH_QUERY] Error finding symbol: {e}")
            return []

    def get_file_context(self, repo_id: str, file_path: str) -> dict[str, Any]:
        """Get structural context for a file (classes, functions)."""
        try:
            context = {
                "file_path": file_path,
                "classes": [],
                "functions": [],
            }

            # Get classes
            classes = self.get_classes_in_file(repo_id, file_path)
            context["classes"] = [c["class_name"] for c in classes]

            # Get top-level functions (DECLARES relationship from File)
            query = """
                SELECT target.name as function_name
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE source.type = 'File'
                  AND source.file_path = :file_path
                  AND source.repo_id = :repo_id
                  AND e.type = 'DECLARES'
                  AND target.type = 'Function'
            """
            params = {"repo_id": repo_id, "file_path": file_path}
            rows = self._execute(query, params)
            context["functions"] = [row["function_name"] for row in rows]

            return context

        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting file context: {e}")
            return {"file_path": file_path, "classes": [], "functions": []}

    def filter_by_structure(
        self, repo_id: str, filters: dict[str, Any]
    ) -> list[str]:
        """Get list of file paths matching structural filters."""
        # Reuse logic from find_files_by_pattern and find_symbol_by_name
        # Or implement minimal bridging logic
        # For brevity, implementing minimal logic mirroring previous impl
        import re
        
        file_paths = set()

        try:
            # === File Type/Pattern Filters ===
            file_type_matches = set()
            
            if "file_type" in filters:
                files = self.find_files_by_pattern(repo_id, file_type=filters["file_type"])
                file_type_matches.update(f["file_path"] for f in files)
            
            if "file_types" in filters:
                for ext in filters["file_types"]:
                    files = self.find_files_by_pattern(repo_id, file_type=ext)
                    file_type_matches.update(f["file_path"] for f in files)
            
            if "file_pattern" in filters:
                files = self.find_files_by_pattern(repo_id, pattern=filters["file_pattern"])
                file_type_matches.update(f["file_path"] for f in files)
                
            if "file_pattern_regex" in filters:
                regex = re.compile(filters["file_pattern_regex"])
                files = self.find_files_by_pattern(repo_id) # Get all files
                for f in files:
                    if regex.search(f["file_path"]):
                         file_type_matches.add(f["file_path"])

            if file_type_matches:
                file_paths = file_type_matches

            # === Symbol Filters ===
            if "class_name" in filters:
                symbols = self.find_symbol_by_name(repo_id, filters["class_name"], symbol_type="Class")
                class_files = {s["file_path"] for s in symbols}
                file_paths &= class_files if file_paths else class_files

            if "function_name" in filters:
                symbols = self.find_symbol_by_name(repo_id, filters["function_name"], symbol_type="Function")
                func_files = {s["file_path"] for s in symbols}
                file_paths &= func_files if file_paths else func_files

            # === Exclusions ===
            if "exclude_patterns" in filters:
                for pattern in filters["exclude_patterns"]:
                    file_paths = {f for f in file_paths if pattern not in f}

            return list(file_paths)

        except Exception as e:
            print(f"[GRAPH_QUERY] Error filtering: {e}")
            return []

    # -- Cross-file relationship queries --

    def get_callers(self, repo_id: str, func_name: str) -> list[dict]:
        """Find all functions that call the given function."""
        try:
            query = """
                SELECT source.name as function_name, source.file_path, e.properties
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE target.name = :func_name
                  AND target.type = 'Function'
                  AND target.repo_id = :repo_id
                  AND e.type = 'CALLS'
                  AND source.type = 'Function'
            """
            params = {"repo_id": repo_id, "func_name": func_name}
            rows = self._execute(query, params)
            return [{"function_name": row["function_name"], "file_path": row["file_path"], "line": row["properties"]} for row in rows]
        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting callers: {e}")
            return []

    def get_callees(self, repo_id: str, func_name: str) -> list[dict]:
        """Find all functions called by the given function."""
        try:
            query = """
                SELECT target.name as function_name, target.file_path
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE source.name = :func_name
                  AND source.type = 'Function'
                  AND source.repo_id = :repo_id
                  AND e.type = 'CALLS'
                  AND target.type = 'Function'
            """
            params = {"repo_id": repo_id, "func_name": func_name}
            rows = self._execute(query, params)
            return [{"function_name": row["function_name"], "file_path": row["file_path"]} for row in rows]
        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting callees: {e}")
            return []

    def get_dependency_chain(self, repo_id: str, file_path: str) -> list[dict]:
        """Get all files imported by this file (direct dependencies)."""
        try:
            query = """
                SELECT target.file_path
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE source.file_path = :file_path
                  AND source.type = 'File'
                  AND source.repo_id = :repo_id
                  AND e.type = 'IMPORTS'
                  AND target.type = 'File'
            """
            params = {"repo_id": repo_id, "file_path": file_path}
            rows = self._execute(query, params)
            return [{"file_path": row["file_path"]} for row in rows]
        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting dependencies: {e}")
            return []

    def get_file_dependents(self, repo_id: str, file_path: str) -> list[dict]:
        """Get all files that import this file."""
        try:
            query = """
                SELECT source.file_path
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE target.file_path = :file_path
                  AND target.type = 'File'
                  AND target.repo_id = :repo_id
                  AND e.type = 'IMPORTS'
                  AND source.type = 'File'
            """
            params = {"repo_id": repo_id, "file_path": file_path}
            rows = self._execute(query, params)
            return [{"file_path": row["file_path"]} for row in rows]
        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting dependents: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_dependents(self, repo_id: str, file_path: str) -> list[dict]:
        """Wrapper for get_file_dependents to match interface."""
        return self.get_file_dependents(repo_id, file_path)

    def get_impact_radius(self, repo_id: str, symbol_name: str) -> dict:
        """Get all symbols/files affected by changing this symbol."""
        try:
            # Reusing methods
            callers = self.get_callers(repo_id, symbol_name)
            affected_functions = callers
            affected_files = {c["file_path"] for c in callers}
            
            # Find definition file to check dependents
            symbols = self.find_symbol_by_name(repo_id, symbol_name)
            for sym in symbols:
                if sym.get("file_path"):
                    dependents = self.get_file_dependents(repo_id, sym["file_path"])
                    for d in dependents:
                        affected_files.add(d["file_path"])

            return {
                "symbol": symbol_name,
                "affected_functions": affected_functions,
                "affected_files": list(affected_files),
            }
        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting impact radius: {e}")
            return {"symbol": symbol_name, "affected_functions": [], "affected_files": []}

    def get_class_hierarchy(self, repo_id: str, class_name: str) -> dict:
        """Get parents and children (subclasses)."""
        try:
            # Parents: target of INHERITS_FROM
            parents_query = """
                SELECT target.name as class_name
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE source.name = :class_name
                  AND source.type = 'Class'
                  AND source.repo_id = :repo_id
                  AND e.type = 'INHERITS_FROM'
            """
            
            # Children: source of INHERITS_FROM
            children_query = """
                SELECT source.name as class_name
                FROM graph_edges e
                JOIN graph_nodes source ON e.source_id = source.id
                JOIN graph_nodes target ON e.target_id = target.id
                WHERE target.name = :class_name
                  AND target.type = 'Class'
                  AND target.repo_id = :repo_id
                  AND e.type = 'INHERITS_FROM'
            """
            
            params = {"repo_id": repo_id, "class_name": class_name}
            
            parents = [{"name": r["class_name"]} for r in self._execute(parents_query, params)]
            children = [{"name": r["class_name"]} for r in self._execute(children_query, params)]
            
            return {"class": class_name, "parents": parents, "children": children}
        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting class hierarchy: {e}")
            return {"class": class_name, "parents": [], "children": []}
