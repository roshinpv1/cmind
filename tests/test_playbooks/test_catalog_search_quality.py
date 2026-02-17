import pytest
import json
from unittest.mock import MagicMock, patch
from codemind.playbooks.tools import PlaybookTools

@pytest.mark.asyncio
async def test_catalog_search_score_maximization():
    # Setup mocks
    mock_lance = MagicMock()
    mock_graph = MagicMock()
    mock_embedder = MagicMock()
    mock_db = MagicMock()
    
    tools = PlaybookTools(mock_lance, mock_graph, mock_embedder, mock_db)
    
    # Mock search_catalogs to return different scores for the same repo across queries
    # Query 1: Repo A (0.5), Repo B (0.6)
    # Query 2: Repo A (0.9), Repo C (0.4)
    def side_effect(emb, repo_id=None, limit=10, columns=None):
        # We use the embedding (encoded query) to distinguish queries in the mock
        if emb == "emb_1":
            return [
                {"repo_id": "repo_a", "_distance": 0.5, "chunk_text": "A1"},
                {"repo_id": "repo_b", "_distance": 0.4, "chunk_text": "B1"}
            ]
        else:
            return [
                {"repo_id": "repo_a", "_distance": 0.1, "chunk_text": "A2"},
                {"repo_id": "repo_c", "_distance": 0.6, "chunk_text": "C1"}
            ]
            
    mock_lance.search_catalogs.side_effect = side_effect
    mock_embedder.encode_query.side_effect = lambda q: "emb_1" if q == "query1" else "emb_2"
    
    # Mock SQLite session
    mock_session = MagicMock()
    mock_db.get_session.return_value.__enter__.return_value = mock_session
    
    # Dummy catalog entries
    mock_session.query.return_value.filter_by.return_value.first.side_effect = lambda: MagicMock(
        content=json.dumps({"description": "Full Desc"}),
        metadata_json={"tech_stack": "Python"}
    )

    params = {
        "queries": ["query1", "query2"],
        "repo_id": None,
        "mode": "catalog",
        "limit": 2
    }
    
    result = await tools.search_codebase(params)
    
    assert result["success"] is True
    assert len(result["results"]) == 2
    
    # Repo A should be top (0.9 vs 0.6) and have the best score
    assert result["results"][0]["repo_id"] == "repo_a"
    assert result["results"][0]["score"] == 0.9 # 1 - 0.1
    
    # Repo B should be second (0.6 vs 0.4 for C)
    assert result["results"][1]["repo_id"] == "repo_b"
    assert result["results"][1]["score"] == 0.6 # 1 - 0.4
