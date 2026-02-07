"""Tests for API endpoints - focuses on repo_id functionality."""

import tempfile
from pathlib import Path

import git
import pytest
from fastapi.testclient import TestClient

from codemind.api.server import app



@pytest.fixture
def temp_git_repo():
    """Create temporary Git repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        repo = git.Repo.init(repo_path)

        test_file = repo_path / "test.py"
        test_file.write_text("print('hello')")
        repo.index.add(["test.py"])
        repo.index.commit("Initial commit")

        yield str(repo_path)


def test_health_check():
    """Test health check."""
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


def test_index_returns_repo_id(temp_git_repo):
    """Test indexing returns repo_id."""
    with TestClient(app) as client:
        response = client.post("/api/v1/index", json={"repo_path": temp_git_repo})
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "repo_id" in data
        assert "status" in data
        assert len(data["repo_id"]) > 0



def test_index_consistent_repo_id(temp_git_repo):
    """Test same repo returns same repo_id."""
    with TestClient(app) as client:
        r1 = client.post("/api/v1/index", json={"repo_path": temp_git_repo})
        r2 = client.post("/api/v1/index", json={"repo_path": temp_git_repo})
        assert r1.json()["repo_id"] == r2.json()["repo_id"]


def test_job_status_includes_repo_id(temp_git_repo):
    """Test job status includes repo_id."""
    with TestClient(app) as client:
        index_resp = client.post("/api/v1/index", json={"repo_path": temp_git_repo})
        job_id = index_resp.json()["job_id"]
        expected_repo_id = index_resp.json()["repo_id"]

        status_resp = client.get(f"/api/v1/jobs/{job_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["repo_id"] == expected_repo_id
