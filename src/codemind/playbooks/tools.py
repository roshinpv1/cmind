"""
Playbook execution tools - Universal code retrieval.

In the new prompt-based architecture, there's only ONE tool: search_codebase.
Playbooks define HOW to process the retrieved code via their system prompts.
"""

from typing import Optional, Any
import traceback


class PlaybookTools:
    """
    Universal code retrieval tool for playbooks.
    
    All playbooks use search_codebase to fetch code, then the LLM processes it
    according to the playbook's system prompt.
    
    READ-ONLY operations on:
    - LanceDB (semantic search)
    - Kùzu (graph filters)
    """
    
    def __init__(self, lance_storage, graph_service, embedder):
        """
        Initialize playbook tools.
        
        Args:
            lance_storage: LanceDBStorage instance
            graph_service: GraphQueryService instance
            embedder: Embedder for query encoding
        """
        self.lance = lance_storage
        self.graph = graph_service
        self.embedder = embedder
    
    async def search_codebase(self, params: dict) -> dict:
        """
        Universal code retrieval: semantic search + graph filters.
        
        This is the ONLY tool. All playbooks use this to fetch code.
        
        Args:
            params: {
                queries: list[str] - Search queries to try
                repo_id: str - Repository ID
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
            repo_id = params["repo_id"]
            queries = params.get("queries", [])
            limit = params.get("limit", 10)
            mode = params.get("mode", "semantic")
            file_types = params.get("file_types", [])
            graph_filters = params.get("graph_filters", {})
            
            # Fallback: if queries is empty but there's a single "query" param
            if not queries and "query" in params:
                queries = [params["query"]]
            
            # Validate
            limit = max(1, min(100, limit))
            
            if not queries:
                return {
                    "error": "No queries provided",
                    "results": [],
                    "count": 0,
                    "queries_used": []
                }
            
            if not self.embedder:
                return {
                    "error": "No embedder available",
                    "results": [],
                    "count": 0,
                    "queries_used": []
                }
            
            # Execute all queries and combine results
            all_results = []
            dedupe_set = set()
            
            for query in queries:
                # Encode query with proper task prefix
                query_emb = self.embedder.encode_query(query)
                
                # Semantic search
                results = self.lance.search(query_emb, repo_id=repo_id, limit=limit * 2)
                
                # Apply filters
                if mode == "hybrid" and (file_types or graph_filters):
                    filtered_results = []
                    
                    for r in results:
                        file_path = r.get('file_path', '')
                        
                        # File type filter
                        if file_types:
                            if not any(file_path.endswith(ft) for ft in file_types):
                                continue
                        
                        # Graph filters (if any)
                        # For now, we apply file_type filter only
                        # In production, you'd query graph for more complex filtering
                        
                        # Dedupe by file_path + chunk_text
                        dedupe_key = f"{file_path}:{r.get('chunk_text', '')[:50]}"
                        if dedupe_key not in dedupe_set:
                            filtered_results.append(r)
                            dedupe_set.add(dedupe_key)
                    
                    results = filtered_results
                else:
                    # Still dedupe
                    for r in results:
                        file_path = r.get('file_path', '')
                        dedupe_key = f"{file_path}:{r.get('chunk_text', '')[:50]}"
                        if dedupe_key not in dedupe_set:
                            all_results.append(r)
                            dedupe_set.add(dedupe_key)
                            continue
                
                all_results.extend(results)
            
            # Sort by score and limit
            all_results.sort(key=lambda x: x.get('score', 0), reverse=True)
            final_results = all_results[:limit]
            
            return {
                "success": True,
                "results": final_results,
                "count": len(final_results),
                "queries_used": queries
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

            # Find file using graph to get full path
            repo_id = params["repo_id"]
            files = self.graph.find_files_by_pattern(repo_id, pattern=file_path)
            if not files:
                return {"error": f"File not found: {file_path}", "content": ""}

            # Read from LanceDB chunks
            query_emb = self.embedder.encode_query(f"file content {file_path}")
            results = self.lance.search(query_emb, repo_id=repo_id, limit=50)

            # Filter to this file
            file_chunks = [r for r in results if file_path in r.get("file_path", "")]
            file_chunks.sort(key=lambda r: r.get("start_line", 0))

            # Apply line range
            if start_line and end_line:
                file_chunks = [
                    r for r in file_chunks
                    if r.get("start_line", 0) <= end_line and r.get("end_line", 0) >= start_line
                ]

            content = "\n".join(r.get("chunk_text", "") for r in file_chunks)
            return {
                "success": True,
                "file_path": file_path,
                "content": content,
                "chunks": len(file_chunks),
            }
        except Exception as e:
            return {"error": str(e), "content": ""}

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
            repo_id = params["repo_id"]
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
            repo_id = params["repo_id"]
            pattern = params.get("pattern")
            file_type = params.get("file_type")

            files = self.graph.find_files_by_pattern(repo_id, pattern=pattern, file_type=file_type)
            return {"success": True, "files": files, "count": len(files)}
        except Exception as e:
            return {"error": str(e), "files": [], "count": 0}

    async def execute_tool(self, tool_name: str, params: dict) -> dict:
        """Dispatch a tool call by name.
        
        Args:
            tool_name: One of the registered tool names
            params: Tool-specific parameters
            
        Returns:
            Tool result dict
        """
        tools = {
            "search_codebase": self.search_codebase,
            "read_file": self.read_file,
            "search_symbol": self.search_symbol,
            "get_callers": self.get_callers,
            "get_callees": self.get_callees,
            "get_dependencies": self.get_dependencies,
            "list_files": self.list_files,
        }

        if tool_name not in tools:
            return {"error": f"Unknown tool: {tool_name}. Available: {list(tools.keys())}"}

        return await tools[tool_name](params)

    @staticmethod
    def get_tool_descriptions() -> list[dict]:
        """Return tool descriptions for LLM prompting."""
        return [
            {
                "name": "search_codebase",
                "description": "Semantic search across the codebase. Best for finding code related to a concept or feature.",
                "parameters": "queries (list[str]), repo_id (str), limit (int), mode ('semantic'|'hybrid'), file_types (list[str])"
            },
            {
                "name": "read_file",
                "description": "Read content of a specific file. Use when you know the file path.",
                "parameters": "file_path (str), repo_id (str), start_line (int, optional), end_line (int, optional)"
            },
            {
                "name": "search_symbol",
                "description": "Find a class or function by name. Returns file locations.",
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
                "description": "List files in the repository matching a pattern or file type.",
                "parameters": "repo_id (str), pattern (str, optional), file_type (str, optional)"
            },
        ]
