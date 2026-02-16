"""
Graph database adapter for SQLite.

Replaces the previous KuzuDB implementation with a lightweight, concurrent
SQLite-based graph storage using Recursive CTEs for queries.
"""

from typing import Any
from dataclasses import dataclass

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from codemind.storage.database import Database, GraphEdge, GraphNode


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
        # Check for duplicate
        for e in self.edges:
            if e.from_id == edge.from_id and e.to_id == edge.to_id and e.type == edge.type:
                return  # Already exists
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


class SQLiteGraphAdapter:
    """Adapter for graph operations on SQLite."""

    def __init__(self, db: Database | None = None):
        """Initialize adapter."""
        self.db = db or Database()

    def _upsert_node(self, node_id: str, node_type: str, repo_id: str, 
                     name: str, file_path: str, properties: dict | None = None,
                     start_line: int = 0, end_line: int = 0):
        """Insert or update a node."""
        with self.db.get_session() as session:
            stmt = insert(GraphNode).values(
                id=node_id,
                type=node_type,
                repo_id=repo_id,
                name=name,
                file_path=file_path,
                properties=properties,
                start_line=start_line,
                end_line=end_line,
            )
            # Do update on conflict to ensure idempotency
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": name,
                    "properties": properties,
                    "start_line": start_line,
                    "end_line": end_line,
                },
            )
            session.execute(stmt)
            session.commit()

    def _add_edge(self, source_id: str, target_id: str, edge_type: str, properties: dict | None = None):
        """Add an edge if it doesn't exist."""
        with self.db.get_session() as session:
            # Check if edge exists (to avoid unique constraint error spam)
            # Although INSERT OR IGNORE is better
            stmt = insert(GraphEdge).values(
                source_id=source_id,
                target_id=target_id,
                type=edge_type,
                properties=properties,
            )
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["source_id", "target_id", "type"]
            )
            session.execute(stmt)
            session.commit()

    # -- Public Interface (matches previous KuzuGraphDB) --

    def add_repository(self, repo_id: str, repo_path: str):
        node_id = f"repo:{repo_id}"
        self._upsert_node(node_id, "Repository", repo_id, repo_id, repo_path, 
                          properties={"path": repo_path})

    def add_file(self, repo_id: str, file_path: str):
        node_id = f"file:{repo_id}:{file_path}"
        self._upsert_node(node_id, "File", repo_id, file_path.split("/")[-1], file_path,
                          properties={"path": file_path})
        
        # Link to repo
        repo_node_id = f"repo:{repo_id}"
        self._add_edge(repo_node_id, node_id, "CONTAINS")

    def add_class(self, repo_id: str, file_path: str, class_name: str, start_line: int = 0, end_line: int = 0):
        node_id = f"class:{repo_id}:{file_path}:{class_name}"
        file_node_id = f"file:{repo_id}:{file_path}"
        
        self._upsert_node(node_id, "Class", repo_id, class_name, file_path, 
                          start_line=start_line, end_line=end_line)
        self._add_edge(file_node_id, node_id, "DECLARES")

    def add_function(self, repo_id: str, file_path: str, function_name: str, 
                     parent_class: str | None = None, start_line: int = 0, end_line: int = 0):
        node_id = f"func:{repo_id}:{file_path}:{function_name}"
        if parent_class:
            node_id = f"func:{repo_id}:{file_path}:{parent_class}.{function_name}"
            
        self._upsert_node(node_id, "Function", repo_id, function_name, file_path,
                          start_line=start_line, end_line=end_line,
                          properties={"parent_class": parent_class})
        
        # Link to parent
        if parent_class:
            parent_id = f"class:{repo_id}:{file_path}:{parent_class}"
            self._add_edge(parent_id, node_id, "HAS_METHOD")
        else:
            parent_id = f"file:{repo_id}:{file_path}"
            self._add_edge(parent_id, node_id, "DECLARES")

    def add_import_edge(self, repo_id: str, from_file: str, to_file: str):
        # We assume import is file-to-file for simplicity or node-to-node
        # Previous Kuzu was add_import_edge(repo_id, from_file, to_file, import_name)
        # We'll link file nodes
        from_id = f"file:{repo_id}:{from_file}"
        to_id = f"file:{repo_id}:{to_file}"
        self._add_edge(from_id, to_id, "IMPORTS")

    def add_call_edge(self, repo_id: str, caller_file: str, caller_func: str,
                      callee_file: str, callee_func: str, line: int = 0):
        # Try to resolve IDs. Caller func might be in a class, which makes ID generation tricky without context.
        # For MVP, we presume flat function names or simple generation.
        # If accurate ID fails, we might skip. But graph builder usually knows context.
        # Here we accept the raw strings and try to construct IDs.
        # NOTE: This implies caller_func is unique in file or we use a convention.
        caller_id = f"func:{repo_id}:{caller_file}:{caller_func}"
        callee_id = f"func:{repo_id}:{callee_file}:{callee_func}"
        
        self._add_edge(caller_id, callee_id, "CALLS", properties={"line": line})

    def add_inheritance_edge(self, repo_id: str, child_file: str, child_class: str,
                             parent_file: str, parent_class: str):
        child_id = f"class:{repo_id}:{child_file}:{child_class}"
        parent_id = f"class:{repo_id}:{parent_file}:{parent_class}"
        self._add_edge(child_id, parent_id, "INHERITS_FROM")


    def close(self):
        """Close graph database connection."""
        # No-op as we share the engine/session with app
        pass


class GraphBuilder:
    """Builds code graph from AST and imports using SQLite adapter."""

    def __init__(self, graph_db: SQLiteGraphAdapter | None):
        """Initialize builder. graph_db may be None."""
        self.graph = graph_db
        self.is_noop = graph_db is None

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
        self.graph.add_import_edge(repo_id, from_file, to_file)

    def build_call_edges(self, repo_id: str, caller_file: str, caller_func: str,
                         callee_file: str, callee_func: str, line: int = 0):
        if self.is_noop: return
        self.graph.add_call_edge(repo_id, caller_file, caller_func, callee_file, callee_func, line)

    def build_inheritance_edges(self, repo_id: str, child_file: str, child_class: str,
                                parent_file: str, parent_class: str):
        if self.is_noop: return
        self.graph.add_inheritance_edge(repo_id, child_file, child_class, parent_file, parent_class)
