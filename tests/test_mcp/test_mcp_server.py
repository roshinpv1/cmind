"""Tests for the CodeMind MCP Server.

All tests mock httpx.Client so no running server is required.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_response(data, status_code=200):
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="error",
            request=MagicMock(),
            response=resp,
        )
    return resp


def _mock_client(response):
    """Build a mock httpx.Client context manager returning *response*."""
    client = MagicMock()
    client.post.return_value = response
    client.get.return_value = response

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, client


# ═══════════════════════════════════════════════════════════════════════════
# Code Intelligence Tools
# ═══════════════════════════════════════════════════════════════════════════


class TestCatalogSearch:
    def test_basic_search(self):
        from codemind.mcp.server import catalog_search

        fake_data = [{"repo_id": "abc", "score": 0.9, "chunk_text": "microservices"}]
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(catalog_search("microservices"))

        assert len(result) == 1
        assert result[0]["repo_id"] == "abc"
        client.post.assert_called_once()
        payload = client.post.call_args[1]["json"]
        assert payload["query"] == "microservices"
        assert payload["limit"] == 5

    def test_search_with_repo_id(self):
        from codemind.mcp.server import catalog_search

        fake_data = []
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(catalog_search("auth", repo_id="r1"))

        payload = client.post.call_args[1]["json"]
        assert payload["repo_id"] == "r1"

    def test_server_down(self):
        from codemind.mcp.server import catalog_search

        import httpx

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(catalog_search("test"))

        assert "error" in result
        assert "not running" in result["error"]


class TestCodeSearch:
    def test_basic_search(self):
        from codemind.mcp.server import code_search

        fake_data = [{"file_path": "auth.py", "chunk_text": "def login():", "score": 0.95}]
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(code_search("login function", repo_id="abc"))

        assert len(result) == 1
        assert result[0]["file_path"] == "auth.py"
        payload = client.post.call_args[1]["json"]
        assert payload["search_mode"] == "hybrid"
        assert payload["repo_id"] == "abc"

    def test_with_filters(self):
        from codemind.mcp.server import code_search

        mock_resp = _mock_response([])
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            code_search(
                "auth",
                repo_id="abc",
                file_types=[".py"],
                file_patterns=["auth"],
                exclude_patterns=["test_"],
            )

        payload = client.post.call_args[1]["json"]
        assert payload["filters"]["file_types"] == [".py"]
        assert payload["filters"]["file_patterns"] == ["auth"]
        assert payload["filters"]["exclude_patterns"] == ["test_"]


class TestCatalogBrowse:
    def test_browse(self):
        from codemind.mcp.server import catalog_browse

        fake_data = [{"summary": "A FastAPI app", "tech_stack": ["Python", "FastAPI"]}]
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(catalog_browse("abc"))

        assert result[0]["tech_stack"] == ["Python", "FastAPI"]
        client.get.assert_called_once_with("/api/v1/catalogs/abc")

    def test_not_found(self):
        from codemind.mcp.server import catalog_browse

        mock_resp = _mock_response([])
        ctx, _ = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(catalog_browse("missing"))

        assert "No catalog found" in result["message"]


# ═══════════════════════════════════════════════════════════════════════════
# Autonomous Agent Tools
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentExecute:
    def test_execute(self):
        from codemind.mcp.server import agent_execute

        fake_data = {"job_id": "j1", "status": "pending", "goal": "find bugs"}
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(agent_execute("find bugs", repo_id="abc"))

        assert result["job_id"] == "j1"
        assert result["status"] == "pending"
        payload = client.post.call_args[1]["json"]
        assert payload["goal"] == "find bugs"
        assert payload["repo_id"] == "abc"

    def test_with_allowed_playbooks(self):
        from codemind.mcp.server import agent_execute

        mock_resp = _mock_response({"job_id": "j2", "status": "pending"})
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            agent_execute("analyze", allowed_playbooks=["code_analyzer"])

        payload = client.post.call_args[1]["json"]
        assert payload["allowed_playbooks"] == ["code_analyzer"]


class TestAgentStatus:
    def test_get_status(self):
        from codemind.mcp.server import agent_status

        fake_data = {"job_id": "j1", "status": "running", "iterations": 3}
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(agent_status("j1"))

        assert result["status"] == "running"
        client.get.assert_called_once_with("/api/v1/agents/autonomous/j1/status")


class TestAgentResult:
    def test_get_result(self):
        from codemind.mcp.server import agent_result

        fake_data = {"job_id": "j1", "status": "completed", "answer": "Found 3 bugs"}
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(agent_result("j1"))

        assert result["answer"] == "Found 3 bugs"
        client.get.assert_called_once_with("/api/v1/agents/autonomous/j1/result")

    def test_still_running(self):
        from codemind.mcp.server import agent_result

        mock_resp = _mock_response({"detail": "Job is running"}, status_code=425)
        ctx, _ = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(agent_result("j1"))

        assert result["status"] == "in_progress"


# ═══════════════════════════════════════════════════════════════════════════
# Resources
# ═══════════════════════════════════════════════════════════════════════════


class TestResources:
    def test_list_repos(self):
        from codemind.mcp.server import list_repos

        fake_data = [{"repo_id": "abc", "name": "my-app", "status": "indexed"}]
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(list_repos())

        assert len(result) == 1
        assert result[0]["name"] == "my-app"
        client.get.assert_called_once_with("/api/v1/repos")

    def test_health(self):
        from codemind.mcp.server import health

        fake_data = {"status": "healthy", "version": "0.1.0", "embedding_model": "bge-base"}
        mock_resp = _mock_response(fake_data)
        ctx, client = _mock_client(mock_resp)

        with patch("codemind.mcp.server._client", return_value=ctx):
            result = json.loads(health())

        assert result["status"] == "healthy"
        client.get.assert_called_once_with("/api/v1/health")
