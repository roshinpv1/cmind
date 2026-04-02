"""
Comprehensive End-to-End Integration tests for CodeMind API.
Tests indexing (both local folder and remote URL), search (semantic/structural/hybrid),
and autonomous playbook execution.

Run with: pytest tests/test_e2e_integration.py -v
"""

import time
import socket
import pytest
import requests
import os
from pathlib import Path

BASE_URL = "http://localhost:8000"

def _server_is_running() -> bool:
    """Check if the API server is reachable."""
    try:
        s = socket.create_connection(("localhost", 8000), timeout=1)
        s.close()
        return True
    except OSError:
        return False

pytestmark = pytest.mark.skipif(
    not _server_is_running(),
    reason="API server not running on localhost:8000",
)

@pytest.fixture(scope="module")
def setup_local_test_repo(tmp_path_factory):
    """Creates a small local directory structure simulating a codebase."""
    repo_dir = tmp_path_factory.mktemp("e2e_test_repo")
    
    # Create some mock Python files
    src_dir = repo_dir / "src"
    src_dir.mkdir()
    
    (src_dir / "main.py").write_text('''
def hello_world():
    print("hello from main")

class DatabaseAdapter:
    def connect(self):
        pass
''')
    
    (src_dir / "utils.py").write_text('''
def help_computation(x):
    return x * 2
''')
    
    (repo_dir / "README.md").write_text('''
# Test Repo
This is an isolated test environment for e2e validation in CodeMind.
''')
    
    import pygit2
    repo = pygit2.init_repository(str(repo_dir), False)
    
    # Needs at least one commit so it's a valid clone/checkout target
    index = repo.index
    index.add_all()
    index.write()
    tree = index.write_tree()
    sig = pygit2.Signature('Test User', 'test@example.com')
    repo.create_commit('HEAD', sig, sig, 'Initial commit', tree, [])
    
    return str(repo_dir)


def wait_for_job(session: requests.Session, job_id: str, timeout_sec: int = 180):
    """Polls the job endpoint until it signals completion or error."""
    start = time.time()
    while time.time() - start < timeout_sec:
        response = session.get(f"{BASE_URL}/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        status = response.json().get("status")
        
        if status == "completed":
            return response.json()
        elif status in ["failed", "error"]:
            pytest.fail(f"Job failed: {response.json().get('error')}")
            
        time.sleep(2)
    pytest.fail(f"Job timed out after {timeout_sec}s")


class TestCodeMindE2E:
    """End-to-end integration flow."""

    @pytest.fixture(autouse=True)
    def setup_class_state(self):
        # We will share state (like the repo_id) across tests on the class instance
        if not hasattr(self, "local_repo_id"):
            self.__class__.local_repo_id = None
        if not hasattr(self, "remote_repo_id"):
            self.__class__.remote_repo_id = None
        if not hasattr(self, "remote_repo_id_branch"):
            self.__class__.remote_repo_id_branch = None
        if not hasattr(self, "session"):
            import json
            s = requests.Session()
            mock_id_token = json.dumps({
                "sub": "e2e-test-user",
                "email": "e2e@example.com",
                "name": "E2E Test User",
                "role": "admin"
            })
            resp = s.post(f"{BASE_URL}/api/v1/auth/sso-login", json={"id_token": mock_id_token})
            if resp.status_code == 200:
                s.headers.update({"Authorization": f"Bearer {resp.json().get('access_token')}"})
            self.__class__.session = s

    def test_01_index_local_folder(self, setup_local_test_repo):
        """1a. Trigger indexing on a physical local folder."""
        response = self.session.post(f"{BASE_URL}/api/v1/index", json={
            "repo_path": setup_local_test_repo,
            "branch": "main"
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        
        job_result = wait_for_job(self.session, data["job_id"])
        
        # Verify repo_id is extracted
        repo_id = job_result.get("repo_id")
        assert repo_id is not None
        self.__class__.local_repo_id = repo_id

    def test_02_index_remote_repo(self):
        """1b. Trigger indexing on a remote Git URL."""
        # Using a very small public repository to keep indexing time completely negligible
        test_url = "https://github.com/octocat/Hello-World"
        response = self.session.post(f"{BASE_URL}/api/v1/index", json={
            "repo_url": test_url,
            "branch": "master"
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        
        # We don't necessarily block waiting on Github if the internet/rate limits fluctuate, 
        # but we do want to verify the system accepts and attempts processing it correctly.
        job_result = wait_for_job(self.session, data["job_id"])
        repo_id = job_result.get("repo_id")
        self.__class__.remote_repo_id = repo_id

    def test_02b_index_remote_repo_branch(self):
        """1c. Trigger indexing on a remote Git URL for a different branch."""
        test_url = "https://github.com/octocat/Hello-World"
        response = self.session.post(f"{BASE_URL}/api/v1/index", json={
            "repo_url": test_url,
            "branch": "test"
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        
        job_result = wait_for_job(self.session, data["job_id"])
        repo_id = job_result.get("repo_id")
        self.__class__.remote_repo_id_branch = repo_id
        
        assert self.remote_repo_id is not None
        assert self.remote_repo_id_branch is not None
        assert self.remote_repo_id != self.remote_repo_id_branch, "Multi-branch generated overlapping repo_ids"

    def test_03_verify_manifests(self):
        """2. Check SQLite manifesting records exist for both environments."""
        response = self.session.get(f"{BASE_URL}/api/v1/repos")
        assert response.status_code == 200
        repos = response.json()
        repo_ids = [repo.get("repo_id") or repo.get("id") for repo in repos]
        
        assert self.local_repo_id in repo_ids, "Local folder repository ID missing from manifest"
        assert self.remote_repo_id in repo_ids, "Remote Github repository ID missing from manifest"
        assert self.remote_repo_id_branch in repo_ids, "Remote branch repository ID missing from manifest"

    def test_04_semantic_search(self):
        """3a. Query LanceDB semantic search engine against local DB."""
        assert self.local_repo_id is not None, "Previous indexing step failed"
        
        response = self.session.post(f"{BASE_URL}/api/v1/search", json={
            "query": "database adapter",
            "repo_id": self.local_repo_id,
            "search_mode": "semantic",
            "limit": 5
        })
        assert response.status_code == 200
        results = response.json()
        assert len(results) > 0
        
        # It should have found our DatabaseAdapter mock
        assert any("DatabaseAdapter" in r.get("chunk_text", "") for r in results)

    def test_05_structural_search(self):
        """3b. Query Kuzu graph engine via structural filters."""
        # Use structural search mode and apply a .py filter 
        response = self.session.post(f"{BASE_URL}/api/v1/search", json={
            "query": "",
            "repo_id": self.local_repo_id,
            "search_mode": "structural",
            "filters": {"file_type": ".py"},
            "limit": 5
        })
        assert response.status_code == 200
        results = response.json()
        if len(results) > 0:
            assert all(r["file_path"].endswith(".py") for r in results)

    def test_06_autonomous_agent_playbook(self):
        """4. Trigger an autonomous LangChain playbook analysis against the repo graph."""
        # Explore CodeBase is one of the faster playbooks relying on context/graph queries
        response = self.session.post(f"{BASE_URL}/api/v1/agents/playbook", json={
            "playbook_name": "explore_codebase",
            "repo_id": self.local_repo_id,
            "prompt": "Tell me about the code in this repo"
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "result" in data
