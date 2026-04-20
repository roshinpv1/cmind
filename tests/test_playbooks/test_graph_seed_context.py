import pytest

from codemind.playbooks.executors import PlaybookExecutor


class _DummyTools:
    async def graphify_query(self, params: dict) -> dict:
        assert params["repo_id"] == "r1"
        return {
            "success": True,
            "start_nodes": ["ServiceA", "Repo"],
            "node_count": 12,
            "edge_count": 17,
            "context": "NODE ServiceA [src=svc.py]\nEDGE ServiceA --calls--> Repo",
        }

    async def graphify_god_nodes(self, params: dict) -> dict:
        assert params["repo_id"] == "r1"
        return {
            "success": True,
            "nodes": [
                {"label": "ServiceA", "edges": 99},
                {"label": "Repo", "edges": 80},
            ],
        }


@pytest.mark.asyncio
async def test_build_graph_seed_context_includes_query_and_hubs():
    executor = PlaybookExecutor(registry=None, tools=_DummyTools(), llm_client=None)
    ctx = await executor._build_graph_seed_context("r1", "how auth works")
    assert "GRAPH CONTEXT (STRUCTURAL)" in ctx
    assert "GRAPH HUBS (GOD NODES)" in ctx
    assert "ServiceA" in ctx


@pytest.mark.asyncio
async def test_build_graph_seed_context_without_repo_returns_empty():
    executor = PlaybookExecutor(registry=None, tools=_DummyTools(), llm_client=None)
    ctx = await executor._build_graph_seed_context(None, "anything")
    assert ctx == ""
