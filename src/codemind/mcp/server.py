"""
CodeMind MCP Server — exposes code intelligence via Model Context Protocol.

Proxies requests to the running CodeMind FastAPI server.

**Graphify-first (recommended for repo analysis):** use ``graphify_*`` tools to
query the pre-built AST/relationship graph — compact, structural, and
token-efficient. Then use ``code_search`` / file reads only for line-level proof.

Tools:
  Graphify (structural / relational — from graphify-out graph):
    - graphify_architecture_map — high-level GPS (hubs, entry points)
    - graphify_file_outline — classes, functions, imports for one file
    - graphify_find_files       — paths matching pattern or extension
    - graphify_symbol           — locate symbol definitions
    - graphify_callers / graphify_callees
    - graphify_file_dependencies — imports or imported-by for a file
    - graphify_trace_path — shortest path between two symbols/paths
    - graphify_impact_radius    — callers + import dependents of a function
    - graphify_reachable_files  — N-hop neighborhood from an anchor file

  Code Intelligence:
    - catalog_search  — semantic search across repository catalogs (LanceDB)
    - code_search     — semantic/hybrid search over indexed code chunks
    - catalog_browse  — full catalog entry for a repo

  Playbooks & agents:
    - playbook_run    — execute one named playbook synchronously (ReAct or linear)
    - agent_execute   — autonomous planner (multi-playbook)
    - agent_status / agent_result

Resources:
    - codemind://repos  — list all indexed repositories
    - codemind://health — server health + embedding info
"""

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_URL = os.environ.get("CODEMIND_API_URL", "http://localhost:8000")
TIMEOUT = float(os.environ.get("CODEMIND_TIMEOUT", "120"))  # generous for agent calls

mcp = FastMCP(
    "CodeMind",
    instructions=(
        "CodeMind: code intelligence with Graphify structural graphs + optional vector search.\n"
        "For repository analysis, prefer graphify_* tools first (architecture_map, file_outline, "
        "symbol, callers/callees, trace_path) — they return compact structural data from AST-backed "
        "graphs, saving tokens. Use code_search for semantic discovery; catalog_search for catalog "
        "entries only. Run playbook_run or agent_execute for full playbook workflows."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client() -> httpx.Client:
    """Create a fresh httpx client with base URL and timeout."""
    return httpx.Client(base_url=API_URL, timeout=TIMEOUT)


def _post(path: str, payload: dict) -> dict:
    """POST JSON to the API and return parsed response."""
    with _client() as client:
        resp = client.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()


def _get(path: str) -> dict | list:
    """GET from the API and return parsed response."""
    with _client() as client:
        resp = client.get(path)
        resp.raise_for_status()
        return resp.json()


def _format(data) -> str:
    """Pretty-print JSON data for MCP text output."""
    return json.dumps(data, indent=2, default=str)


def _error(msg: str) -> str:
    """Return a formatted error message."""
    return json.dumps({"error": msg})


# ═══════════════════════════════════════════════════════════════════════════
# Code Intelligence Tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def catalog_search(
    query: str,
    repo_id: str | None = None,
    limit: int = 5,
    min_score: float = 0.5,
) -> str:
    """Search across all indexed repository catalogs using semantic similarity.

    Use this to find repositories matching a topic, technology, or architecture pattern.

    Args:
        query: Natural language search query (e.g. "microservices with authentication")
        repo_id: Optional repository ID to scope the search
        limit: Maximum number of results (default 5)
        min_score: Minimum similarity score 0-1 (default 0.8)
    """
    try:
        payload: dict = {"query": query, "limit": limit, "min_score": min_score}
        if repo_id:
            payload["repo_id"] = repo_id
        data = _post("/api/v1/catalogs/search", payload)
        if not data:
            return _format({"message": "No catalogs matched your query.", "query": query})
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running. Start it with: uvicorn codemind.api.server:app")
    except httpx.HTTPStatusError as e:
        return _error(f"API error {e.response.status_code}: {e.response.text}")


@mcp.tool()
def code_search(
    query: str,
    repo_id: str,
    search_mode: str = "hybrid",
    limit: int = 10,
    file_types: list[str] | None = None,
    file_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> str:
    """Search over indexed source code using semantic and structural filters.

    Supports three modes:
    - "semantic": pure vector similarity
    - "hybrid": vector similarity + structural filters (recommended)
    - "structural": graph-only, no semantic matching

    Args:
        query: Natural language search query (e.g. "authentication middleware")
        repo_id: Repository ID to search within (required)
        search_mode: One of "semantic", "hybrid", "structural" (default "hybrid")
        limit: Maximum results (default 10)
        file_types: Filter by extensions, e.g. [".py", ".ts"]
        file_patterns: Filter by filename patterns, e.g. ["auth", "middleware"]
        exclude_patterns: Exclude files matching patterns, e.g. ["test_", "__pycache__"]
    """
    try:
        payload: dict = {
            "query": query,
            "repo_id": repo_id,
            "search_mode": search_mode,
            "limit": limit,
        }
        filters: dict = {}
        if file_types:
            filters["file_types"] = file_types
        if file_patterns:
            filters["file_patterns"] = file_patterns
        if exclude_patterns:
            filters["exclude_patterns"] = exclude_patterns
        if filters:
            payload["filters"] = filters

        data = _post("/api/v1/search", payload)
        if not data:
            return _format({"message": "No code matched your query.", "query": query})
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running. Start it with: uvicorn codemind.api.server:app")
    except httpx.HTTPStatusError as e:
        return _error(f"API error {e.response.status_code}: {e.response.text}")


@mcp.tool()
def catalog_browse(repo_id: str) -> str:
    """Get the full catalog entry for a specific repository.

    Returns the complete auto-generated summary including architecture,
    tech stack, dependencies, and key components.

    Args:
        repo_id: Repository ID to browse
    """
    try:
        data = _get(f"/api/v1/catalogs/{repo_id}")
        if not data:
            return _format({"message": f"No catalog found for repo {repo_id}"})
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running. Start it with: uvicorn codemind.api.server:app")
    except httpx.HTTPStatusError as e:
        return _error(f"API error {e.response.status_code}: {e.response.text}")


# ═══════════════════════════════════════════════════════════════════════════
# Autonomous Agent Tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def agent_execute(
    goal: str,
    repo_id: str | None = None,
    max_iterations: int = 10,
    allowed_playbooks: list[str] | None = None,
) -> str:
    """Start an autonomous agent to achieve a natural language goal.

    The agent uses a Think → Act → Observe loop to plan and execute
    multi-step code analysis tasks. Returns a job_id for tracking.

    Typical workflow: agent_execute → agent_status (poll) → agent_result

    Args:
        goal: What the agent should accomplish (e.g. "Find all API endpoints and their auth requirements")
        repo_id: Repository to analyze (optional for cross-repo tasks)
        max_iterations: Maximum reasoning iterations (default 10, max 50)
        allowed_playbooks: Restrict agent to specific playbooks (e.g. ["code_analyzer"])
    """
    try:
        payload: dict = {"goal": goal, "max_iterations": max_iterations}
        if repo_id:
            payload["repo_id"] = repo_id
        if allowed_playbooks:
            payload["allowed_playbooks"] = allowed_playbooks

        data = _post("/api/v1/agents/autonomous", payload)
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running. Start it with: uvicorn codemind.api.server:app")
    except httpx.HTTPStatusError as e:
        return _error(f"API error {e.response.status_code}: {e.response.text}")


@mcp.tool()
def agent_status(job_id: str) -> str:
    """Check the status of a running autonomous agent job.

    Returns status (pending/running/completed/failed), iteration count,
    and execution logs.

    Args:
        job_id: Job ID returned by agent_execute
    """
    try:
        data = _get(f"/api/v1/agents/autonomous/{job_id}/status")
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running.")
    except httpx.HTTPStatusError as e:
        return _error(f"API error {e.response.status_code}: {e.response.text}")


@mcp.tool()
def agent_result(job_id: str) -> str:
    """Get the final result of a completed autonomous agent job.

    Only available after the job status is 'completed' or 'failed'.
    Returns the agent's answer, playbooks used, and step count.

    Args:
        job_id: Job ID returned by agent_execute
    """
    try:
        data = _get(f"/api/v1/agents/autonomous/{job_id}/result")
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running.")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 425:
            return _format({"status": "in_progress", "message": "Job is still running. Try again shortly."})
        return _error(f"API error {e.response.status_code}: {e.response.text}")


# ═══════════════════════════════════════════════════════════════════════════
# Resources
# ═══════════════════════════════════════════════════════════════════════════


@mcp.resource("codemind://repos")
def list_repos() -> str:
    """List all indexed repositories with their metadata."""
    try:
        data = _get("/api/v1/repos")
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running.")


@mcp.resource("codemind://health")
def health() -> str:
    """CodeMind server health, embedding model info, and version."""
    try:
        data = _get("/api/v1/health")
        return _format(data)
    except httpx.ConnectError:
        return _error("CodeMind API server is not running.")
