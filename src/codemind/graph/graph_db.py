"""
Graph database adapter for Kuzu.

Embedded graph database using Cypher queries for code structure storage.
Replaces the previous SQLite-based graph implementation.
"""

import os
from typing import Any
from dataclasses import dataclass
from pathlib import Path

import kuzu


@dataclass
class Node:
    """Graph node (legacy - for backward compatibility)."""

    id: str
    type: str  # Repository, File, Class, Function
    properties: dict[str, Any]


@dataclass
class Edge:
    """Graph edge (legacy - for backward compatibility)."""

    from_id: str
    to_id: str
    type: str  # CONTAINS, DECLARES, IMPORTS, CALLS
    properties: dict[str, Any]


class GraphDB:
    """Simple in-memory graph database (legacy)."""

    def __init__(self):
        """Initialize graph."""
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []

    def add_node(self, node: Node):
        """Add or update node (idempotent)."""
        self.nodes[node.id] = node

    def add_edge(self, edge: Edge):
        """Add edge if not exists."""
        for e in self.edges:
            if e.from_id == edge.from_id and e.to_id == edge.to_id and e.type == edge.type:
                return
        self.edges.append(edge)

    def get_node(self, node_id: str) -> Node | None:
        """Get node by ID."""
        return self.nodes.get(node_id)

    def query_edges(
        self,
        from_id: str | None = None,
        to_id: str | None = None,
        edge_type: str | None = None,
    ) -> list[Edge]:
        """Query edges with filters."""
        results = self.edges
        if from_id:
            results = [e for e in results if e.from_id == from_id]
        if to_id:
            results = [e for e in results if e.to_id == to_id]
        if edge_type:
            results = [e for e in results if e.type == edge_type]
        return results

    def clear(self):
        """Clear all data."""
        self.nodes.clear()
        self.edges.clear()


class KuzuGraphAdapter:
    """Adapter for graph operations on Kuzu embedded graph database."""

    def __init__(self, db_path: str | Path | None = None):
        """Initialize Kuzu graph database.
        
        Args:
            db_path: Path to Kuzu database directory. 
                     Defaults to CODEMIND_KUZU_PATH env or 'data/kuzu'.
        """
        if db_path is None:
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_KUZU_PATH", os.path.join(base_default, "kuzu"))
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db = kuzu.Database(str(self.db_path))
        self.conn = kuzu.Connection(self.db)
        
        self._init_schema()

    def _init_schema(self):
        """Create node and relationship tables if they don't exist."""
        # Node tables
        self.conn.execute(
            'CREATE NODE TABLE IF NOT EXISTS Repository('
            'id STRING, name STRING, path STRING, '
            'PRIMARY KEY(id))'
        )
        self.conn.execute(
            'CREATE NODE TABLE IF NOT EXISTS File('
            'id STRING, repo_id STRING, name STRING, path STRING, '
            'language STRING DEFAULT "", '
            'PRIMARY KEY(id))'
        )
        self.conn.execute(
            'CREATE NODE TABLE IF NOT EXISTS Class('
            'id STRING, repo_id STRING, name STRING, file_path STRING, '
            'start_line INT64 DEFAULT 0, end_line INT64 DEFAULT 0, '
            'PRIMARY KEY(id))'
        )
        self.conn.execute(
            'CREATE NODE TABLE IF NOT EXISTS Function('
            'id STRING, repo_id STRING, name STRING, file_path STRING, '
            'parent_class STRING DEFAULT "", '
            'start_line INT64 DEFAULT 0, end_line INT64 DEFAULT 0, '
            'PRIMARY KEY(id))'
        )
        self.conn.execute(
            'CREATE NODE TABLE IF NOT EXISTS Module('
            'id STRING, repo_id STRING, name STRING, path STRING, '
            'PRIMARY KEY(id))'
        )
        self.conn.execute(
            'CREATE NODE TABLE IF NOT EXISTS API('
            'id STRING, repo_id STRING, method STRING, route STRING, '
            'file_path STRING, '
            'PRIMARY KEY(id))'
        )

        # Relationship tables
        self.conn.execute('CREATE REL TABLE IF NOT EXISTS CONTAINS(FROM Repository TO File)')
        self.conn.execute('CREATE REL TABLE IF NOT EXISTS DECLARES(FROM File TO Class)')
        self.conn.execute('CREATE REL TABLE IF NOT EXISTS DECLARES_FUNC(FROM File TO Function)')
        self.conn.execute('CREATE REL TABLE IF NOT EXISTS HAS_METHOD(FROM Class TO Function)')
        self.conn.execute(
            'CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO File, '
            'module_name STRING DEFAULT "")'
        )
        self.conn.execute(
            'CREATE REL TABLE IF NOT EXISTS CALLS(FROM Function TO Function, '
            'line INT64 DEFAULT 0)'
        )
        self.conn.execute('CREATE REL TABLE IF NOT EXISTS INHERITS_FROM(FROM Class TO Class)')
        self.conn.execute('CREATE REL TABLE IF NOT EXISTS IMPLEMENTS(FROM Class TO Class)')
        self.conn.execute('CREATE REL TABLE IF NOT EXISTS HANDLED_BY(FROM API TO Function)')

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _execute(self, query: str, params: dict | None = None):
        """Execute a Cypher query with optional parameters."""
        try:
            if params:
                return self.conn.execute(query, params)
            return self.conn.execute(query)
        except Exception as e:
            # Log but don't crash — graph ops are non-fatal
            print(f"[KUZU] ⚠️ Query error: {e}\n  Query: {query[:200]}")
            return None

    def _result_to_list(self, result) -> list[list]:
        """Convert Kuzu result to list of rows."""
        if result is None:
            return []
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    # ── Public Interface (matches previous SQLiteGraphAdapter) ───────────

    def add_repository(self, repo_id: str, repo_path: str):
        """Add or update a repository node."""
        node_id = f"repo:{repo_id}"
        self._execute(
            'MERGE (r:Repository {id: $id}) '
            'SET r.name = $name, r.path = $path',
            {"id": node_id, "name": repo_id, "path": repo_path}
        )

    def delete_file_nodes(self, repo_id: str, file_paths: list[str]) -> int:
        """Delete File, Class, and Function nodes for specific file paths."""
        if not file_paths:
            return 0
            
        try:
            # Delete functions, classes, and the file node itself using DETACH DELETE
            # This safely removes connected edges first
            self._execute(
                'MATCH (n:Function) WHERE n.repo_id = $rid AND n.file_path IN $paths '
                'DETACH DELETE n',
                {"rid": repo_id, "paths": file_paths}
            )
            self._execute(
                'MATCH (c:Class) WHERE c.repo_id = $rid AND c.file_path IN $paths '
                'DETACH DELETE c',
                {"rid": repo_id, "paths": file_paths}
            )
            self._execute(
                'MATCH (f:File) WHERE f.repo_id = $rid AND f.path IN $paths '
                'DETACH DELETE f',
                {"rid": repo_id, "paths": file_paths}
            )
            print(f"[KUZU] ✅ Purged old graph nodes for {len(file_paths)} files")
            return len(file_paths)
        except Exception as e:
            print(f"[KUZU] ⚠️ Error deleting graph nodes: {e}")
            return 0

    def add_file(self, repo_id: str, file_path: str, language: str = ""):
        """Add or update a file node and link to repository."""
        node_id = f"file:{repo_id}:{file_path}"
        repo_node_id = f"repo:{repo_id}"
        
        self._execute(
            'MERGE (f:File {id: $id}) '
            'SET f.repo_id = $repo_id, f.name = $name, f.path = $path, f.language = $lang',
            {"id": node_id, "repo_id": repo_id, 
             "name": file_path.split("/")[-1], "path": file_path, "lang": language}
        )
        # Link to repo
        self._execute(
            'MATCH (r:Repository {id: $rid}), (f:File {id: $fid}) '
            'MERGE (r)-[:CONTAINS]->(f)',
            {"rid": repo_node_id, "fid": node_id}
        )

    def add_class(self, repo_id: str, file_path: str, class_name: str,
                  start_line: int = 0, end_line: int = 0):
        """Add or update a class node and link to file."""
        node_id = f"class:{repo_id}:{file_path}:{class_name}"
        file_node_id = f"file:{repo_id}:{file_path}"
        
        self._execute(
            'MERGE (c:Class {id: $id}) '
            'SET c.repo_id = $repo_id, c.name = $name, c.file_path = $fp, '
            'c.start_line = $sl, c.end_line = $el',
            {"id": node_id, "repo_id": repo_id, "name": class_name, 
             "fp": file_path, "sl": start_line, "el": end_line}
        )
        self._execute(
            'MATCH (f:File {id: $fid}), (c:Class {id: $cid}) '
            'MERGE (f)-[:DECLARES]->(c)',
            {"fid": file_node_id, "cid": node_id}
        )

    def add_function(self, repo_id: str, file_path: str, function_name: str,
                     parent_class: str | None = None, start_line: int = 0, end_line: int = 0):
        """Add or update a function node and link to parent."""
        if parent_class:
            node_id = f"func:{repo_id}:{file_path}:{parent_class}.{function_name}"
        else:
            node_id = f"func:{repo_id}:{file_path}:{function_name}"
        
        self._execute(
            'MERGE (fn:Function {id: $id}) '
            'SET fn.repo_id = $repo_id, fn.name = $name, fn.file_path = $fp, '
            'fn.parent_class = $pc, fn.start_line = $sl, fn.end_line = $el',
            {"id": node_id, "repo_id": repo_id, "name": function_name,
             "fp": file_path, "pc": parent_class or "", 
             "sl": start_line, "el": end_line}
        )
        
        if parent_class:
            parent_id = f"class:{repo_id}:{file_path}:{parent_class}"
            self._execute(
                'MATCH (c:Class {id: $cid}), (fn:Function {id: $fid}) '
                'MERGE (c)-[:HAS_METHOD]->(fn)',
                {"cid": parent_id, "fid": node_id}
            )
        else:
            file_node_id = f"file:{repo_id}:{file_path}"
            self._execute(
                'MATCH (f:File {id: $fid}), (fn:Function {id: $fnid}) '
                'MERGE (f)-[:DECLARES_FUNC]->(fn)',
                {"fid": file_node_id, "fnid": node_id}
            )

    def add_import_edge(self, repo_id: str, from_file: str, to_file: str, 
                        module_name: str = ""):
        """Add an import relationship between files."""
        from_id = f"file:{repo_id}:{from_file}"
        to_id = f"file:{repo_id}:{to_file}"
        self._execute(
            'MATCH (a:File {id: $fid}), (b:File {id: $tid}) '
            'MERGE (a)-[:IMPORTS {module_name: $mn}]->(b)',
            {"fid": from_id, "tid": to_id, "mn": module_name}
        )

    def add_call_edge(self, repo_id: str, caller_file: str, caller_func: str,
                      callee_file: str, callee_func: str, line: int = 0):
        """Add a function call relationship."""
        caller_id = f"func:{repo_id}:{caller_file}:{caller_func}"
        callee_id = f"func:{repo_id}:{callee_file}:{callee_func}"
        self._execute(
            'MATCH (a:Function {id: $cid}), (b:Function {id: $tid}) '
            'MERGE (a)-[:CALLS {line: $line}]->(b)',
            {"cid": caller_id, "tid": callee_id, "line": line}
        )

    def add_inheritance_edge(self, repo_id: str, child_file: str, child_class: str,
                             parent_file: str, parent_class: str):
        """Add a class inheritance relationship."""
        child_id = f"class:{repo_id}:{child_file}:{child_class}"
        parent_id = f"class:{repo_id}:{parent_file}:{parent_class}"
        self._execute(
            'MATCH (child:Class {id: $cid}), (parent:Class {id: $pid}) '
            'MERGE (child)-[:INHERITS_FROM]->(parent)',
            {"cid": child_id, "pid": parent_id}
        )

    def add_implements_edge(self, repo_id: str, class_file: str, class_name: str,
                            interface_file: str, interface_name: str):
        """Add a class→interface implementation relationship."""
        class_id = f"class:{repo_id}:{class_file}:{class_name}"
        interface_id = f"class:{repo_id}:{interface_file}:{interface_name}"
        self._execute(
            'MATCH (c:Class {id: $cid}), (i:Class {id: $iid}) '
            'MERGE (c)-[:IMPLEMENTS]->(i)',
            {"cid": class_id, "iid": interface_id}
        )

    def add_module(self, repo_id: str, module_name: str, module_path: str):
        """Add a module/package node."""
        node_id = f"module:{repo_id}:{module_path}"
        self._execute(
            'MERGE (m:Module {id: $id}) '
            'SET m.repo_id = $repo_id, m.name = $name, m.path = $path',
            {"id": node_id, "repo_id": repo_id, "name": module_name, "path": module_path}
        )

    def add_api_handler(self, repo_id: str, method: str, route: str,
                        file_path: str, function_name: str):
        """Add an API endpoint node and link to its handler function."""
        api_id = f"api:{repo_id}:{method}:{route}"
        func_id = f"func:{repo_id}:{file_path}:{function_name}"
        
        self._execute(
            'MERGE (a:API {id: $id}) '
            'SET a.repo_id = $repo_id, a.method = $method, a.route = $route, '
            'a.file_path = $fp',
            {"id": api_id, "repo_id": repo_id, "method": method, 
             "route": route, "fp": file_path}
        )
        self._execute(
            'MATCH (a:API {id: $aid}), (fn:Function {id: $fid}) '
            'MERGE (a)-[:HANDLED_BY]->(fn)',
            {"aid": api_id, "fid": func_id}
        )

    def close(self):
        """Close graph database connection."""
        # Kuzu handles cleanup on garbage collection
        self.conn = None
        self.db = None


# Backward compatibility alias
SQLiteGraphAdapter = KuzuGraphAdapter


class GraphBuilder:
    """Builds code graph from AST and imports using Kuzu adapter."""

    def __init__(self, graph_db: KuzuGraphAdapter | None):
        """Initialize builder. graph_db may be None."""
        self.graph = graph_db
        self.is_noop = graph_db is None

    def delete_file_nodes(self, repo_id: str, file_paths: list[str]) -> None:
        if self.is_noop: return
        print(f"[GRAPH] Deleting graph nodes for {len(file_paths)} files")
        self.graph.delete_file_nodes(repo_id, file_paths)

    def build_repository_node(self, repo_id: str, repo_path: str) -> None:
        if self.is_noop: return
        print(f"[GRAPH] Creating repository node: {repo_id}")
        self.graph.add_repository(repo_id, repo_path)

    def build_file_node(self, repo_id: str, file_path: str, relative_path: str) -> None:
        if self.is_noop: return
        print(f"[GRAPH] Building file node: {relative_path}")
        self.graph.add_file(repo_id, file_path)

    def build_class_node(self, repo_id: str, file_path: str, class_name: str):
        if self.is_noop: return
        self.graph.add_class(repo_id, file_path, class_name)

    def build_function_node(self, repo_id: str, file_path: str, function_name: str,
                            parent_class: str | None = None):
        if self.is_noop: return
        self.graph.add_function(repo_id, file_path, function_name, parent_class)

    def build_import_edges(self, repo_id: str, from_file: str, to_file: str, import_name: str):
        if self.is_noop: return
        self.graph.add_import_edge(repo_id, from_file, to_file, module_name=import_name)

    def build_call_edges(self, repo_id: str, caller_file: str, caller_func: str,
                         callee_file: str, callee_func: str, line: int = 0):
        if self.is_noop: return
        self.graph.add_call_edge(repo_id, caller_file, caller_func, callee_file, callee_func, line)

    def build_inheritance_edges(self, repo_id: str, child_file: str, child_class: str,
                                parent_file: str, parent_class: str):
        if self.is_noop: return
        self.graph.add_inheritance_edge(repo_id, child_file, child_class, parent_file, parent_class)
