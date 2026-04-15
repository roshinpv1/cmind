"""
Graph query service for code structure navigation.

Provides high-level query methods over the Graphify NetworkX graph.
Replaces legacy Kuzu Cypher queries with pure Python iterations.
"""

from typing import Any
import fnmatch
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

    def _node_file_path(self, data: dict) -> str | None:
        """
        Extract a relative file path from a graph node's attribute dict.

        Graphify stores the path as ``source_file`` (absolute path).
        GraphBuilder stores it as ``file_path`` (relative).
        Some legacy nodes use ``path``.

        Returns a normalized relative path, or None if none found.
        """
        raw = (
            data.get("file_path")
            or data.get("source_file")
            or data.get("path")
        )
        if not raw:
            return None
        return self._normalize_path(str(raw))

    def find_files_by_pattern(
        self, repo_id: str, pattern: str | None = None, file_type: str | None = None
    ) -> list[dict]:
        """Find files matching a pattern or file type.

        *pattern* supports:
        - ``None`` — all files (subject to *file_type*)
        - substring — case-insensitive match on full path or basename
        - glob — ``*``, ``?``, ``[]`` matched with :func:`fnmatch.fnmatch` on path and basename
        """
        G = self.graph.get_graph(repo_id)
        results = []
        pat = pattern.strip() if pattern else None
        glob_chars = frozenset("*?[]")
        is_glob = bool(pat and any(c in glob_chars for c in pat))

        def path_matches(path: str, basename: str) -> bool:
            if not pat:
                return True
            path_n = path.replace("\\", "/")
            base_n = basename.replace("\\", "/")
            if is_glob:
                if fnmatch.fnmatch(path_n, pat) or fnmatch.fnmatch(base_n, pat):
                    return True
                # Common case: pattern is ``*.py`` — match any path ending
                if pat.startswith("*") and path_n.endswith(pat[1:]):
                    return True
                return fnmatch.fnmatch(path_n, f"*/{pat}") or fnmatch.fnmatch(path_n, f"**/{pat}")
            pl = pat.lower()
            return pl in path_n.lower() or pl in base_n.lower()

        for n, data in G.nodes(data=True):
            if data.get("type") == "File":
                path = data.get("path", "")
                name = data.get("label", "") or ""
                base = name or os.path.basename(path.replace("\\", "/"))
                if pat and not path_matches(path, base):
                    continue
                if file_type and not path.endswith(file_type):
                    continue
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

        results = []
        file_id = self._find_file_node(G, repo_id, file_path)
        if file_id:
            for u, v, edata in G.edges([file_id], data=True):
                if edata.get("type") in ("DECLARES", "DECLARES_CLASS"):
                    target = v if u == file_id else u
                    data = G.nodes[target]
                    if data.get("type") in ("Class", "Struct", "Interface"):
                        results.append({
                            "name":       data.get("name") or data.get("label"),
                            "start_line": data.get("start_line", 0),
                            "end_line":   data.get("end_line", 0),
                        })

        # Fallback: scan by source_file (Graphify slug IDs)
        if not results:
            fp_lo = file_path.replace("\\", "/").lower()
            for n, data in G.nodes(data=True):
                if data.get("type") not in ("Class", "Struct", "Interface"):
                    continue
                sfp = self._node_file_path(data) or ""
                if sfp.replace("\\", "/").lower().endswith(fp_lo):
                    results.append({
                        "name":       data.get("name") or data.get("label"),
                        "start_line": data.get("start_line", 0),
                        "end_line":   data.get("end_line", 0),
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
                        "name":       data.get("name"),
                        "file_path":  self._node_file_path(data),
                        "start_line": data.get("start_line", 0),
                        "end_line":   data.get("end_line", 0),
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
            node_name = data.get("name") or data.get("label") or ""
            if not node_name:
                continue
            node_type = data.get("type")
            if want_class and node_type in ("Class", "Struct", "Interface") and name_lower in node_name.lower():
                results.append({
                    "name":       node_name,
                    "file_path":  self._node_file_path(data),
                    "start_line": data.get("start_line", 0),
                    "end_line":   data.get("end_line", 0),
                    "type":       node_type or "Class",
                })
            elif want_func and node_type in ("Function", "Method") and name_lower in node_name.lower():
                ctype = "Method" if data.get("parent_class") else "Function"
                results.append({
                    "name":       node_name,
                    "file_path":  self._node_file_path(data),
                    "start_line": data.get("start_line", 0),
                    "end_line":   data.get("end_line", 0),
                    "type":       ctype,
                })
        return results

    def _find_file_node(self, G: nx.Graph, repo_id: str, file_path: str) -> str | None:
        """
        Find the graph node ID for a file — handles both ID schemes:
        - Legacy GraphBuilder: ``file:{repo_id}:{relative_path}``
        - Graphify slugs: match against ``source_file`` / ``path`` attributes.
        """
        norm = self._normalize_path(file_path).replace("\\", "/").lower()
        legacy_id = f"file:{repo_id}:{self._normalize_path(file_path)}"
        if legacy_id in G:
            return legacy_id
        for n, data in G.nodes(data=True):
            if data.get("type") != "File":
                continue
            fp = self._node_file_path(data) or ""
            if fp.replace("\\", "/").lower().endswith(norm):
                return n
        return None

    def get_file_context(self, repo_id: str, file_path: str) -> dict[str, Any]:
        file_path = self._normalize_path(file_path)
        G = self.graph.get_graph(repo_id)

        classes: list[dict] = []
        functions: list[dict] = []
        methods: list[dict]  = []
        imports: list[str]   = []

        file_id = self._find_file_node(G, repo_id, file_path)
        if file_id:
            for u, v, edata in G.edges([file_id], data=True):
                target = v if u == file_id else u
                target_data = G.nodes[target]
                etype = edata.get("type")

                if etype in ("DECLARES", "DECLARES_CLASS") and target_data.get("type") in ("Class", "Struct", "Interface"):
                    classes.append({
                        "name":       target_data.get("name") or target_data.get("label"),
                        "start_line": target_data.get("start_line", 0),
                        "end_line":   target_data.get("end_line", 0),
                    })
                    for cu, cv, cedata in G.edges([target], data=True):
                        if cedata.get("type") in ("HAS_METHOD", "DECLARES_FUNC"):
                            mtarget = cv if cu == target else cu
                            m_data = G.nodes[mtarget]
                            methods.append({
                                "class":      target_data.get("name") or target_data.get("label"),
                                "name":       m_data.get("name") or m_data.get("label"),
                                "start_line": m_data.get("start_line", 0),
                                "end_line":   m_data.get("end_line", 0),
                            })

                elif etype in ("DECLARES_FUNC", "DECLARES") and target_data.get("type") in ("Function", "Method"):
                    functions.append({
                        "name":       target_data.get("name") or target_data.get("label"),
                        "start_line": target_data.get("start_line", 0),
                        "end_line":   target_data.get("end_line", 0),
                    })

                elif etype == "IMPORTS" and target_data.get("type") == "File":
                    imp = self._node_file_path(target_data)
                    if imp:
                        imports.append(imp)

        # Graphify also has DECLARES edges from func nodes directly on file nodes
        if file_id and not classes and not functions:
            for n, data in G.nodes(data=True):
                fp = self._node_file_path(data) or ""
                if not fp.replace("\\", "/").lower().endswith(file_path.replace("\\", "/").lower()):
                    continue
                ntype = data.get("type")
                if ntype in ("Class", "Struct", "Interface"):
                    classes.append({
                        "name":       data.get("name") or data.get("label"),
                        "start_line": data.get("start_line", 0),
                        "end_line":   data.get("end_line", 0),
                    })
                elif ntype in ("Function", "Method"):
                    functions.append({
                        "name":       data.get("name") or data.get("label"),
                        "start_line": data.get("start_line", 0),
                        "end_line":   data.get("end_line", 0),
                    })

        return {
            "file_path": file_path,
            "classes":   classes,
            "functions": functions,
            "methods":   methods,
            "imports":   imports,
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
        func_lo = func_name.lower()
        for u, v, edata in G.edges(data=True):
            if edata.get("type") == "CALLS":
                callee = G.nodes[v]
                if func_lo in (callee.get("name") or callee.get("label") or "").lower():
                    caller = G.nodes[u]
                    results.append({
                        "name":      caller.get("name") or caller.get("label"),
                        "file_path": self._node_file_path(caller),
                        "line":      edata.get("line", 0),
                    })
        return results

    def get_callees(self, repo_id: str, func_name: str) -> list[dict]:
        G = self.graph.get_graph(repo_id)
        results = []
        func_lo = func_name.lower()
        for u, v, edata in G.edges(data=True):
            if edata.get("type") == "CALLS":
                caller = G.nodes[u]
                if func_lo in (caller.get("name") or caller.get("label") or "").lower():
                    callee = G.nodes[v]
                    results.append({
                        "name":      callee.get("name") or callee.get("label"),
                        "file_path": self._node_file_path(callee),
                        "line":      edata.get("line", 0),
                    })
        return results

    def get_dependency_chain(self, repo_id: str, file_path: str) -> list[dict]:
        file_path = self._normalize_path(file_path)
        G = self.graph.get_graph(repo_id)
        results = []

        file_id = self._find_file_node(G, repo_id, file_path)
        if file_id:
            for u, v, edata in G.edges([file_id], data=True):
                target = v if u == file_id else u
                if edata.get("type") == "IMPORTS" and G.nodes[target].get("type") == "File":
                    results.append({
                        "file_path":   self._node_file_path(G.nodes[target]),
                        "module_name": edata.get("module_name", ""),
                    })
        return results

    def get_file_dependents(self, repo_id: str, file_path: str) -> list[dict]:
        file_path = self._normalize_path(file_path)
        G = self.graph.get_graph(repo_id)
        results = []

        file_id = self._find_file_node(G, repo_id, file_path)
        if not file_id:
            return results

        for u, v, edata in G.edges(data=True):
            if edata.get("type") == "IMPORTS" and v == file_id:
                results.append({
                    "file_path":   self._node_file_path(G.nodes[u]),
                    "module_name": edata.get("module_name", ""),
                })
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
                "name":      caller_data.get("name") or caller_data.get("label"),
                "file_path": self._node_file_path(caller_data),
                "depth":     depth,
                "relation":  "calls" if depth == 1 else "transitive_call",
            })

        file_path = self._node_file_path(G.nodes[target_id])
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
                pdata = G.nodes[parent_id]
                parents.append({"name": pdata.get("name"), "file_path": self._node_file_path(pdata)})

            for u, v, data in G.edges(data=True):
                if data.get("type") == "INHERITS_FROM" and v == target_id:
                    udata = G.nodes[u]
                    children.append({"name": udata.get("name"), "file_path": self._node_file_path(udata)})

            for u, v, data in G.edges(data=True):
                if data.get("type") == "IMPLEMENTS" and v == target_id:
                    udata = G.nodes[u]
                    implementations.append({"name": udata.get("name"), "file_path": self._node_file_path(udata)})
                    
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
                    "method":    data.get("method"),
                    "route":     data.get("route"),
                    "file_path": self._node_file_path(data),
                    "handler":   handler,
                })
        return results

    def trace_symbol_definition(self, repo_id: str, symbol_name: str, 
                                start_file: str | None = None) -> list[dict]:
        results = self.find_symbol_by_name(repo_id, symbol_name)
        if start_file:
            start_file = self._normalize_path(start_file)
            results.sort(key=lambda x: x["file_path"] != start_file)
        return results
    def get_reachable_files(self, repo_id: str, anchor_file: str, hops: int = 2) -> set[str]:
        """
        Get files reachable within N hops from an anchor file in the graph.
        Follows IMPORTS and CALLS edges in both directions.
        """
        G = self.graph.get_graph(repo_id)
        if not G or len(G.nodes) == 0:
            return set()

        anchor_norm = self._normalize_path(anchor_file).replace("\\", "/").lower()

        # Locate the anchor node — works with both Graphify slug IDs and
        # legacy ``file:repo:path`` IDs by matching against source_file/path.
        anchor_id: str | None = None
        legacy_id = f"file:{repo_id}:{self._normalize_path(anchor_file)}"
        if legacy_id in G:
            anchor_id = legacy_id
        else:
            for n, data in G.nodes(data=True):
                fp = self._node_file_path(data) or ""
                if fp.replace("\\", "/").lower().endswith(anchor_norm):
                    anchor_id = n
                    break

        if anchor_id is None:
            return set()

        reachable = {anchor_id}
        frontier  = {anchor_id}

        for _ in range(hops):
            next_frontier: set[str] = set()
            for node in frontier:
                for neighbor in list(G.predecessors(node)) + list(G.successors(node)):
                    if neighbor not in reachable:
                        next_frontier.add(neighbor)
            if not next_frontier:
                break
            reachable |= next_frontier
            frontier = next_frontier

        reachable_files: set[str] = set()
        for node in reachable:
            fp = self._node_file_path(G.nodes.get(node, {}))
            if fp:
                reachable_files.add(fp)
        return reachable_files

    def get_callers_for_node(self, repo_id: str, node_id: str) -> list[dict]:
        """Efficiently get callers for a specific node ID."""
        G = self.graph.get_graph(repo_id)
        results = []
        if node_id in G:
            for u, v, edata in G.in_edges(node_id, data=True):
                if edata.get("type") == "CALLS":
                    caller = G.nodes[u]
                    results.append({
                        "name":      caller.get("name"),
                        "file_path": self._node_file_path(caller),
                        "line":      edata.get("line", 0),
                    })
        return results

    def get_callees_for_node(self, repo_id: str, node_id: str) -> list[dict]:
        """Efficiently get callees for a specific node ID."""
        G = self.graph.get_graph(repo_id)
        results = []
        if node_id in G:
            for u, v, edata in G.out_edges(node_id, data=True):
                if edata.get("type") == "CALLS":
                    callee = G.nodes[v]
                    results.append({
                        "name":      callee.get("name"),
                        "file_path": self._node_file_path(callee),
                        "line":      edata.get("line", 0),
                    })
        return results

    # ── entry-point heuristics ────────────────────────────────────────────────

    _ENTRY_POINT_FUNC_NAMES = frozenset({
        "main", "run", "execute", "start", "serve", "app", "init",
        "handle", "handler", "dispatch", "entrypoint", "entry",
    })
    _ENTRY_POINT_PATH_SIGNALS = (
        "/main.go", "/main.py", "/main.ts", "/main.js",
        "/app.py", "/app.ts", "/app.js",
        "/server.py", "/server.ts", "/server.js",
        "/cmd/", "/commands/", "/entrypoint",
        "/__main__", "/manage.py", "/wsgi.py", "/asgi.py",
        "index.js", "index.ts",
    )
    _ENTRY_POINT_TYPES = frozenset({"API", "Endpoint", "Route", "Command", "Handler"})

    def _is_entry_point(self, data: dict) -> bool:
        """Heuristically decide whether a graph node is an entry point."""
        ntype  = (data.get("type") or "").lower()
        name   = (data.get("name") or data.get("label") or "").lower()
        fp     = (self._node_file_path(data) or "").replace("\\", "/").lower()

        if data.get("type") in self._ENTRY_POINT_TYPES:
            return True
        if any(sig in fp for sig in self._ENTRY_POINT_PATH_SIGNALS):
            return True
        if name in self._ENTRY_POINT_FUNC_NAMES:
            return True
        # HTTP handler patterns: Handle*, Route*, Register*, ServeHTTP
        if re.match(r"^(handle|route|register|serve|controller)", name):
            return True
        # CLI command patterns: RunE, Execute, AddCommand (cobra / click)
        if name in ("rune", "execute", "addcommand", "runcommand"):
            return True
        # Route annotations / decorators in the label
        if "controller" in fp or "handler" in fp or "router" in fp:
            return True
        return False

    def get_architecture_map(self, repo_id: str, limit: int = 15) -> dict:
        """
        Get a high-level 'GPS' of the repository architecture.
        Identifies high-degree nodes (most connected) and potential entry points.
        """
        G = self.graph.get_graph(repo_id)
        if not G or len(G.nodes) == 0:
            return {
                "top_nodes": [], "entry_points": [],
                "total_nodes": 0, "total_edges": 0,
                "message": f"Graph for {repo_id} not found or empty.",
            }

        degrees = dict(G.degree())

        top_nodes = sorted(degrees.items(), key=lambda x: x[1], reverse=True)[:limit]
        node_summaries = []
        for n_id, deg in top_nodes:
            data = G.nodes[n_id]
            node_summaries.append({
                "id":          n_id,
                "name":        data.get("name") or data.get("label") or n_id,
                "type":        data.get("type"),
                "file_path":   self._node_file_path(data),
                "connections": deg,
            })

        # Entry-point detection using broad heuristics
        entry_points: list[dict] = []
        seen_fps: set[str] = set()
        for n, data in G.nodes(data=True):
            if not self._is_entry_point(data):
                continue
            fp = self._node_file_path(data) or ""
            if fp and fp in seen_fps:
                continue
            if fp:
                seen_fps.add(fp)
            entry_points.append({
                "name":      data.get("name") or data.get("label"),
                "type":      data.get("type"),
                "route":     data.get("route", ""),
                "file_path": fp or None,
            })

        return {
            "repo_id":      repo_id,
            "top_nodes":    node_summaries,
            "entry_points": entry_points[:15],
            "total_nodes":  len(G.nodes),
            "total_edges":  len(G.edges),
        }

    def _find_nodes_by_symbol(self, G: nx.Graph, symbol: str) -> list[str]:
        """
        Find node IDs whose name, label, file_path, source_file, or graph ID
        contains *symbol* (case-insensitive).  Handles both Graphify slug IDs
        and the legacy ``file:repo:path`` ID scheme.
        """
        sym_lo = symbol.lower()
        matches: list[str] = []
        for n, data in G.nodes(data=True):
            name     = str(data.get("name")        or "").lower()
            label    = str(data.get("label")       or "").lower()
            fp       = str(self._node_file_path(data) or "").lower()
            node_str = str(n).lower()
            if (sym_lo in name or sym_lo in label
                    or sym_lo in fp or sym_lo in node_str):
                matches.append(n)
        return matches

    def trace_connectivity_path(self, repo_id: str, start_symbol: str, end_symbol: str) -> list[dict]:
        """
        Find the shortest path between two symbols/files to show data/call flows.
        """
        G = self.graph.get_graph(repo_id)
        if not G:
            return []

        start_nodes = self._find_nodes_by_symbol(G, start_symbol)
        end_nodes   = self._find_nodes_by_symbol(G, end_symbol)

        if not start_nodes or not end_nodes:
            return []

        try:
            path = nx.shortest_path(G, source=start_nodes[0], target=end_nodes[0])
            return [
                {
                    "id":        node_id,
                    "name":      G.nodes[node_id].get("name") or G.nodes[node_id].get("label"),
                    "type":      G.nodes[node_id].get("type"),
                    "file_path": self._node_file_path(G.nodes[node_id]),
                }
                for node_id in path
            ]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    # ── goal-to-entity mapping ────────────────────────────────────────────────

    def find_entities_by_terms(
        self,
        repo_id: str,
        terms: list[str],
        max_per_term: int = 6,
        entity_types: tuple[str, ...] = ("Function", "Method", "Class", "Interface", "Struct"),
    ) -> list[dict]:
        """
        Map a list of goal keywords to specific code entities (Functions, Classes).

        Returns deduplicated matches ranked by degree (most connected first),
        including their community ID so callers can scope by cluster.
        """
        G = self.graph.get_graph(repo_id)
        if not G or not terms:
            return []

        degrees = dict(G.degree())
        seen: set[str] = set()
        results: list[dict] = []

        for term in terms:
            term_lo = term.lower()
            matches: list[tuple[str, int]] = []
            for n, data in G.nodes(data=True):
                if data.get("type") not in entity_types:
                    continue
                name = (data.get("name") or data.get("label") or "").lower()
                if not name or term_lo not in name:
                    continue
                if n in seen:
                    continue
                matches.append((n, degrees.get(n, 0)))

            # Keep top N by degree
            matches.sort(key=lambda x: x[1], reverse=True)
            for n, deg in matches[:max_per_term]:
                seen.add(n)
                data = G.nodes[n]
                results.append({
                    "node_id":    n,
                    "name":       data.get("name") or data.get("label"),
                    "type":       data.get("type"),
                    "file_path":  self._node_file_path(data),
                    "start_line": data.get("start_line", 0),
                    "end_line":   data.get("end_line", 0),
                    "community":  data.get("community"),
                    "degree":     deg,
                })

        # Sort globally by degree
        results.sort(key=lambda x: x["degree"], reverse=True)
        return results

    def get_nodes_in_community(
        self,
        repo_id: str,
        community_id: int,
        entity_types: tuple[str, ...] | None = None,
        limit: int = 30,
    ) -> list[dict]:
        """
        Return all nodes belonging to a Graphify community cluster.

        Used to restrict analysis scope to the cluster that contains
        the matched entities.
        """
        G = self.graph.get_graph(repo_id)
        if not G:
            return []

        degrees = dict(G.degree())
        results: list[dict] = []

        for n, data in G.nodes(data=True):
            if data.get("community") != community_id:
                continue
            ntype = data.get("type")
            if entity_types and ntype not in entity_types:
                continue
            results.append({
                "node_id":   n,
                "name":      data.get("name") or data.get("label"),
                "type":      ntype,
                "file_path": self._node_file_path(data),
                "degree":    degrees.get(n, 0),
            })

        results.sort(key=lambda x: x["degree"], reverse=True)
        return results[:limit]

    def get_entity_signatures(
        self,
        repo_id: str,
        entity_node_ids: list[str],
    ) -> list[dict]:
        """
        Return lightweight signatures for a list of entity node IDs:
        name, type, file, line range, immediate callers/callees count,
        and parent class (for methods).

        This is the "minimal context" — enough for the LLM to understand
        the entity without fetching full source code.
        """
        G = self.graph.get_graph(repo_id)
        if not G:
            return []

        results: list[dict] = []
        for node_id in entity_node_ids:
            if node_id not in G:
                continue
            data = G.nodes[node_id]

            # Count direct callers and callees
            callers_count = sum(
                1 for u, v, ed in G.in_edges(node_id, data=True)
                if ed.get("type") == "CALLS"
            )
            callees_count = sum(
                1 for u, v, ed in G.out_edges(node_id, data=True)
                if ed.get("type") == "CALLS"
            )

            # Immediate callees (top 5 by name)
            callees = [
                G.nodes[v].get("name") or G.nodes[v].get("label")
                for u, v, ed in G.out_edges(node_id, data=True)
                if ed.get("type") == "CALLS"
            ][:5]

            results.append({
                "node_id":      node_id,
                "name":         data.get("name") or data.get("label"),
                "type":         data.get("type"),
                "file_path":    self._node_file_path(data),
                "start_line":   data.get("start_line", 0),
                "end_line":     data.get("end_line", 0),
                "community":    data.get("community"),
                "parent_class": data.get("parent_class"),
                "callers":      callers_count,
                "callees":      callees_count,
                "calls":        [c for c in callees if c],
            })

        return results

    def get_immediate_neighborhood(
        self,
        repo_id: str,
        node_ids: list[str],
        edge_types: tuple[str, ...] = ("CALLS", "IMPORTS"),
    ) -> list[dict]:
        """
        Expand a set of anchor nodes by one hop along the given edge types.

        Returns the neighbor nodes (deduplicated), including their type,
        file path, and community — so the caller can decide whether to
        stay in-cluster or follow cross-community edges.
        """
        G = self.graph.get_graph(repo_id)
        if not G:
            return []

        seen: set[str] = set(node_ids)
        neighbors: list[dict] = []

        for nid in node_ids:
            if nid not in G:
                continue
            for u, v, ed in list(G.out_edges(nid, data=True)) + list(G.in_edges(nid, data=True)):
                other = v if u == nid else u
                if other in seen:
                    continue
                if ed.get("type") not in edge_types:
                    continue
                seen.add(other)
                d = G.nodes[other]
                neighbors.append({
                    "node_id":   other,
                    "name":      d.get("name") or d.get("label"),
                    "type":      d.get("type"),
                    "file_path": self._node_file_path(d),
                    "community": d.get("community"),
                    "edge_type": ed.get("type"),
                })

        return neighbors
