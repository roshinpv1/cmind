"""
E2E API tests using FastAPI TestClient.

Tests the full HTTP request lifecycle without requiring a running server,
LLM backend, or GPU. All dependencies are mocked via conftest.py fixtures.

Run: pytest tests/test_api/test_api_e2e.py -v
"""

import pytest


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_200(self, app_client):
        """Health endpoint returns 200 with expected fields."""
        response = app_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "embedding_dim" in data

    def test_health_contains_version(self, app_client):
        """Health endpoint includes version string."""
        response = app_client.get("/api/v1/health")
        data = response.json()
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0


class TestIndexEndpoint:
    """Test the repository indexing endpoint."""

    def test_index_local_repo(self, app_client):
        """POST /index with repo_path creates a job."""
        response = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test_repo"
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["repo_id"] is not None

    def test_index_validation_no_path_or_url(self, app_client):
        """POST /index with neither repo_path nor repo_url returns 422."""
        response = app_client.post("/api/v1/index", json={})
        assert response.status_code == 422

    def test_index_validation_both_path_and_url(self, app_client):
        """POST /index with both repo_path and repo_url returns 422."""
        response = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test",
            "repo_url": "https://github.com/test/test"
        })
        assert response.status_code == 422


class TestJobEndpoint:
    """Test the job status endpoint."""

    def test_get_job_status(self, app_client):
        """GET /jobs/{id} returns status for existing job."""
        # Create a job first
        create_resp = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test_repo"
        })
        job_id = create_resp.json()["job_id"]

        # Check status
        response = app_client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "pending"

    def test_get_job_not_found(self, app_client):
        """GET /jobs/{id} returns 404 for nonexistent job."""
        response = app_client.get("/api/v1/jobs/nonexistent-id-12345")
        assert response.status_code == 404


class TestSearchEndpoint:
    """Test the search endpoint."""

    def test_search_returns_results_structure(self, app_client):
        """POST /search returns valid structure even with no indexed data."""
        response = app_client.post("/api/v1/search", json={
            "query": "authentication middleware",
            "limit": 5
        })
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "count" in data
        assert isinstance(data["results"], list)

    def test_search_empty_for_no_data(self, app_client):
        """POST /search returns empty results when nothing is indexed."""
        response = app_client.post("/api/v1/search", json={
            "query": "xyzzy does not exist anywhere",
            "limit": 5
        })
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0


class TestReposEndpoint:
    """Test the repository listing endpoint."""

    def test_list_repos_empty(self, app_client):
        """GET /repos returns empty list when nothing is indexed."""
        response = app_client.get("/api/v1/repos")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestPlaybookEndpoint:
    """Test the playbook execution endpoint."""

    def test_execute_playbook(self, app_client):
        """POST /agents/playbook executes and returns result."""
        response = app_client.post("/api/v1/agents/playbook", json={
            "playbook_name": "code_analyzer",
            "prompt": "Explain the authentication flow",
            "repo_id": "test123"
        })
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "result" in data

    def test_execute_playbook_auto_select(self, app_client):
        """POST /agents/playbook with 'auto' selects a playbook."""
        response = app_client.post("/api/v1/agents/playbook", json={
            "playbook_name": "auto",
            "prompt": "Search for all API endpoints in the codebase",
            "repo_id": "test123"
        })
        assert response.status_code == 200
        data = response.json()
        assert "success" in data

    def test_execute_playbook_no_prompt_auto(self, app_client):
        """POST /agents/playbook with 'auto' but no prompt returns 400."""
        response = app_client.post("/api/v1/agents/playbook", json={
            "playbook_name": "auto"
        })
        assert response.status_code == 400


class TestAutonomousEndpoint:
    """Test the autonomous agent endpoint."""

    def test_create_autonomous_job(self, app_client):
        """POST /agents/autonomous creates a job."""
        response = app_client.post("/api/v1/agents/autonomous", json={
            "goal": "Find all database models and their relationships",
            "repo_id": "test123",
            "max_iterations": 3
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["goal"] == "Find all database models and their relationships"

    def test_autonomous_status_not_found(self, app_client):
        """GET /agents/autonomous/{id}/status returns 404."""
        response = app_client.get("/api/v1/agents/autonomous/nonexistent/status")
        assert response.status_code == 404
