"""
Graph query service for code structure navigation.

Provides high-level query methods over the Kùzu graph database.
"""

from typing import Any

from .kuzu_graph import KuzuGraphDB


class GraphQueryService:
    """Service for querying code structure using Kùzu graph."""

    def __init__(self, graph_db: KuzuGraphDB):
        """Initialize graph query service."""
        self.graph = graph_db

    def find_files_by_pattern(
        self, repo_id: str, pattern: str | None = None, file_type: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Find files matching a pattern or file type.

        Args:
            repo_id: Repository ID
            pattern: File path pattern (e.g., "test_*.py", "*/models/*")
            file_type: File extension (e.g., ".py", ".js")

        Returns:
            List of file dictionaries with path and metadata
        """
        try:
            query = """
                MATCH (r:Repository {repo_id: $repo_id})-[:CONTAINS]->(f:File)
                WHERE 1=1
            """
            params = {"repo_id": repo_id}

            if pattern:
                # Simple pattern matching (can be enhanced with regex)
                query += " AND f.path CONTAINS $pattern"
                params["pattern"] = pattern.replace("*", "")

            if file_type:
                query += " AND f.path ENDS WITH $file_type"
                params["file_type"] = file_type

            query += " RETURN f.path AS file_path"

            result = self.graph.conn.execute(query, params)
            rows = []
            while result.has_next():
                batch = result.get_next()
                rows.extend([{"file_path": row[0]} for row in batch])
            return rows

        except Exception as e:
            print(f"[GRAPH_QUERY] Error finding files: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_classes_in_file(self, repo_id: str, file_path: str) -> list[dict[str, Any]]:
        """
        Get all classes defined in a file.

        Args:
            repo_id: Repository ID
            file_path: File path

        Returns:
            List of class dictionaries with name and metadata
        """
        try:
            query = """
                MATCH (f:File {repo_id: $repo_id, path: $file_path})-[:DECLARES_CLASS]->(c:Class)
                RETURN c.name AS class_name
            """
            params = {"repo_id": repo_id, "file_path": file_path}

            result = self.graph.conn.execute(query, params)
            rows = []
            while result.has_next():
                rows.extend([{"class_name": row[0]} for row in result.get_next()])
            return rows

        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting classes: {e}")
            return []

    def get_functions_in_class(
        self, repo_id: str, class_name: str
    ) -> list[dict[str, Any]]:
        """
        Get all functions/methods in a class.

        Args:
            repo_id: Repository ID
            class_name: Class name

        Returns:
            List of function dictionaries
        """
        try:
            query = """
                MATCH (c:Class {repo_id: $repo_id, name: $class_name})-[:HAS_METHOD]->(fn:Function)
                RETURN fn.name AS function_name, fn.file_path AS file_path
            """
            params = {"repo_id": repo_id, "class_name": class_name}

            result = self.graph.conn.execute(query, params)
            rows = []
            while result.has_next():
                rows.extend([{"function_name": row[0], "file_path": row[1]} for row in result.get_next()])
            return rows

        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting functions: {e}")
            return []

    def find_symbol_by_name(
        self, repo_id: str, name: str, symbol_type: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Find classes or functions by name.

        Args:
            repo_id: Repository ID
            name: Symbol name (supports partial matching)
            symbol_type: "Class" or "Function" (None for both)

        Returns:
            List of symbol dictionaries
        """
        try:
            if symbol_type == "Class":
                query = """
                    MATCH (c:Class {repo_id: $repo_id})
                    WHERE c.name CONTAINS $name
                    RETURN 'Class' AS type, c.name AS name, c.file_path AS file_path
                """
            elif symbol_type == "Function":
                query = """
                    MATCH (fn:Function {repo_id: $repo_id})
                    WHERE fn.name CONTAINS $name
                    RETURN 'Function' AS type, fn.name AS name, fn.file_path AS file_path
                """
            else:
                # Search both
                query = """
                    MATCH (c:Class {repo_id: $repo_id})
                    WHERE c.name CONTAINS $name
                    RETURN 'Class' AS type, c.name AS name, c.file_path AS file_path
                    UNION
                    MATCH (fn:Function {repo_id: $repo_id})
                    WHERE fn.name CONTAINS $name
                    RETURN 'Function' AS type, fn.name AS name, fn.file_path AS file_path
                """

            params = {"repo_id": repo_id, "name": name}
            result = self.graph.conn.execute(query, params)
            rows = []
            while result.has_next():
                rows.extend([{"type": row[0], "name": row[1], "file_path": row[2]} for row in result.get_next()])
            return rows

        except Exception as e:
            print(f"[GRAPH_QUERY] Error finding symbol: {e}")
            return []

    def get_file_context(self, repo_id: str, file_path: str) -> dict[str, Any]:
        """
        Get structural context for a file (classes, functions).

        Args:
            repo_id: Repository ID
            file_path: File path

        Returns:
            Dictionary with file structure
        """
        try:
            context = {
                "file_path": file_path,
                "classes": [],
                "functions": [],
            }

            # Get classes
            classes = self.get_classes_in_file(repo_id, file_path)
            context["classes"] = [c["class_name"] for c in classes]

            # Get all functions declared in file
            # Note: This includes both class methods and top-level functions
            try:
                query = """
                    MATCH (f:File {repo_id: $repo_id, path: $file_path})-[:DECLARES_FUNCTION]->(fn:Function)
                    RETURN fn.name AS function_name
                """
                params = {"repo_id": repo_id, "file_path": file_path}
                result = self.graph.conn.execute(query, params)
                funcs = []
                while result.has_next():
                    funcs.extend([row[0] for row in result.get_next()])
                context["functions"] = funcs
            except:
                # If DECLARES_FUNCTION relationship doesn't exist, skip functions
                context["functions"] = []

            return context

        except Exception as e:
            print(f"[GRAPH_QUERY] Error getting file context: {e}")
            return {"file_path": file_path, "classes": [], "functions": []}

    def filter_by_structure(
        self, repo_id: str, filters: dict[str, Any]
    ) -> list[str]:
        """
        Get list of file paths matching structural filters.

        Args:
            repo_id: Repository ID
            filters: Filter dict with support for:
                - Single values: {"file_type": ".py"}
                - OR logic (arrays): {"file_types": [".py", ".js"]}
                - Regex: {"file_pattern_regex": "^tests/.*_test\\.py$"}
                - Exclusions: {"exclude_patterns": ["__pycache__", "node_modules"]}
                - Code structure: {"has_decorator": "@app.get", "inherits_from": "BaseModel"}

        Returns:
            List of file paths that match the filters
        """
        import re
        
        file_paths = set()

        try:
            # === File Type/Pattern Filters (with OR support) ===
            file_type_matches = set()
            
            # Single file type
            if "file_type" in filters:
                files = self.find_files_by_pattern(
                    repo_id, file_type=filters["file_type"]
                )
                file_type_matches.update(f["file_path"] for f in files)
            
            # Multiple file types (OR logic)
            if "file_types" in filters:
                for ext in filters["file_types"]:
                    files = self.find_files_by_pattern(repo_id, file_type=ext)
                    file_type_matches.update(f["file_path"] for f in files)
            
            # File pattern
            if "file_pattern" in filters:
                files = self.find_files_by_pattern(
                    repo_id, pattern=filters["file_pattern"]
                )
                file_type_matches.update(f["file_path"] for f in files)
            
            # Multiple patterns (OR logic)
            if "file_patterns" in filters:
                for pattern in filters["file_patterns"]:
                    files = self.find_files_by_pattern(repo_id, pattern=pattern)
                    file_type_matches.update(f["file_path"] for f in files)
            
            # Regex pattern matching
            if "file_pattern_regex" in filters:
                regex = re.compile(filters["file_pattern_regex"])
                # Get all files and filter by regex
                query = """
                    MATCH (r:Repository {repo_id: $repo_id})-[:CONTAINS]->(f:File)
                    RETURN f.path AS file_path
                """
                result = self.graph.conn.execute(query, {"repo_id": repo_id})
                all_files = []
                while result.has_next():
                    all_files.extend([row[0] for row in result.get_next()])
                file_type_matches.update(f for f in all_files if regex.search(f))
            
            if file_type_matches:
                file_paths = file_type_matches

            # === Class Name Filters (with OR support) ===
            if "class_name" in filters or "class_names" in filters:
                class_files = set()
                
                # Single class
                if "class_name" in filters:
                    symbols = self.find_symbol_by_name(
                        repo_id, filters["class_name"], symbol_type="Class"
                    )
                    class_files.update(s["file_path"] for s in symbols)
                
                # Multiple classes (OR)
                if "class_names" in filters:
                    for class_name in filters["class_names"]:
                        symbols = self.find_symbol_by_name(
                            repo_id, class_name, symbol_type="Class"
                        )
                        class_files.update(s["file_path"] for s in symbols)
                
                # AND with previous filters
                if file_paths:
                    file_paths &= class_files
                else:
                    file_paths = class_files

            # === Function Name Filters (with OR support) ===
            if "function_name" in filters or "function_names" in filters:
                func_files = set()
                
                # Single function
                if "function_name" in filters:
                    symbols = self.find_symbol_by_name(
                        repo_id, filters["function_name"], symbol_type="Function"
                    )
                    func_files.update(s["file_path"] for s in symbols)
                
                # Multiple functions (OR)
                if "function_names" in filters:
                    for func_name in filters["function_names"]:
                        symbols = self.find_symbol_by_name(
                            repo_id, func_name, symbol_type="Function"
                        )
                        func_files.update(s["file_path"] for s in symbols)
                
                # AND with previous filters
                if file_paths:
                    file_paths &= func_files
                else:
                    file_paths = func_files

            # === Exclusion Filters ===
            if "exclude_patterns" in filters:
                for pattern in filters["exclude_patterns"]:
                    file_paths = {
                        f for f in file_paths 
                        if pattern not in f
                    }
            
            if "exclude_pattern_regex" in filters:
                regex = re.compile(filters["exclude_pattern_regex"])
                file_paths = {
                    f for f in file_paths 
                    if not regex.search(f)
                }

            # === Advanced Code Structure Filters ===
            # Note: These require AST metadata in graph which may not be fully populated yet
            
            # Has decorator filter
            if "has_decorator" in filters:
                # This would require decorator info in the graph
                # For now, we'll do a simple name-based filter
                decorator = filters["has_decorator"].replace("@", "")
                symbols = self.find_symbol_by_name(repo_id, decorator)
                decorator_files = {s["file_path"] for s in symbols}
                
                if file_paths:
                    file_paths &= decorator_files
                else:
                    file_paths = decorator_files
            
            # Inherits from filter (would need graph relationships)
            if "inherits_from" in filters:
                # Similar - would need inheritance data in graph
                parent_class = filters["inherits_from"]
                symbols = self.find_symbol_by_name(repo_id, parent_class, symbol_type="Class")
                inheritance_files = {s["file_path"] for s in symbols}
                
                if file_paths:
                    file_paths &= inheritance_files
                else:
                    file_paths = inheritance_files

            return list(file_paths)

        except Exception as e:
            print(f"[GRAPH_QUERY] Error filtering by structure: {e}")
            import traceback
            traceback.print_exc()
            return []
