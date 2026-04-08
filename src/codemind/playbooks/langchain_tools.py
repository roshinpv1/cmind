"""
LangChain tool wrappers for PlaybookTools.

Converts PlaybookTools methods into LangChain @tool decorated functions
for use with LangGraph's ToolNode and bind_tools().

The original PlaybookTools class is kept as-is — these wrappers delegate
to its methods, adding Pydantic schemas for automatic tool description
generation and structured input validation.

Usage:
    from codemind.playbooks.langchain_tools import create_langchain_tools
    
    playbook_tools = PlaybookTools(lance, graph, embedder, db)
    tools = create_langchain_tools(playbook_tools)
    
    # Use with LangGraph
    llm_with_tools = chat_model.bind_tools(tools)
    tool_node = ToolNode(tools)
"""

import json
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field


# ─── Input Schemas ────────────────────────────────────────────────────────────

class SearchCodebaseInput(BaseModel):
    """Input for searching the indexed codebase."""
    queries: list[str] = Field(description="Search queries to try (semantic)")
    repo_id: Optional[str] = Field(default=None, description="Repository identifier (omit to search globally across latest branches)")
    limit: int = Field(default=10, description="Max results to return")
    mode: str = Field(default="hybrid", description="Search mode: 'semantic' or 'hybrid'")
    file_types: Optional[list[str]] = Field(default=None, description="File extensions to filter, e.g. ['.py', '.js']")


class ReadFileInput(BaseModel):
    """Input for reading a specific file."""
    file_path: str = Field(description="Path to the file to read")
    repo_id: str = Field(description="Repository identifier")
    start_line: Optional[int] = Field(default=None, description="Start line (1-indexed)")
    end_line: Optional[int] = Field(default=None, description="End line (1-indexed)")


class GetFileOutlineInput(BaseModel):
    """Input for getting the structural outline of a file."""
    file_path: str = Field(description="Path to the file to outline")
    repo_id: str = Field(description="Repository identifier")


class SearchSymbolInput(BaseModel):
    """Input for finding a symbol by name."""
    name: str = Field(description="Symbol name to search for")
    repo_id: Optional[str] = Field(default=None, description="Repository identifier (omit to search globally across latest branches)")
    symbol_type: Optional[str] = Field(default=None, description="'Class' or 'Function'")


class GetCallersInput(BaseModel):
    """Input for finding callers of a function."""
    function_name: str = Field(description="Function name to find callers for")
    repo_id: str = Field(description="Repository identifier")


class GetCalleesInput(BaseModel):
    """Input for finding callees of a function."""
    function_name: str = Field(description="Function name to find callees for")
    repo_id: str = Field(description="Repository identifier")


class GetDependenciesInput(BaseModel):
    """Input for getting file dependencies."""
    file_path: str = Field(description="File path to get dependencies for")
    repo_id: str = Field(description="Repository identifier")
    direction: str = Field(
        default="imports",
        description="'imports' for what this file uses, 'imported_by' for what uses this file"
    )


class ListFilesInput(BaseModel):
    """Input for listing repository files."""
    repo_id: str = Field(description="Repository identifier")
    pattern: Optional[str] = Field(default=None, description="Glob pattern to filter files")
    file_type: Optional[str] = Field(default=None, description="File extension to filter, e.g. '.py'")


class ListFileSystemInput(BaseModel):
    """Input for listing physical files across the entire host."""
    path: str = Field(description="Absolute path to a directory on the physical file system")


class ReadFileSystemInput(BaseModel):
    """Input for reading a physical file across the entire host."""
    path: str = Field(description="Absolute path to a file on the physical file system")


class WriteFileSystemInput(BaseModel):
    """Input for writing a physical file across the entire host."""
    path: str = Field(description="Absolute path to save the physical file")
    content: str = Field(description="Text content to write to the file")


class GrepSearchInput(BaseModel):
    """Input for searching literal strings across the repository via grep."""
    query: str = Field(description="Literal string or regular expression to search for")
    repo_id: Optional[str] = Field(default=None, description="Repository identifier")
    includes: Optional[list[str]] = Field(default=None, description="File extensions to include, e.g. ['*.py', '*.js']")


class SearchCatalogsInput(BaseModel):
    """Input for searching across repository catalogs."""
    query: str = Field(description="Search query for catalog entries")
    repo_id: Optional[str] = Field(default=None, description="Filter to specific repository")
    limit: int = Field(default=5, description="Max results to return")


class SaveCatalogEntryInput(BaseModel):
    """Input for saving a catalog entry."""
    repo_id: str = Field(description="Repository identifier")
    description: str = Field(description="Repository description")
    architecture: str = Field(default="", description="Architecture description")
    tech_stack: str = Field(default="", description="Technology stack")
    topics: list[str] = Field(default_factory=list, description="Relevant topics/tags")
    repo_name: str = Field(default="", description="Repository name")
    repo_url: str = Field(default="", description="Repository URL")
    branch: str = Field(default="", description="Git branch")
    summary_high_level: str = Field(default="", description="High-level summary")
    summary_detailed: str = Field(default="", description="Detailed summary")
    category: str = Field(default="Uncategorized", description="Category classification")
    quality_score: int = Field(default=0, description="Quality score 1-100")
    specification: str = Field(default="", description="API specification or key specs")
    pros: list[str] = Field(default_factory=list, description="Advantages/strengths")
    cons: list[str] = Field(default_factory=list, description="Disadvantages/weaknesses")
    # Accept nested format fields too (normalizer handles them)
    name: Optional[str] = Field(default=None, description="Alt for repo_name")
    url: Optional[str] = Field(default=None, description="Alt for repo_url")
    purpose: Optional[dict] = Field(default=None, description="Nested purpose obj")
    quality_assessment: Optional[dict] = Field(default=None, description="Nested quality obj")
    
    model_config = {"extra": "allow"}  # Allow extra fields from LLM


# ─── Tool Factory ─────────────────────────────────────────────────────────────

def create_langchain_tools(playbook_tools, enforced_repo_id: Optional[str | list[str]] = None) -> list:
    """Create LangChain tool instances that delegate to PlaybookTools methods.
    
    Args:
        playbook_tools: Initialized PlaybookTools instance with storage refs
        
    Returns:
        List of LangChain tool functions for use with bind_tools/ToolNode
    """
    
    @tool(args_schema=SearchCodebaseInput)
    async def search_codebase(
        queries: list[str],
        repo_id: Optional[str] = None,
        limit: int = 10,
        mode: str = "hybrid",
        file_types: Optional[list[str]] = None,
    ) -> str:
        """Search indexed codebase using semantic and structural queries.
        Best for finding code related to a concept, feature, or pattern."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
        
        params = {
            "queries": queries,
            "repo_id": repo_id,
            "limit": limit,
            "mode": mode,
        }
        if file_types:
            params["file_types"] = file_types
        result = await playbook_tools.search_codebase(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=ReadFileInput)
    async def read_file(
        file_path: str,
        repo_id: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Read content of a specific file. Use when you know the exact file path."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        params = {"file_path": file_path, "repo_id": repo_id}
        if start_line is not None:
            params["start_line"] = start_line
        if end_line is not None:
            params["end_line"] = end_line
        result = await playbook_tools.read_file(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=GetFileOutlineInput)
    async def get_file_outline(
        file_path: str,
        repo_id: str
    ) -> str:
        """Get structural outline (AST) of a file showing classes, methods, and functions.
        Use this BEFORE reading a large file to locate exact line numbers of functions."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        params = {"file_path": file_path, "repo_id": repo_id}
        result = await playbook_tools.get_file_outline(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=SearchSymbolInput)
    async def search_symbol(
        name: str,
        repo_id: Optional[str] = None,
        symbol_type: Optional[str] = None,
    ) -> str:
        """Find a class or function by name. Returns file locations and definitions."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        params = {"name": name, "repo_id": repo_id}
        if symbol_type:
            params["symbol_type"] = symbol_type
        result = await playbook_tools.search_symbol(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=GetCallersInput)
    async def get_callers(function_name: str, repo_id: str) -> str:
        """Find all functions that call a given function. Shows who depends on it."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        result = await playbook_tools.get_callers({
            "function_name": function_name, "repo_id": repo_id
        })
        return json.dumps(result, default=str)
    
    @tool(args_schema=GetCalleesInput)
    async def get_callees(function_name: str, repo_id: str) -> str:
        """Find all functions called by a given function. Shows what it depends on."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        result = await playbook_tools.get_callees({
            "function_name": function_name, "repo_id": repo_id
        })
        return json.dumps(result, default=str)
    
    @tool(args_schema=GetDependenciesInput)
    async def get_dependencies(
        file_path: str, repo_id: str, direction: str = "imports"
    ) -> str:
        """Get file-level import dependencies.
        direction='imports' for what this file uses, 'imported_by' for what uses this file."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        result = await playbook_tools.get_dependencies({
            "file_path": file_path, "repo_id": repo_id, "direction": direction
        })
        return json.dumps(result, default=str)
    
    @tool(args_schema=ListFilesInput)
    async def list_files(
        repo_id: str,
        pattern: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> str:
        """List files in the repository matching a pattern or file type."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        params = {"repo_id": repo_id}
        if pattern:
            params["pattern"] = pattern
        if file_type:
            params["file_type"] = file_type
        result = await playbook_tools.list_files(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=ListFileSystemInput)
    async def list_file_system(path: str) -> str:
        """List files natively traversing the physical host file system (bypassing repo index graph tracking).
        Use this when searching outside of the repository boundary, especially for migrations."""
        result = await playbook_tools.list_file_system({"path": path})
        return json.dumps(result, default=str)

    @tool(args_schema=ReadFileSystemInput)
    async def read_file_system(path: str) -> str:
        """Read text extracted from a physical file system path (bypassing the Vector DB).
        Use this to analyze files originating from outside the sandbox."""
        result = await playbook_tools.read_file_system({"path": path})
        return json.dumps(result, default=str)
    
    @tool(args_schema=WriteFileSystemInput)
    async def write_file_system(path: str, content: str) -> str:
        """Write text to a physical file system path (bypassing the Vector DB).
        Use this to save generated migrations securely to disk. Automatically creates required folder structures."""
        result = await playbook_tools.write_file_system({"path": path, "content": content})
        return json.dumps(result, default=str)
    
    @tool(args_schema=GrepSearchInput)
    async def grep_search(
        query: str,
        repo_id: Optional[str] = None,
        includes: Optional[list[str]] = None,
    ) -> str:
        """Search across the codebase for exact literal strings or regex patterns. 
        Use this when semantic search fails or when looking for hardcoded values, error messages, or exact imports."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        params = {"query": query, "repo_id": repo_id}
        if includes:
            params["includes"] = includes
        result = await playbook_tools.grep_search(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=SearchCatalogsInput)
    async def search_catalogs(
        query: str,
        repo_id: Optional[str] = None,
        limit: int = 5,
    ) -> str:
        """Search across high-level documentation catalogs.
        Use to find relevant repositories or architectural summaries."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
            
        params = {"query": query, "limit": limit}
        if repo_id:
            params["repo_id"] = repo_id
        result = await playbook_tools.search_catalogs(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=SaveCatalogEntryInput)
    async def save_catalog_entry(**kwargs) -> str:
        """Save a comprehensive catalog entry documenting a repository.
        Includes purpose, architecture, quality assessment, and metadata.
        Handles both flat and nested LLM output formats."""
        if enforced_repo_id:
            kwargs["repo_id"] = enforced_repo_id
            
        result = await playbook_tools.save_catalog_entry(kwargs)
        return json.dumps(result, default=str)
    
    return [
        search_codebase,
        read_file,
        get_file_outline,
        search_symbol,
        get_callers,
        get_callees,
        get_dependencies,
        list_files,
        list_file_system,
        read_file_system,
        write_file_system,
        grep_search,
        search_catalogs,
        save_catalog_entry,
    ]
