"""
Base agent class with integrated search and graph access.
All agents inherit from this to get codebase intelligence.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
from ..factory import get_llm_client
from ..base import LLMDriver
from ...storage.lancedb_storage import LanceDBStorage
from ...graph.graph_query import GraphQueryService


class BaseCodeAgent(ABC):
    """
    Base class for all code analysis agents.
    
    Provides common functionality:
    - Access to hybrid search (LanceDB + Kùzu)
    - Graph structure queries
    - LLM generation
    - Context management
    """
    
    def __init__(
        self,
        llm: Optional[LLMDriver] = None,
        search_service: Optional[LanceDBStorage] = None,
        graph_service: Optional[GraphQueryService] = None
    ):
        """
        Initialize agent with services.
        
        Args:
            llm: LLM driver (defaults to auto-detected via factory)
            search_service: LanceDB search service
            graph_service: Kùzu graph query service
        """
        self.llm = llm or get_llm_client()
        self.search = search_service
        self.graph = graph_service
    
    def search_codebase(
        self,
        query: str,
        repo_id: str,
        filters: Optional[dict] = None,
        limit: int = 10
    ) -> list[dict]:
        """
        Search codebase using hybrid search.
        
        Args:
            query: Natural language search query
            repo_id: Repository ID
            filters: Optional filters (file_type, patterns, etc.)
            limit: Max results
            
        Returns:
            List of search results with file_path, chunk_text, etc.
        """
        if not self.search:
            return []
        
        from ...embeddings.mlx_embedder import get_embedder
        embedder = get_embedder()
        
        # Generate query embedding
        query_embedding = embedder.embed_text(query)
        
        # Perform semantic search
        results = self.search.search(
            query_embedding,
            repo_id=repo_id,
            limit=limit
        )
        
        # Apply filters if using hybrid mode
        if filters and self.graph:
            try:
                candidate_files = self.graph.filter_by_structure(repo_id, filters)
                
                # Normalize paths (graph has relative, LanceDB has full)
                if candidate_files and results:
                    normalized = set()
                    for result in results:
                        lance_path = result['file_path']
                        for candidate in candidate_files:
                            if lance_path.endswith(candidate):
                                normalized.add(lance_path)
                                break
                    
                    results = [r for r in results if r['file_path'] in normalized]
            except Exception as e:
                print(f"[AGENT] Filter error, using semantic only: {e}")
        
        return results
    
    def get_file_structure(self, repo_id: str, file_path: str) -> dict:
        """
        Get structural information about a file.
        
        Args:
            repo_id: Repository ID
            file_path: File path (relative from graph)
            
        Returns:
            Dict with classes, functions, etc.
        """
        if not self.graph:
            return {}
        
        return self.graph.get_file_context(repo_id, file_path)
    
    async def generate_text(self, prompt: str, **kwargs) -> str:
        """
        Generate text using LLM.
        
        Args:
            prompt: Input prompt
            **kwargs: Additional LLM parameters (temperature, max_tokens, etc.)
            
        Returns:
            Generated text
        """
        return await self.llm.generate(prompt, **kwargs)
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """
        Execute the agent's main task.
        Must be implemented by subclasses.
        """
        pass
