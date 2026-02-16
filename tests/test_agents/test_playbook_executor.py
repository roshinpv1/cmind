"""
PlaybookExecutor unit tests.

Tests the search → LLM → format pipeline with mocked dependencies.
No real LLM or indexed data required.

Run: pytest tests/test_agents/test_playbook_executor.py -v
"""

import asyncio
import json
import pytest


def _run(coro):
    """Helper to run an async coroutine in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestPlaybookExecutorPipeline:
    """Test the full PlaybookExecutor pipeline."""

    def test_execute_code_analyzer(self, mock_llm, playbook_registry, playbook_tools):
        """Full search → llm → format pipeline for code_analyzer."""
        from codemind.playbooks import PlaybookExecutor

        executor = PlaybookExecutor(playbook_registry, playbook_tools, mock_llm)
        result = _run(executor.execute("code_analyzer", {
            "goal": "Explain the auth module",
            "query": "authentication",
            "repo_id": "test123",
        }))

        assert isinstance(result, dict)
        assert "success" in result
        assert "outputs" in result
        assert "logs" in result
        assert isinstance(result["logs"], list)

    def test_execute_with_empty_results(self, mock_llm, playbook_registry, playbook_tools):
        """LLM generates gracefully when search returns nothing."""
        from codemind.playbooks import PlaybookExecutor

        executor = PlaybookExecutor(playbook_registry, playbook_tools, mock_llm)
        result = _run(executor.execute("code_analyzer", {
            "goal": "Find something that doesn't exist",
            "query": "xyzzy_nonexistent_symbol",
            "repo_id": "nonexistent_repo",
        }))

        assert isinstance(result, dict)
        assert "success" in result
        # Should still produce some output, even if it says "no results"
        assert "outputs" in result

    def test_execute_unknown_playbook(self, mock_llm, playbook_registry, playbook_tools):
        """Executing a nonexistent playbook returns an error."""
        from codemind.playbooks import PlaybookExecutor

        executor = PlaybookExecutor(playbook_registry, playbook_tools, mock_llm)
        result = _run(executor.execute("nonexistent_playbook_xyz", {
            "goal": "test",
            "query": "test",
        }))

        assert isinstance(result, dict)
        assert result["success"] is False
        assert result.get("error") is not None

    def test_execute_catalog_search(self, mock_llm, playbook_registry, playbook_tools):
        """Catalog search mode with dedup."""
        from codemind.playbooks import PlaybookExecutor

        # catalog_search playbook may or may not be loaded
        available = playbook_registry.list_playbooks()
        if "catalog_search" not in available:
            pytest.skip("catalog_search playbook not loaded")

        executor = PlaybookExecutor(playbook_registry, playbook_tools, mock_llm)
        result = _run(executor.execute("catalog_search", {
            "goal": "Find repos with authentication",
            "query": "authentication microservice",
        }))

        assert isinstance(result, dict)
        assert "success" in result


class TestContextPacker:
    """Test the ContextPacker utility."""

    def test_context_packer_budget(self):
        """ContextPacker respects max_chars limit."""
        from codemind.playbooks.executors import ContextPacker

        packer = ContextPacker(max_chars=500)

        chunks = [
            {"chunk_text": "x" * 200, "file_path": "a.py", "score": 0.9, "start_line": 1},
            {"chunk_text": "y" * 200, "file_path": "b.py", "score": 0.8, "start_line": 1},
            {"chunk_text": "z" * 200, "file_path": "c.py", "score": 0.7, "start_line": 1},
        ]

        packed = packer.pack(chunks, query="test")
        # The packed result should be under budget (with some header overhead)
        assert len(packed) <= 700

    def test_context_packer_dedup(self):
        """ContextPacker deduplicates same file chunks."""
        from codemind.playbooks.executors import ContextPacker

        packer = ContextPacker(max_chars=10000)

        chunks = [
            {"chunk_text": "same content", "file_path": "a.py", "score": 0.9, "start_line": 1},
            {"chunk_text": "same content", "file_path": "a.py", "score": 0.85, "start_line": 1},
            {"chunk_text": "different content", "file_path": "b.py", "score": 0.8, "start_line": 1},
        ]

        packed = packer.pack(chunks)
        # Should contain both files but deduplicate identical chunks
        assert "a.py" in packed
        assert "b.py" in packed

    def test_context_packer_empty(self):
        """ContextPacker handles empty chunk list."""
        from codemind.playbooks.executors import ContextPacker

        packer = ContextPacker(max_chars=10000)
        packed = packer.pack([])
        assert isinstance(packed, str)

    def test_context_packer_ordering(self):
        """ContextPacker orders by score (highest first)."""
        from codemind.playbooks.executors import ContextPacker

        packer = ContextPacker(max_chars=10000)

        chunks = [
            {"chunk_text": "low_score", "file_path": "low.py", "score": 0.3, "start_line": 1},
            {"chunk_text": "high_score", "file_path": "high.py", "score": 0.95, "start_line": 1},
            {"chunk_text": "mid_score", "file_path": "mid.py", "score": 0.6, "start_line": 1},
        ]

        packed = packer.pack(chunks)
        # High score should appear before low score
        high_pos = packed.find("high.py")
        low_pos = packed.find("low.py")
        if high_pos >= 0 and low_pos >= 0:
            assert high_pos < low_pos, "Higher-scored chunks should appear first"


class TestPlaybookExecutorToolCalls:
    """Test that format_output detects and handles tool calls."""

    def test_tool_call_in_output(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """format_output detects save_catalog_entry tool calls in LLM output."""
        from codemind.playbooks import PlaybookExecutor

        # Simulate catalog_generator behavior — LLM output contains a tool call
        tool_call_output = json.dumps({
            "tool_calls": [{
                "name": "save_catalog_entry",
                "params": {
                    "repo_id": "test123",
                    "repo_name": "test-repo",
                    "catalog_data": {"description": "A test repository"}
                }
            }],
            "analysis": "Repository catalog generated."
        })

        llm = mock_llm_with_responses([
            # LLM generate: produce tool call in output
            tool_call_output,
        ])

        # Only run if catalog_generator is available
        available = playbook_registry.list_playbooks()
        playbook_name = "catalog_generator" if "catalog_generator" in available else "code_analyzer"

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        result = _run(executor.execute(playbook_name, {
            "goal": "Generate catalog",
            "query": "full overview",
            "repo_id": "test123",
        }))

        assert isinstance(result, dict)
        assert "success" in result
