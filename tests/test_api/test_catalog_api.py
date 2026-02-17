import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, AsyncMock
import codemind.api.server as server

client = TestClient(server.app)

@pytest.fixture(autouse=True)
def setup_mocks():
    # Mock the manifest in app state
    server.app.state.manifest = MagicMock()
    
    # Create the mock tools
    mock_tools = MagicMock()
    mock_tools.search_catalogs = AsyncMock()
    
    # Create the mock executor
    mock_executor = MagicMock()
    mock_executor.tools = mock_tools
    
    # Patch playbook_executor in BOTH modules where it might be used/imported
    with patch("codemind.api.autonomous_agents.playbook_executor", mock_executor), \
         patch("codemind.api.server.playbook_executor", mock_executor, create=True):
        yield mock_executor

def test_catalog_search_post(setup_mocks):
    setup_mocks.tools.search_catalogs.return_value = {
        "success": True,
        "results": [{"repo_id": "post_repo", "score": 0.9}]
    }
    
    response = client.post("/api/v1/catalogs/search", json={"query": "test"})
    assert response.status_code == 200
    assert response.json() == [{"repo_id": "post_repo", "score": 0.9}]

def test_catalog_search_get(setup_mocks):
    setup_mocks.tools.search_catalogs.return_value = {
        "success": True,
        "results": [{"repo_id": "get_repo", "score": 0.8}]
    }
    
    response = client.get("/api/v1/catalogs/search?query=find_me")
    assert response.status_code == 200
    assert response.json() == [{"repo_id": "get_repo", "score": 0.8}]
    
    # Verify the call params
    setup_mocks.tools.search_catalogs.assert_called_once()
    args = setup_mocks.tools.search_catalogs.call_args[0][0]
    assert args["query"] == "find_me"

def test_catalog_search_error(setup_mocks):
    setup_mocks.tools.search_catalogs.return_value = {
        "success": False,
        "error": "Simulated error"
    }
    
    response = client.get("/api/v1/catalogs/search?query=fail")
    assert response.status_code == 500
    assert "Simulated error" in response.json()["detail"]
