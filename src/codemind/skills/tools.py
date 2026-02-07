"""
Skill execution tools - Universal code retrieval.

In the new prompt-based architecture, there's only ONE tool: search_codebase.
Skills define HOW to process the retrieved code via their system prompts.
"""

from typing import Optional, Any
import traceback


class SkillTools:
    """
    Universal code retrieval tool for skills.
    
    All skills use search_codebase to fetch code, then the LLM processes it
    according to the skill's system prompt.
    
    READ-ONLY operations on:
    - LanceDB (semantic search)
    - Kùzu (graph filters)
    """
    
    def __init__(self, lance_storage, graph_service, embedder):
        """
        Initialize skill tools.
        
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
        
        This is the ONLY tool. All skills use this to fetch code.
        
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
                # Encode query
                encoded = self.embedder.model.encode([query])[0]
                query_emb = encoded.tolist() if hasattr(encoded, 'tolist') else encoded
                
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
