"""
Integration Test for Autonomous Agent (Phase-1)

Runs the REAL planner flow against the REAL LLM (LM Studio / local).
Captures and prints:
  - Prompt sent to LLM
  - LLM response
  - Playbook selection decision
  - Playbook execution result
  - Final answer

Usage:
    python -m tests.test_agent_integration

Requires:
    - LM Studio (or compatible) running on localhost:1234
    - A repository already indexed (repo_id)
"""

import asyncio
import socket
import sys
import os
import json
import pytest
from datetime import datetime
from pathlib import Path


def _llm_is_running() -> bool:
    """Check if a local LLM server is reachable."""
    try:
        s = socket.create_connection(("localhost", 1234), timeout=1)
        s.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _llm_is_running(),
    reason="LLM server not running on localhost:1234",
)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ─────────────────────────────────────────────────
# LLM Wrapper that captures all prompts and responses
# ─────────────────────────────────────────────────

class CapturingLLMWrapper:
    """Wraps any LLM client to capture all prompts and responses."""

    def __init__(self, real_client):
        self._real = real_client
        self.interactions: list[dict] = []

    async def generate(self, prompt: str, **kwargs) -> str:
        """Intercept generate calls, log, and forward."""
        ts = datetime.now().isoformat()
        response = await self._real.generate(prompt, **kwargs)
        self.interactions.append({
            "timestamp": ts,
            "prompt": prompt,
            "response": response,
            "kwargs": {k: str(v) for k, v in kwargs.items()},
        })
        return response

    def print_interactions(self):
        """Pretty-print all captured interactions."""
        print("\n" + "=" * 80)
        print("📋 CAPTURED LLM INTERACTIONS")
        print("=" * 80)
        for i, interaction in enumerate(self.interactions, 1):
            print(f"\n{'─' * 60}")
            print(f"  🔹 Interaction #{i}  ({interaction['timestamp']})")
            print(f"  kwargs: {interaction['kwargs']}")
            print(f"{'─' * 60}")
            print(f"\n  📤 PROMPT SENT TO LLM:\n")
            for line in interaction["prompt"].split("\n"):
                print(f"    | {line}")
            print(f"\n  📥 LLM RESPONSE:\n")
            for line in interaction["response"].split("\n"):
                print(f"    | {line}")
        print(f"\n{'=' * 80}")
        print(f"  Total LLM calls: {len(self.interactions)}")
        print(f"{'=' * 80}\n")


# ─────────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────────

async def run_test(goal: str, repo_id: str):
    """Run a single integration test case."""

    print(f"\n{'═' * 80}")
    print(f"🧪 INTEGRATION TEST: Autonomous Agent (Phase-1)")
    print(f"{'═' * 80}")
    print(f"  Goal:    {goal}")
    print(f"  Repo ID: {repo_id}")
    print(f"{'═' * 80}\n")

    # ── Step 1: Initialize components ──
    print("📦 [1/4] Initializing components...")

    from codemind.llm.factory import get_llm_client
    from codemind.playbooks import PlaybookRegistry, PlaybookExecutor, PlaybookTools
    from codemind.agents import PlannerAgent

    # Get real LLM client and wrap it
    real_llm = get_llm_client()
    llm = CapturingLLMWrapper(real_llm)
    print(f"  ✓ LLM client: {type(real_llm).__name__}")

    # Initialize playbook registry
    registry = PlaybookRegistry()
    available_playbooks = registry.list_playbooks()
    print(f"  ✓ Registry: {len(registry)} playbooks loaded: {available_playbooks}")

    if not available_playbooks:
        print("  ✗ ERROR: No playbooks loaded! Check playbooks/ directory.")
        return

    # Show what the planner will see
    print(f"\n  Playbooks prompt format:")
    for name in available_playbooks:
        playbook = registry.get_playbook(name)
        print(f"    - {name}: {playbook.when_to_use[:100]}...")

    # ── Step 2: Initialize executor (needs real search backend) ──
    print("\n📦 [2/4] Initializing executor...")

    # Try to get storage and graph service from the server module
    try:
        from codemind.storage.lance_storage import LanceDBStorage
        from codemind.graph.service import GraphQueryService
        from codemind.embeddings import get_embedder

        storage_path = os.environ.get("LANCE_STORAGE_PATH", "data/lancedb")
        lance_storage = LanceDBStorage(storage_path)
        
        graph_db_path = os.environ.get("GRAPH_DB_PATH", "data/graph_db")
        graph_service = GraphQueryService(graph_db_path)
        
        embedder = get_embedder()
        
        tools = PlaybookTools(lance_storage, graph_service, embedder)
        # Use the capturing wrapper for ALL LLM calls (executor + planner)
        executor = PlaybookExecutor(registry, tools, llm)
        print(f"  ✓ Executor ready (with real search backend)")
    except Exception as e:
        print(f"  ⚠ Could not initialize real search backend: {e}")
        print(f"  ⚠ Using mock tools (search will return empty results)")
        
        # Create a minimal mock
        class MockTools:
            async def search_codebase(self, search_params):
                print(f"  [MOCK] search_codebase called with: {search_params}")
                return {"success": True, "results": [], "count": 0}
        
        tools = MockTools()
        executor = PlaybookExecutor(registry, tools, llm)

    # ── Step 3: Run planner ──
    print(f"\n🚀 [3/4] Running planner...")
    print(f"{'─' * 60}")

    planner = PlannerAgent(registry, executor, llm)
    
    try:
        result = await planner.execute(goal, repo_id, max_iterations=5)
    except Exception as e:
        print(f"\n  ✗ PLANNER CRASHED: {e}")
        import traceback
        traceback.print_exc()
        result = {"error": str(e)}

    # ── Step 4: Print results ──
    print(f"\n{'─' * 60}")
    print(f"\n📊 [4/4] RESULTS")
    print(f"{'─' * 60}")

    if result:
        print(f"\n  Goal:         {result.get('goal', 'N/A')}")
        print(f"  Steps taken:  {result.get('steps_taken', 'N/A')}")
        print(f"  Iterations:   {result.get('iterations', 'N/A')}")
        print(f"  Playbooks used:  {result.get('playbooks_used', 'N/A')}")
        
        if result.get("error"):
            print(f"\n  ❌ Error: {result['error']}")
        
        answer = result.get("answer", "")
        if answer:
            print(f"\n  📝 ANSWER:")
            print(f"  {'─' * 40}")
            # Show first 1000 chars
            for line in answer[:1000].split("\n"):
                print(f"    {line}")
            if len(answer) > 1000:
                print(f"    ... ({len(answer) - 1000} more chars)")
    else:
        print("  ✗ No result returned")

    # ── Print all captured LLM interactions ──
    llm.print_interactions()

    return result


# ─────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────

if __name__ == "__main__":
    # Default test parameters
    default_goal = "Provide me the application flow and API endpoints and its integrations"
    default_repo_id = "99d2025f600a3a09"

    # Allow overriding via CLI args
    goal = sys.argv[1] if len(sys.argv) > 1 else default_goal
    repo_id = sys.argv[2] if len(sys.argv) > 2 else default_repo_id

    asyncio.run(run_test(goal, repo_id))
