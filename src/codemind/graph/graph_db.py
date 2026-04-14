"""
Graph database adapter using Graphify (NetworkX in-memory).

Replaces the Kuzu Cypher-backed database with Graphify NetworkX instances, 
persisted as JSON on disk and cached in memory using an LRU strategy.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any
import networkx as nx
from networkx.readwrite import json_graph

from codemind.graphify.export import to_json


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
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []

    def add_node(self, node: Node):
        self.nodes[node.id] = node

    def add_edge(self, edge: Edge):
        for e in self.edges:
            if e.from_id == edge.from_id and e.to_id == edge.to_id and e.type == edge.type:
                return
        self.edges.append(edge)

    def get_node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def query_edges(self, from_id: str | None = None, to_id: str | None = None, edge_type: str | None = None) -> list[Edge]:
        results = self.edges
        if from_id:
            results = [e for e in results if e.from_id == from_id]
        if to_id:
            results = [e for e in results if e.to_id == to_id]
        if edge_type:
            results = [e for e in results if e.type == edge_type]
        return results

    def clear(self):
        self.nodes.clear()
        self.edges.clear()


class GraphifyAdapter:
    """Adapter for graph operations on Graphify in-memory graph (NetworkX)."""
    
    # Simple LRU-style class-level cache to keep graphs warm
    _cache: dict[str, nx.Graph] = {}

    def __init__(self, db_path: str | Path | None = None):
        """Initialize Graphify adapter."""
        if db_path is None:
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            self.base_path = Path(os.getenv("CODEMIND_REPOS_PATH", os.path.join(base_default, "repos")))
        else:
            self.base_path = Path(db_path)
            
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_graph_path(self, repo_id: str) -> Path:
        """Get the path to the graph.json file for a repository."""
        p = self.base_path / repo_id / "graphify-out" / "graph.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_graph(self, repo_id: str) -> nx.Graph:
        """Get the NetworkX graph for a repository (cached)."""
        if repo_id in self._cache:
            return self._cache[repo_id]
            
        graph_path = self._get_graph_path(repo_id)
        if graph_path.exists():
            try:
                with open(graph_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    try:
                        G = json_graph.node_link_graph(data, edges="links")
                    except TypeError:
                        G = json_graph.node_link_graph(data)
                    
                    G.graph["hyperedges"] = data.get("hyperedges", [])
                    self._cache[repo_id] = G
                    return G
            except (json.JSONDecodeError, ValueError) as e:
                print(f"[GRAPHIFY] ⚠️ Corrupted graph detected for {repo_id}: {e}")
                corrupt_path = f"{graph_path}.corrupt"
                try:
                    os.rename(graph_path, corrupt_path)
                    print(f"[GRAPHIFY] Renamed corrupted graph to {corrupt_path}")
                except Exception as rename_err:
                    print(f"[GRAPHIFY] Failed to rename corrupted graph: {rename_err}")
            except Exception as e:
                print(f"[GRAPHIFY] ⚠️ Failed to load graph for {repo_id}: {e}")
        
        print(f"[GRAPHIFY] Initializing fresh graph for {repo_id}")
        G = nx.DiGraph()
        self._cache[repo_id] = G
        return G

    def save_graph(self, repo_id: str, G: nx.Graph):
        """Save the NetworkX graph for a repository."""
        self._cache[repo_id] = G
        graph_path = self._get_graph_path(repo_id)
        
        communities = {}
        for n, data in G.nodes(data=True):
            cid = data.get("community")
            if cid is not None:
                if cid not in communities:
                    communities[cid] = []
                communities[cid].append(n)
                
        to_json(G, communities, str(graph_path))

    def update_node_community(self, node_type: str, node_id: str, community_id: str):
        """Update the topology cluster boundary mapping."""
        pass

    def add_semantic_link(self, repo_id: str, node_type: str, source_id: str, target_id: str,
                          provenance: str = "INFERRED", confidence: float = 0.8, reasoning: str = ""):
        """Add a semantic relationship inferred by an LLM."""
        G = self.get_graph(repo_id)
        rel = "SEMANTIC_LINK_CLASS" if node_type == "Class" else "SEMANTIC_LINK_FILE"
        G.add_edge(source_id, target_id, relation=rel, provenance=provenance, 
                   confidence_score=confidence, reasoning=reasoning)
        self.save_graph(repo_id, G)

    def delete_file_nodes(self, repo_id: str, file_paths: list[str]) -> int:
        """Delete nodes associated with specific file paths to handle delta indexing."""
        G = self.get_graph(repo_id)
        
        normalized_paths = [os.path.normpath(p) for p in file_paths]
        
        to_delete = []
        for n, data in list(G.nodes(data=True)):
            src = data.get("source_file")
            if src and os.path.normpath(src) in normalized_paths:
                to_delete.append(n)
                
        for n in to_delete:
            G.remove_node(n)
            
        if to_delete:
            self.save_graph(repo_id, G)
            print(f"[GRAPHIFY] ✅ Purged old graph nodes for {len(file_paths)} files")
        return len(to_delete)

    def close(self):
        """Clear cache if requested."""
        pass


# Backward compatibility aliases
SQLiteGraphAdapter = GraphifyAdapter



class GraphBuilder:
    """Builds code graph explicitly typing nodes (File, Class, Function) onto GraphifyAdapter's nx.Graph."""

    def __init__(self, graph_db: GraphifyAdapter | None):
        self.graph = graph_db
        self.is_noop = graph_db is None
        self.batch_mode = False
        import threading
        self._lock = threading.Lock()

    def set_batch_mode(self, enabled: bool):
        """Enable batch mode to defer disk saves until the end."""
        self.batch_mode = enabled

    def commit(self, repo_id: str):
        """Force a save of the graph to disk."""
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            self.graph.save_graph(repo_id, G)

    def delete_file_nodes(self, repo_id: str, file_paths: list[str]) -> None:
        if self.is_noop: return
        with self._lock:
            self.graph.delete_file_nodes(repo_id, file_paths)
        
    def build_repository_node(self, repo_id: str, repo_path: str) -> None:
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            G.add_node(f"repo:{repo_id}", type="Repository", name=repo_id, path=repo_path, label=repo_id)
            if not self.batch_mode:
                self.graph.save_graph(repo_id, G)

    def build_file_node(self, repo_id: str, file_path: str, relative_path: str) -> None:
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            file_id = f"file:{repo_id}:{relative_path}"
            G.add_node(file_id, type="File", repo_id=repo_id, path=relative_path, source_file=relative_path, label=file_path.split("/")[-1])
            G.add_edge(f"repo:{repo_id}", file_id, type="CONTAINS", relation="contains")
            if not self.batch_mode:
                self.graph.save_graph(repo_id, G)

    def build_class_node(self, repo_id: str, file_path: str, class_name: str, start_line: int=0, end_line: int=0):
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            class_id = f"class:{repo_id}:{file_path}:{class_name}"
            G.add_node(class_id, type="Class", repo_id=repo_id, name=class_name, file_path=file_path, source_file=file_path, start_line=start_line, end_line=end_line, label=class_name)
            G.add_edge(f"file:{repo_id}:{file_path}", class_id, type="DECLARES", relation="declares")
            if not self.batch_mode:
                self.graph.save_graph(repo_id, G)

    def build_function_node(self, repo_id: str, file_path: str, function_name: str, parent_class: str | None = None, start_line: int=0, end_line: int=0):
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            if parent_class:
                func_id = f"func:{repo_id}:{file_path}:{parent_class}.{function_name}"
                G.add_node(func_id, type="Function", repo_id=repo_id, name=function_name, file_path=file_path, source_file=file_path, parent_class=parent_class, start_line=start_line, end_line=end_line, label=f"{parent_class}.{function_name}")
                G.add_edge(f"class:{repo_id}:{file_path}:{parent_class}", func_id, type="HAS_METHOD", relation="has_method")
            else:
                func_id = f"func:{repo_id}:{file_path}:{function_name}"
                G.add_node(func_id, type="Function", repo_id=repo_id, name=function_name, file_path=file_path, source_file=file_path, parent_class="", start_line=start_line, end_line=end_line, label=function_name)
                G.add_edge(f"file:{repo_id}:{file_path}", func_id, type="DECLARES_FUNC", relation="declares_func")
            if not self.batch_mode:
                self.graph.save_graph(repo_id, G)

    def build_import_edges(self, repo_id: str, from_file: str, to_file: str, import_name: str):
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            from_id = f"file:{repo_id}:{from_file}"
            to_id = f"file:{repo_id}:{to_file}"
            if from_id in G and to_id in G:
                G.add_edge(from_id, to_id, type="IMPORTS", relation="imports", module_name=import_name)
                if not self.batch_mode:
                    self.graph.save_graph(repo_id, G)

    def build_call_edges(self, repo_id: str, caller_file: str, caller_func: str, callee_file: str, callee_func: str, line: int = 0):
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            # Use substring search in IDs to find the caller/callee since parent_class might not be provided in caller_func
            caller_nodes = [n for n in G.nodes if n.startswith(f"func:{repo_id}:{caller_file}:") and n.endswith(caller_func)]
            callee_nodes = [n for n in G.nodes if n.startswith(f"func:{repo_id}:{callee_file}:") and n.endswith(callee_func)]
            
            if caller_nodes and callee_nodes:
                G.add_edge(caller_nodes[0], callee_nodes[0], type="CALLS", relation="calls", line=line)
                if not self.batch_mode:
                    self.graph.save_graph(repo_id, G)

    def build_inheritance_edges(self, repo_id: str, child_file: str, child_class: str, parent_file: str, parent_class: str):
        if self.is_noop: return
        with self._lock:
            G = self.graph.get_graph(repo_id)
            child_id = f"class:{repo_id}:{child_file}:{child_class}"
            parent_id = f"class:{repo_id}:{parent_file}:{parent_class}"
            if child_id in G and parent_id in G:
                G.add_edge(child_id, parent_id, type="INHERITS_FROM", relation="inherits_from")
                if not self.batch_mode:
                    self.graph.save_graph(repo_id, G)

    def build_semantic_link(self, repo_id: str, node_type: str, source_id: str, target_id: str,
                            provenance: str = "INFERRED", confidence: float = 0.8, reasoning: str = ""):
        if self.is_noop: return
        self.graph.add_semantic_link(repo_id, node_type, source_id, target_id, provenance, confidence, reasoning)

    def set_community_id(self, *args, **kwargs): pass
