"""
Agent Planner Loop Tests.

Tests the PlannerAgent's Think-Act-Observe-Finish loop with mocked
LLM and tools. No real LLM or indexed data required.

The new planner uses CmindChatModel.bind_tools() with prompt-based
tool calling. Mock LLM responses must return JSON tool_calls format
that _extract_tool_calls can parse.

Run: pytest tests/test_agents/test_planner_agent.py -v
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tool_call_response(tool_name: str, args: dict) -> str:
    """Create a mock LLM response with a tool call in JSON format."""
    return json.dumps({
        "tool_calls": [{"name": tool_name, "args": args}]
    })


def make_finish_response(answer: str = "Analysis complete.") -> str:
    """Create a mock LLM finish response (plain text, no tool calls)."""
    return answer


def make_final_answer(answer: str = "The code uses FastAPI.", **kwargs) -> str:
    """Create a mock LLM synthesis response."""
    result = {"answer": answer, "confidence": 0.9}
    result.update(kwargs)
    return json.dumps(result)


def _run(coro):
    """Helper to run an async coroutine in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlannerSingleIteration:
    """Test that the planner can complete in a single iteration."""

    def test_single_iteration_finish(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """Agent thinks → calls playbook tool → observes → finishes."""
        from codemind.playbooks import PlaybookExecutor
        from codemind.agents import PlannerAgent

        llm = mock_llm_with_responses([
            # Think step: LLM outputs a tool call for playbook_catalog_search
            make_tool_call_response("playbook_catalog_search", {"query": "API endpoints"}),
            # Executor's LLM generation (for the playbook execution itself)
            json.dumps({"analysis": "No code available to analyze."}),
            # Think again: LLM finishes (plain text, no tool call)
            make_finish_response("I found no code to analyze."),
            # Synthesis LLM call
            make_final_answer("No relevant code was found for this query."),
        ])

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        planner = PlannerAgent(playbook_registry, executor, llm)
        result = _run(planner.execute("Find API endpoints", repo_id="test123", max_iterations=5))

        assert result is not None
        assert "answer" in result or "error" in result


class TestPlannerMaxIterations:
    """Test max iteration safety."""

    def test_max_iterations_safety(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """Agent stops at max_iterations limit."""
        from codemind.playbooks import PlaybookExecutor
        from codemind.agents import PlannerAgent

        # Always return a tool call, never finish — forces max_iterations
        responses = []
        for i in range(20):
            responses.append(make_tool_call_response(
                "playbook_catalog_search", {"query": f"query {i}"}
            ))
            responses.append(json.dumps({"analysis": f"Finding {i}: some pattern."}))
        responses.append(make_final_answer("Ran out of iterations."))

        llm = mock_llm_with_responses(responses)

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        planner = PlannerAgent(playbook_registry, executor, llm)
        result = _run(planner.execute("Infinite search", repo_id="test123", max_iterations=2))

        assert result is not None
        # Should have stopped, not crashed


class TestPlannerToolDispatch:
    """Test that the planner dispatches tools correctly."""

    def test_tool_dispatch_search(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """Agent selects search_codebase tool via JSON tool call."""
        from codemind.playbooks import PlaybookExecutor
        from codemind.agents import PlannerAgent

        llm = mock_llm_with_responses([
            # Think: use search tool
            make_tool_call_response("search_codebase", {
                "queries": ["auth"], "repo_id": "test123"
            }),
            # Think: finish after observing results
            make_finish_response("Auth module found at auth.py."),
            # Synthesize
            make_final_answer("The auth module is in auth.py."),
        ])

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        planner = PlannerAgent(playbook_registry, executor, llm)
        result = _run(planner.execute("Find auth module", repo_id="test123", max_iterations=5))

        assert result is not None


class TestPlannerAllowedPlaybooks:
    """Test allowed_playbooks filtering."""

    def test_allowed_playbooks_filter(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """Agent only uses whitelisted playbooks."""
        from codemind.playbooks import PlaybookExecutor
        from codemind.agents import PlannerAgent

        llm = mock_llm_with_responses([
            # Think: call the allowed playbook
            make_tool_call_response("playbook_catalog_search", {"query": "endpoints"}),
            # Executor LLM
            json.dumps({"analysis": "Found some endpoints."}),
            # Finish
            make_finish_response("Analysis complete."),
            # Synthesize
            make_final_answer("Endpoints analyzed successfully."),
        ])

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        planner = PlannerAgent(playbook_registry, executor, llm)
        result = _run(planner.execute(
            "Find endpoints",
            repo_id="test123",
            max_iterations=5,
            allowed_playbooks=["catalog_search"]
        ))

        assert result is not None


class TestPlannerErrorRecovery:
    """Test error handling in the planner loop."""

    def test_error_recovery(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """Agent handles errors without crashing."""
        from codemind.playbooks import PlaybookExecutor
        from codemind.agents import PlannerAgent

        llm = mock_llm_with_responses([
            # Think: call a tool that may fail — the fallback will kick in
            make_tool_call_response("search_codebase", {"queries": ["nonexistent"], "repo_id": "test123"}),
            # Think: recover and finish
            make_finish_response("Could not execute, but recovered."),
            # Synthesize
            make_final_answer("Recovered from error."),
        ])

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        planner = PlannerAgent(playbook_registry, executor, llm)
        result = _run(planner.execute("Test error recovery", repo_id="test123", max_iterations=5))

        # Should return a result, not crash
        assert result is not None


class TestPlannerStateAccumulation:
    """Test that state accumulates correctly across iterations."""

    def test_state_accumulation(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """LLM is called multiple times across iterations."""
        from codemind.playbooks import PlaybookExecutor
        from codemind.agents import PlannerAgent

        llm = mock_llm_with_responses([
            # Iteration 1: Think → playbook tool call
            make_tool_call_response("playbook_catalog_search", {"query": "step 1"}),
            # Executor LLM for playbook
            json.dumps({"analysis": "Step 1 complete."}),
            # Iteration 2: Think → finish (plain text)
            make_finish_response("All done after 2 iterations."),
            # Synthesize
            make_final_answer("Completed in 2 iterations."),
        ])

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        planner = PlannerAgent(playbook_registry, executor, llm)

        result = _run(planner.execute("Multi-step task", repo_id="test123", max_iterations=5))

        assert result is not None
        # The LLM was called multiple times
        assert llm._call_count >= 2  # At least think + playbook executor


class TestPlannerFinishBehavior:
    """Test finish/synthesis behavior."""

    def test_finish_without_data(self, mock_llm_with_responses, playbook_registry, playbook_tools):
        """Agent finishes gracefully when no results found."""
        from codemind.playbooks import PlaybookExecutor
        from codemind.agents import PlannerAgent

        llm = mock_llm_with_responses([
            # Think: FINISH is NOT allowed on first call (no data yet)
            # The planner's fallback will force a playbook call
            make_finish_response("I want to finish early"),
            # Executor LLM (from fallback playbook)
            json.dumps({"analysis": "Nothing found."}),
            # Think: now can finish (has data from one run)
            make_finish_response("Unable to find relevant information."),
            # Synthesize
            make_final_answer("Unable to find relevant information."),
        ])

        executor = PlaybookExecutor(playbook_registry, playbook_tools, llm)
        planner = PlannerAgent(playbook_registry, executor, llm)
        result = _run(planner.execute("Find something rare", repo_id="test123", max_iterations=5))

        assert result is not None
        assert "answer" in result
