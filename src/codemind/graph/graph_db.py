"""
Graph database integration for code relationships.

Uses Kùzu embedded graph database for persistent storage.
"""

from dataclasses import dataclass
from typing import Any

from .kuzu_graph import KuzuGraphDB


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
    """Simple in-memory graph database (legacy - use KuzuGraphDB instead)."""

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


class GraphBuilder:
    """Builds code graph from AST and imports."""

    def __init__(self, graph_db: KuzuGraphDB | GraphDB):
        """Initialize builder."""
        self.graph = graph_db
        self.is_kuzu = isinstance(graph_db, KuzuGraphDB)

    def build_repository_node(self, repo_id: str, repo_path: str) -> None:
        """Create or update repository node."""
        print(f"[KUZU] Creating repository node: {repo_id}")
        if self.is_kuzu:
            self.graph.add_repository(repo_id, repo_path)
        else:
            node = Node(
                id=f"repo:{repo_id}",
                type="Repository",
                properties={"path": repo_path, "repo_id": repo_id},
            )
            self.graph.add_node(node)
        print(f"[KUZU] ✅ Repository node created")

    def build_file_node(self, repo_id: str, file_path: str, relative_path: str) -> None:
        """Build file node and extract structure."""
        print(f"[KUZU] Building file node: {relative_path}")
        
        # Create file node
        if self.is_kuzu:
            self.graph.add_file(repo_id, file_path)
            print(f"[KUZU] Created file node for: {relative_path}")
        else:
            file_id = f"file:{repo_id}:{file_path}"
            node = Node(
                id=file_id,
                type="File",
                properties={"path": file_path, "repo_id": repo_id},
            )
            self.graph.add_node(node)

            # Link to repository
            edge = Edge(
                from_id=f"repo:{repo_id}",
                to_id=file_id,
                type="CONTAINS",
                properties={},
            )
            self.graph.add_edge(edge)
            print(f"[KUZU] Created file node: {file_id}")

        # The following AST extraction logic is not part of the original GraphBuilder and would require
        # an ast_extractor to be initialized. For now, it's commented out to maintain
        # syntactic correctness and avoid introducing undeclared dependencies.
        # If this functionality is intended, the GraphBuilder's __init__ method
        # and potentially other helper methods would need to be updated.

        # # Extract AST structure
        # try:
        #     structure = self.ast_extractor.extract(file_path)
            
        #     # Create class nodes
        #     classes_created = 0
        #     for cls in structure.get("classes", []):
        #         class_id = f"{file_id}:{cls['name']}"
        #         self.create_class_node(
        #             repo_id, class_id, cls["name"], cls.get("docstring")
        #         )
        #         self.create_relationship(file_id, "DECLARES_CLASS", class_id)
        #         classes_created += 1

        #         # Create method nodes
        #         methods_created = 0
        #         for method in cls.get("methods", []):
        #             method_id = f"{class_id}:{method['name']}"
        #             self.create_function_node(
        #                 repo_id,
        #                 method_id,
        #                 method["name"],
        #                 method.get("docstring"),
        #                 method.get("parameters", []),
        #             )
        #             self.create_relationship(class_id, "HAS_METHOD", method_id)
        #             methods_created += 1
                
        #         if methods_created > 0:
        #             print(f"[KUZU]   Created {methods_created} methods for class {cls['name']}")

        #     # Create function nodes
        #     functions_created = 0
        #     for func in structure.get("functions", []):
        #         func_id = f"{file_id}:{func['name']}"
        #         self.create_function_node(
        #             repo_id,
        #             func_id,
        #             func["name"],
        #             func.get("docstring"),
        #             func.get("parameters", []),
        #         )
        #         self.create_relationship(file_id, "DECLARES_FUNCTION", func_id)
        #         functions_created += 1

        #     if classes_created > 0 or functions_created > 0:
        #         print(f"[KUZU] ✅ Extracted structure: {classes_created} classes, {functions_created} functions")
        # except Exception as e:
        #     print(f"[KUZU] ⚠️  AST extraction failed for {relative_path}: {e}")
        #     # Continue without structure
        #     pass

    def build_class_node(self, repo_id: str, file_path: str, class_name: str):
        """Create class node and link to file."""
        if self.is_kuzu:
            self.graph.add_class(repo_id, file_path, class_name)
        else:
            file_id = f"file:{repo_id}:{file_path}"
            class_id = f"class:{repo_id}:{file_path}:{class_name}"

            node = Node(
                id=class_id,
                type="Class",
                properties={"name": class_name, "file_path": file_path},
            )
            self.graph.add_node(node)

            # Link to file
            edge = Edge(from_id=file_id, to_id=class_id, type="DECLARES", properties={})
            self.graph.add_edge(edge)

    def build_function_node(
        self,
        repo_id: str,
        file_path: str,
        function_name: str,
        parent_class: str | None = None,
    ):
        """Create function node and link to file or class."""
        if self.is_kuzu:
            self.graph.add_function(repo_id, file_path, function_name, parent_class)
        else:
            func_id = f"func:{repo_id}:{file_path}:{function_name}"

            node = Node(
                id=func_id,
                type="Function",
                properties={
                    "name": function_name,
                    "file_path": file_path,
                    "parent_class": parent_class,
                },
            )
            self.graph.add_node(node)

            # Link to parent (file or class)
            if parent_class:
                parent_id = f"class:{repo_id}:{file_path}:{parent_class}"
            else:
                parent_id = f"file:{repo_id}:{file_path}"

            edge = Edge(from_id=parent_id, to_id=func_id, type="DECLARES", properties={})
            self.graph.add_edge(edge)
