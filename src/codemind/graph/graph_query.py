"""
Graph query service for code structure navigation.

Provides high-level query methods over the Graphify NetworkX graph.
Replaces legacy Kuzu Cypher queries with pure Python iterations.
"""

from typing import Any
import os
import re
import networkx as nx

from .graph_db import GraphifyAdapter

# Pattern to strip dynamic system paths prefix from file paths
_BASE = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
_REPOS_PATH = os.getenv("CODEMIND_REPOS_PATH", os.path.join(_BASE, "repos"))
_REPOS_PREFIX = _REPOS_PATH.replace('\\', '/').rstrip('/') + '/'
_REPO_PATH_PREFIX = re.compile(rf'^{re.escape(_REPOS_PREFIX)}[^/]+/[^/]+/')


class GraphQueryService:
    """Service for querying code structure using Graphify Graph."""

    def __init__(self, graph_db: GraphifyAdapter):
        """Initialize graph query service."""
        self.graph = graph_db

    def _normalize_path(self, file_path: str) -> str:
        """Normalize a file path."""
        return _REPO_PATH_PREFIX.sub('', file_path)

    def find_files_by_pattern(
        self, repo_id: str, pattern: str | None = None, file_type: str | None = None
    ) -> list[dict]:
        """Find files matching a pattern or file type."""
        G = self.graph.get_graph(repo_id)
        results = []
        for n, data in G.nodes(data=True):
            if data.get("type") == "File":
                path = data.get("path", "")
                name = data.get("label", "")
                if pattern and pattern not in path: continue
                if file_type and not path.endswith(file_type): continue
                results.append({
                    "file_path": path,
                    "name": name,
                    "language": data.get("language", "")
                })
        return results

    def get_classes_in_file(self, repo_id: str, file_path: str) -> list[dict]:
        """Get all classes defined in a file."""
        file_path = self._normalize_path(file_path)
        G = self.graph.get_graph(repo_id)
        file_id = f"file:{repo_id}:{file_path}"
        
        results = []
        if file_id in G:
            for u, v, edata in G.edges([file_id], data=True):
               if edata.get("type") == "DECLARES":
                   target = v if u == file_id else u
                   data = G.nodes[target]
                   if data.get("type") == "Class":
                       results.append({
                           "name": data.get("name"),
                           "start_line": data.get("start_line", 0),
                           "end_line": data.get("end_line", 0)
                       })
        return results

    def get_functions_in_class(self, repo_id: str, class_name: str) -> list[dict]:
        G = self.graph.get_graph(repo_id)
        results = []
        
        class_node = None
        for n, data in G.nodes(data=True):
            if data.get("type") == "Class" and data.get("name") == class_name:
                class_node = n
                break
                
        if not class_node:
            return results
            
        for u, v, edata in G.edges([class_node], data=True):
            if edata.get("type") == "HAS_METHOD":
                target = v if u == class_node else u
                data = G.nodes[target]
                if data.get("type") == "Function":
                    results.append({
                        "name": data.get("name"),
                        "file_path": data.get("file_path"),
                        "start_line": data.get("start_line", 0),
                        "end_line": data.get("end_line", 0)
                    })
        return results

    def find_symbol_by_name(
        self, repo_id: str, name: str, symbol_type: str | None = None
    ) -> list[dict]:
        G = self.graph.get_graph(repo_id)
        results = []
        name_lower = name.lower()
        
        want_class = symbol_type is None or symbol_type.lower() in ("class", "interface", "struct")
        want_func = symbol_type is None or symbol_type.lower() in ("function", "method")

        for n, data in G.nodes(data=True):
            if not data.get("name"): continue
            
            node_type = data.get("type")
            if want_class and node_type == "Class" and name_lower in data.get("name", "").lower():
                results.append({
                    "name": data.get("name"), "file_path": data.get("file_path"),
                    "start_line": data.get("start_line", 0), "end_line": data.get("end_line", 0),
                    "type": "Class"
                })
            elif want_func and node_type == "Function" and name_lower in data.get("name", "").lower():
                ctype = "Method" if data.get("parent_class") else "Function"
                results.append({
                    "name": data.get("name"), "file_path": data.get("file_path"),
                    "start_line": data.get("start_line", 0), "end_line": data.get("end_line", 0),
                    "type": ctype
                })
        return results

    def get_file_context(self, repo_id: str, file_path: str) -> dict[str, Any]:
        file_path = self._normalize_path(file_path)
        G = self.graph.get_graph(repo_id)
        file_id = f"file:{repo_id}:{file_path}"
        
        classes = []
        functions = []
        methods = []
        imports = []
        
        if file_id in G:
            for u, v, edata in G.edges([file_id], data=True):
                target = v if u == file_id else u
                target_data = G.nodes[target]
                etype = edata.get("type")
                
                if etype == "DECLARES" and target_data.get("type") == "Class":
                    classes.append({"name": target_data.get("name"), "start_line": target_data.get("start_line", 0), "end_line": target_data.get("end_line", 0)})
                    for cu, cv, cedata in G.edges([target], data=True):
                        if cedata.get("type") == "HAS_METHOD":
                            mtarget = cv if cu == target else cu
                            m_data = G.nodes[mtarget]
                            methods.append({"class": target_data.get("name"), "name": m_data.get("name"), 
                                          "start_line": m_data.get("start_line", 0), "end_line": m_data.get("end_line", 0)})
                                          
                elif etype == "DECLARES_FUNC" and target_data.get("type") == "Function":
                    functions.append({"name": target_data.get("name"), "start_line": target_data.get("start_line", 0), "end_line": target_data.get("end_line", 0)})
                
                elif etype == "IMPORTS":
                     if target_data.get("type") == "File":
                         imports.append(target_data.get("path"))

        return {
            "file_path": file_path,
            "classes": classes,
            "functions": functions,
            "methods": methods,
            "imports": imports,
        }

    def filter_by_structure(self, repo_id: str, filters: dict[str, Any]) -> list[str]:
        file_paths = set()
        file_type = filters.get("file_type")
        if file_type:
            results = self.find_files_by_pattern(repo_id, file_type=file_type)
            file_paths.update(r["file_path"] for r in results)

        class_name = filters.get("class_name")
        if class_name:
            cresults = self.find_symbol_by_name(repo_id, class_name, "class")
            file_paths.update(r["file_path"] for r in cresults)

        func_name = filters.get("function_name")
        if func_name:
            fresults = self.find_symbol_by_name(repo_id, func_name, "function")
            file_paths.update(r["file_path"] for r in fresults)

        pattern = filters.get("pattern")
        if pattern:
            results = self.find_files_by_pattern(repo_id, pattern=pattern)
            file_paths.update(r["file_path"] for r in results)

        return list(file_paths)

    def get_callers(self, repo_id: str, func_name: str) -> list[dict]:
        G = self.graph.get_graph(repo_id)
        results = []
        for u, v, edata in G.edges(data=True):
            if edata.get("type") == "CALLS":
                caller = G.nodes[u]
                callee = G.nodes[v]
                if callee.get("name") == func_name:
                    results.append({"name": caller.get("name"), "file_path": caller.get("file_path"), "line": edata.get("line", 0)})
        return results

    def get_callees(self, repo_id: str, func_name: str) -> list[dict]:
        G = self.graph.get_graph(repo_id)
        results = []
        for u, v, edata in G.edges(data=True):
            if edata.get("type") == "CALLS":
                caller = G.nodes[u]
                callee = G.nodes[v]
                if caller.get("name") == func_name:
                    results.append({"name": callee.get("name"), "file_path": callee.get("file_path"), "line": edata.get("line", 0)})
        return results

    def get_dependency_chain(self, repo_id: str, file_path: str) -> list[dict]:
        file_path = self._normalize_path(file_path)
        G = self.graph.get_graph(repo_id)
        file_id = f"file:{repo_id}:{file_path}"
        results = []
        
        if file_id in G:
            for u, v, edata in G.edges([file_id], data=True):
                target = v if u == file_id else u
                if edata.get("type") == "IMPORTS":
                     if G.nodes[target].get("type") == "File":
                         results.append({"file_path": G.nodes[target].get("path"), "module_name": edata.get("module_name", "")})
        return results

    def get_file_dependents(self, repo_id: str, file_path: str) -> list[dict]:
        file_path = self._normalize_path(file_path)
        G = self.graph.get_graph(repo_id)
        file_id = f"file:{repo_id}:{file_path}"
        results = []
        
        for u, v, edata in G.edges(data=True):
            if edata.get("type") == "IMPORTS":
                if v == file_id:
                    results.append({"file_path": G.nodes[u].get("path"), "module_name": edata.get("module_name", "")})
                elif u == file_id and not G.is_directed():
                    pass 
        return results

    def get_dependents(self, repo_id: str, file_path: str) -> list[dict]:
        return self.get_file_dependents(repo_id, file_path)

    def get_impact_radius(self, repo_id: str, symbol_name: str) -> list[dict]:
        G = self.graph.get_graph(repo_id)
        results = []
        
        target_nodes = [n for n, data in G.nodes(data=True) if data.get("name") == symbol_name and data.get("type") == "Function"]
        if not target_nodes:
            return results
            
        target_id = target_nodes[0]
        
        def get_callers_at_depth(start_id, max_depth):
            visited = set([start_id])
            queue = [(start_id, 0)]
            found = []
            
            while queue:
                current, depth = queue.pop(0)
                if depth > 0:
                    found.append((current, depth))
                if depth >= max_depth:
                    continue
                for u, v, data in G.edges(data=True):
                     if data.get("type") == "CALLS" and v == current and u not in visited:
                         visited.add(u)
                         queue.append((u, depth + 1))
            return found
            
        callers = get_callers_at_depth(target_id, 3)
        for caller_id, depth in callers:
            caller_data = G.nodes[caller_id]
            results.append({
                "name": caller_data.get("name"),
                "file_path": caller_data.get("file_path"),
                "depth": depth,
                "relation": "calls" if depth == 1 else "transitive_call"
            })
            
        file_path = G.nodes[target_id].get("file_path")
        if file_path:
             deps = self.get_file_dependents(repo_id, file_path)
             for dep in deps:
                 if not any(x.get("file_path") == dep["file_path"] for x in results):
                     results.append({
                         "name": dep["file_path"].split("/")[-1],
                         "file_path": dep["file_path"],
                         "depth": 1,
                         "relation": "imports"
                     })
                     
        return results

    def get_class_hierarchy(self, repo_id: str, class_name: str) -> dict:
        G = self.graph.get_graph(repo_id)
        target_nodes = [n for n, data in G.nodes(data=True) if data.get("name") == class_name and data.get("type") == "Class"]
        
        parents = []
        children = []
        implementations = []
        
        if target_nodes:
            target_id = target_nodes[0]
            
            def get_parents(start_id, max_depth):
                visited = set([start_id])
                queue = [(start_id, 0)]
                found = []
                while queue:
                    current, depth = queue.pop(0)
                    if depth > 0:
                        found.append(current)
                    if depth >= max_depth:
                        continue
                    for u, v, data in G.edges(data=True):
                        if data.get("type") == "INHERITS_FROM" and u == current and v not in visited:
                            visited.add(v)
                            queue.append((v, depth + 1))
                return found
                
            for parent_id in get_parents(target_id, 5):
                parents.append({"name": G.nodes[parent_id].get("name"), "file_path": G.nodes[parent_id].get("file_path")})
                
            for u, v, data in G.edges(data=True):
                if data.get("type") == "INHERITS_FROM" and v == target_id:
                    children.append({"name": G.nodes[u].get("name"), "file_path": G.nodes[u].get("file_path")})
                    
            for u, v, data in G.edges(data=True):
                if data.get("type") == "IMPLEMENTS" and v == target_id:
                    implementations.append({"name": G.nodes[u].get("name"), "file_path": G.nodes[u].get("file_path")})
                    
        return {
            "class_name": class_name,
            "parents": parents,
            "children": children,
            "implementations": implementations
        }

    def get_api_endpoints(self, repo_id: str) -> list[dict]:
        G = self.graph.get_graph(repo_id)
        results = []
        for n, data in G.nodes(data=True):
            if data.get("type") == "API":
                handler = ""
                for u, v, edata in G.edges([n], data=True):
                    if edata.get("type") == "HANDLED_BY":
                        target = v if u == n else u
                        handler = G.nodes[target].get("name", "")
                
                results.append({
                    "method": data.get("method"),
                    "route": data.get("route"),
                    "file_path": data.get("file_path"),
                    "handler": handler
                })
        return results

    def trace_symbol_definition(self, repo_id: str, symbol_name: str, 
                                start_file: str | None = None) -> list[dict]:
        results = self.find_symbol_by_name(repo_id, symbol_name)
        if start_file:
            start_file = self._normalize_path(start_file)
            results.sort(key=lambda x: x["file_path"] != start_file)
        return results
