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


class SearchBm25Input(BaseModel):
    """Input for BM25 lexical retrieval."""
    query: str = Field(description="Lexical query string")
    repo_id: str = Field(description="Repository identifier")
    limit: int = Field(default=20, description="Max results to return")
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
    pattern: Optional[str] = Field(
        default=None,
        description="Substring or glob (e.g. '*auth*', '*.py') against graph file paths",
    )
    file_type: Optional[str] = Field(default=None, description="File extension to filter, e.g. '.py'")


class ListRepoDirectoryInput(BaseModel):
    """List directory contents from the repository checkout on disk."""

    repo_id: str = Field(description="Repository identifier")
    relative_path: str = Field(
        default=".",
        description="Path relative to repo root (e.g. 'src' or 'packages/api')",
    )
    recursive: bool = Field(
        default=False,
        description="If true, list files under this path up to max_depth (directories from graph not required)",
    )
    max_depth: int = Field(default=4, ge=1, le=12, description="Max directory depth when recursive=true")
    max_entries: int = Field(default=300, ge=1, le=2000, description="Cap on number of entries returned")
    include_dotfiles: bool = Field(default=False, description="Include names starting with .")


class ListFileSystemInput(BaseModel):
    """Input for listing physical files across the entire host."""
    path: str = Field(description="Absolute path to a directory on the physical file system")


class ReadFileSystemInput(BaseModel):
    """Input for reading a physical file across the entire host."""
    path: str = Field(description="Absolute path to a file on the physical file system")


class WriteFileSystemInput(BaseModel):
    """Input for writing a physical file across the entire host."""
    file_path: str = Field(description="Path (relative or absolute) to save the file, e.g. 'index.html' or 'src/main.py'")
    content: str = Field(description="Full text content to write to the file")


class GrepSearchInput(BaseModel):
    """Input for Python-based regex search over the repo checkout (no external binaries)."""

    query: str = Field(description="Literal string or regular expression to search for")
    repo_id: Optional[str] = Field(default=None, description="Repository identifier")
    includes: Optional[list[str]] = Field(
        default=None,
        description="Glob filters, e.g. ['*.py', '*.ts'] (passed to rg --glob or grep --include)",
    )
    limit: int = Field(
        default=500,
        ge=10,
        le=5000,
        description="Max match lines to return (avoids context blow-up)",
    )


class GetMapInput(BaseModel):
    """Input for Graphify architecture map (reading roadmap)."""

    repo_id: Optional[str] = Field(
        default=None,
        description="Repository identifier (omit to use latest indexed default)",
    )
    limit: int = Field(
        default=15,
        ge=5,
        le=50,
        description="How many high-degree nodes to include",
    )


class TracePathInput(BaseModel):
    """Input for shortest path between two symbols or path hints in the graph."""

    repo_id: str = Field(description="Repository identifier")
    start: str = Field(description="Start symbol name or path fragment")
    end: str = Field(description="End symbol name or path fragment")


class GraphifyQueryInput(BaseModel):
    """Input for Graphify traversal query over graph.json."""

    repo_id: str = Field(description="Repository identifier")
    question: str = Field(description="Natural-language graph question or keyword query")
    mode: str = Field(default="bfs", description="Traversal mode: 'bfs' or 'dfs'")
    depth: int = Field(default=2, ge=1, le=6, description="Traversal depth")
    budget: int = Field(default=5000, ge=200, le=20000, description="Approx output token budget")


class GraphifyPathInput(BaseModel):
    """Input for shortest path query in Graphify graph."""

    repo_id: str = Field(description="Repository identifier")
    source: str = Field(description="Source concept label or keyword")
    target: str = Field(description="Target concept label or keyword")
    max_hops: int = Field(default=8, ge=1, le=20, description="Maximum allowed hops")


class GraphifyExplainInput(BaseModel):
    """Input for explaining one graph node."""

    repo_id: str = Field(description="Repository identifier")
    term: str = Field(description="Concept label or node ID to explain")
    include_neighbors: bool = Field(default=True, description="Whether to include direct neighbors")
    max_neighbors: int = Field(default=25, ge=1, le=200, description="Cap neighbor count")


class GraphifyAddInput(BaseModel):
    """Input for adding URL content into graphify corpus."""

    url: str = Field(description="URL to ingest (paper/tweet/video/web)")
    target_dir: str = Field(default="./raw", description="Target corpus directory for ingested files")
    author: Optional[str] = Field(default=None, description="Original content author tag")
    contributor: Optional[str] = Field(default=None, description="Person adding the item")
    update_graph: bool = Field(
        default=False,
        description="Deprecated no-op (graph generation runs during indexing only)",
    )
    deep_mode: bool = Field(
        default=False,
        description="Deprecated no-op retained for backward compatibility",
    )
    graph_root: str = Field(
        default=".",
        description="Deprecated no-op retained for backward compatibility",
    )


class GraphifyRunInput(BaseModel):
    """Input for unified Graphify pipeline run."""

    repo_id: str = Field(description="Repository identifier")
    no_viz: bool = Field(default=False, description="Skip HTML visualization generation")
    obsidian: bool = Field(default=False, description="Generate Obsidian vault export")
    obsidian_dir: Optional[str] = Field(default=None, description="Output directory for Obsidian vault")


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

def create_langchain_tools(
    playbook_tools,
    enforced_repo_id: Optional[str | list[str]] = None,
    *,
    exclude_tool_names: frozenset[str] | None = None,
) -> list:
    """Create LangChain tool instances that delegate to PlaybookTools methods.
    
    Args:
        playbook_tools: Initialized PlaybookTools instance with storage refs
        
    Returns:
        List of LangChain tool functions for use with bind_tools/ToolNode
    """

    @tool(args_schema=GetMapInput)
    async def get_map(
        repo_id: Optional[str] = None,
        limit: int = 15,
    ) -> str:
        """Graphify Phase A — architecture map (high-degree nodes, entry points). Call first to choose what to read."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
        params: dict = {"limit": limit}
        if repo_id:
            params["repo_id"] = repo_id
        result = await playbook_tools.get_map(params)
        return json.dumps(result, default=str)

    @tool(args_schema=TracePathInput)
    async def trace_path(repo_id: str, start: str, end: str) -> str:
        """Graphify Phase B — shortest path between two symbols/files; use to order read_file calls along real edges."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
        result = await playbook_tools.trace_path(
            {"repo_id": repo_id, "start": start, "end": end}
        )
        return json.dumps(result, default=str)

    @tool(args_schema=GraphifyQueryInput)
    async def graphify_query(
        repo_id: str,
        question: str,
        mode: str = "bfs",
        depth: int = 2,
        budget: int = 5000,
    ) -> str:
        """Run Graphify query traversal with BFS/DFS and token budget."""
        result = await playbook_tools.graphify_query(
            {
                "repo_id": repo_id,
                "question": question,
                "mode": mode,
                "depth": depth,
                "budget": budget,
            }
        )
        return json.dumps(result, default=str)

    @tool(args_schema=GraphifyPathInput)
    async def graphify_path(
        repo_id: str,
        source: str,
        target: str,
        max_hops: int = 8,
    ) -> str:
        """Find shortest relationship path between two concepts in Graphify graph."""
        result = await playbook_tools.graphify_path(
            {
                "repo_id": repo_id,
                "source": source,
                "target": target,
                "max_hops": max_hops,
            }
        )
        return json.dumps(result, default=str)

    @tool(args_schema=GraphifyExplainInput)
    async def graphify_explain(
        repo_id: str,
        term: str,
        include_neighbors: bool = True,
        max_neighbors: int = 25,
    ) -> str:
        """Explain a concept from the Graphify graph with node metadata and neighbors."""
        result = await playbook_tools.graphify_explain(
            {
                "repo_id": repo_id,
                "term": term,
                "include_neighbors": include_neighbors,
                "max_neighbors": max_neighbors,
            }
        )
        return json.dumps(result, default=str)

    @tool(args_schema=GraphifyAddInput)
    async def graphify_add(
        url: str,
        target_dir: str = "./raw",
        author: Optional[str] = None,
        contributor: Optional[str] = None,
        update_graph: bool = False,
        deep_mode: bool = False,
        graph_root: str = ".",
    ) -> str:
        """Ingest URL content into graph corpus; optionally regenerate Graphify outputs."""
        result = await playbook_tools.graphify_add(
            {
                "url": url,
                "target_dir": target_dir,
                "author": author,
                "contributor": contributor,
                "update_graph": update_graph,
                "deep_mode": deep_mode,
                "graph_root": graph_root,
            }
        )
        return json.dumps(result, default=str)

    @tool(args_schema=GraphifyRunInput)
    async def graphify_run(
        repo_id: str,
        no_viz: bool = False,
        obsidian: bool = False,
        obsidian_dir: Optional[str] = None,
    ) -> str:
        """Regenerate graph-derived artifacts for an indexed repo_id."""
        result = await playbook_tools.graphify_run(
            {
                "repo_id": repo_id,
                "no_viz": no_viz,
                "obsidian": obsidian,
                "obsidian_dir": obsidian_dir,
            }
        )
        return json.dumps(result, default=str)

    @tool(args_schema=SearchCodebaseInput)
    async def search_codebase(
        queries: list[str],
        repo_id: Optional[str] = None,
        limit: int = 10,
        mode: str = "hybrid",
        file_types: Optional[list[str]] = None,
    ) -> str:
        """Search indexed codebase using semantic and structural queries.
        Best for finding code related to a concept, feature, or pattern.
        Automatically falls back to search_code (Python text search) if no semantic results are found."""
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

        # ── Fallback: if semantic search returned nothing, try search_code ──
        if result.get("success") and result.get("count", 0) == 0:
            primary_query = queries[0] if queries else ""
            print(
                f"[TOOL] search_codebase returned 0 results for '{primary_query}'. "
                f"Falling back to search_code..."
            )
            grep_result = await playbook_tools.grep_search({
                "query": primary_query,
                "repo_id": repo_id,
            })
            if grep_result.get("count", 0) > 0:
                grep_result["fallback_used"] = "search_code"
                grep_result["fallback_reason"] = (
                    f"semantic search returned 0 results for '{primary_query}'; "
                    f"search_code found {grep_result['count']} literal matches."
                )
                return json.dumps(grep_result, default=str)

        return json.dumps(result, default=str)

    @tool(args_schema=SearchBm25Input)
    async def search_bm25(
        query: str,
        repo_id: str,
        limit: int = 20,
        file_types: Optional[list[str]] = None,
    ) -> str:
        """Ranked lexical retrieval using SQLite FTS5 BM25 over indexed chunks."""
        if enforced_repo_id:
            repo_id = enforced_repo_id
        params = {"query": query, "repo_id": repo_id, "limit": limit}
        if file_types:
            params["file_types"] = file_types
        result = await playbook_tools.search_bm25(params)
        return json.dumps(result, default=str)
    
    @tool(args_schema=ReadFileInput)
    async def read_file(
        file_path: str,
        repo_id: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Read content of a file from the repository checkout on disk (manifest path), using the graph to resolve paths when needed.
        Optionally pass start_line/end_line (1-based) for a slice. Falls back to vector index only if embeddings are enabled and the file is not on disk."""
        if enforced_repo_id:
            repo_id = enforced_repo_id

        params = {"file_path": file_path, "repo_id": repo_id}
        if start_line is not None:
            params["start_line"] = start_line
        if end_line is not None:
            params["end_line"] = end_line

        result = await playbook_tools.read_file(params)

        if result.get("success") and result.get("source") == "filesystem":
            return json.dumps(result, default=str)

        # ── Fallback: if file not found in index, search semantically by path ──
        content_empty = not result.get("content", "").strip()
        has_error = "error" in result or not result.get("success", True)
        chunks_zero = result.get("chunks", 1) == 0

        if content_empty or has_error or chunks_zero:
            import os
            file_name = os.path.basename(file_path)
            print(
                f"[TOOL] read_file returned empty for '{file_path}'. "
                f"Falling back to search_codebase with filename query..."
            )
            # Use filename and directory stem as search signal
            dir_stem = os.path.basename(os.path.dirname(file_path))
            fallback_queries = [q for q in [file_name, dir_stem, file_path] if q]

            search_result = await playbook_tools.search_codebase({
                "queries": fallback_queries,
                "repo_id": repo_id,
                "limit": 15,
                "mode": "hybrid",
            })

            if search_result.get("count", 0) > 0:
                search_result["fallback_used"] = "search_codebase"
                search_result["fallback_reason"] = (
                    f"read_file found no indexed content for '{file_path}'; "
                    f"search_codebase returned {search_result['count']} semantically "
                    f"related chunks using queries {fallback_queries}."
                )
                return json.dumps(search_result, default=str)

            # Final fallback: try reading from physical filesystem if path looks absolute
            if file_path.startswith("/") or file_path.startswith("\\"):
                print(
                    f"[TOOL] search_codebase also returned 0 results. "
                    f"Attempting read_file_system on absolute path '{file_path}'..."
                )
                fs_result = await playbook_tools.read_file_system({"path": file_path})
                if fs_result.get("content"):
                    fs_result["fallback_used"] = "read_file_system"
                    fs_result["fallback_reason"] = (
                        f"Both read_file and search_codebase returned empty. "
                        f"read_file_system successfully read '{file_path}' from disk."
                    )
                    return json.dumps(fs_result, default=str)

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

    @tool(args_schema=ListRepoDirectoryInput)
    async def list_repo_directory(
        repo_id: str,
        relative_path: str = ".",
        recursive: bool = False,
        max_depth: int = 4,
        max_entries: int = 300,
        include_dotfiles: bool = False,
    ) -> str:
        """Browse the repo on disk under a relative path. Use when you need to discover files outside the graph or verify layout."""
        if enforced_repo_id:
            repo_id = enforced_repo_id

        result = await playbook_tools.list_repo_directory(
            {
                "repo_id": repo_id,
                "relative_path": relative_path,
                "recursive": recursive,
                "max_depth": max_depth,
                "max_entries": max_entries,
                "include_dotfiles": include_dotfiles,
            }
        )
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
    async def write_file_system(file_path: str, content: str) -> str:
        """Write text content to a file on disk. Use this to save any generated file.
        Provide file_path as a relative path like 'index.html' or 'src/app.py'.
        The runtime will place it in the correct workspace location automatically."""
        result = await playbook_tools.write_file_system({"path": file_path, "content": content})
        return json.dumps(result, default=str)
    
    @tool(args_schema=GrepSearchInput)
    async def search_code(
        query: str,
        repo_id: Optional[str] = None,
        includes: Optional[list[str]] = None,
        limit: int = 500,
    ) -> str:
        """Phase C — regex search over repo files in Python (no grep/rg). Use after graph tools narrow scope; for symbols prefer search_symbol / get_map."""
        if enforced_repo_id:
            repo_id = enforced_repo_id

        params: dict = {"query": query, "repo_id": repo_id, "limit": limit}
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
    
    tools_list = [
        get_map,
        trace_path,
        graphify_query,
        graphify_path,
        graphify_explain,
        graphify_add,
        graphify_run,
        search_symbol,
        get_file_outline,
        get_callers,
        get_callees,
        get_dependencies,
        list_files,
        list_repo_directory,
        search_code,
        search_bm25,
        search_codebase,
        read_file,
        list_file_system,
        read_file_system,
        write_file_system,
        search_catalogs,
        save_catalog_entry,
    ]

    # If this PlaybookTools embedder is none (or missing), semantic search is unusable
    _emb = getattr(playbook_tools, "embedder", None)
    if not _emb or getattr(_emb, "provider_type", None) == "none":
        tools_list = [t for t in tools_list if t.name != "search_codebase"]

    if exclude_tool_names:
        tools_list = [t for t in tools_list if t.name not in exclude_tool_names]

    return tools_list
