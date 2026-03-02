"""Pytest configuration and shared fixtures.

Provides mock infrastructure for CI-friendly testing:
- MockLLMDriver: Returns pre-programmed responses (no real LLM)
- MockEmbedder: Returns fixed-dimension embeddings
- In-memory SQLite database
- Temporary LanceDB storage
- FastAPI TestClient with all services wired up
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fixtures: Sample data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_repo_path(tmp_path: Path) -> Path:
    """Provides a temporary directory with sample source files."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Create a simple Python file
    (repo_dir / "main.py").write_text('''
def hello_world():
    """A simple hello world function."""
    return "Hello, World!"

class SampleClass:
    """A sample class for testing."""

    def __init__(self, name: str):
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}!"
''')

    # Create a second file for relationship testing
    (repo_dir / "utils.py").write_text('''
from main import SampleClass

def create_greeter(name: str) -> SampleClass:
    """Factory function for SampleClass."""
    return SampleClass(name)
''')

    return repo_dir


@pytest.fixture
def sample_python_file(tmp_path: Path) -> Path:
    """Provides a sample Python file for testing."""
    file_path = tmp_path / "sample.py"
    file_path.write_text('''
def hello_world():
    """A simple hello world function."""
    return "Hello, World!"

class SampleClass:
    """A sample class for testing."""

    def __init__(self, name: str):
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}!"
''')
    return file_path


# ---------------------------------------------------------------------------
# Fixtures: Mock LLM
# ---------------------------------------------------------------------------

class MockLLMDriver:
    """
    Mock LLM driver that returns pre-programmed responses.
    
    Inspects the prompt to decide what to return, simulating the
    PlannerAgent's expected LLM behavior.
    """

    class _Config:
        """Mimics LLMConfig enough for PlannerAgent._think."""
        max_tokens = 4000
        temperature = 0.1

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self._call_count = 0
        self.prompts: list[str] = []  # Record all prompts for assertions
        self.config = self._Config()
        self.driver = self  # Self-referencing: acts as both chat_model and driver
        self.model = "mock-model"

    async def generate(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        idx = self._call_count
        self._call_count += 1

        # If pre-programmed responses are provided, use them
        if idx < len(self.responses):
            return self.responses[idx]

        # Default behavior: detect what the prompt is asking for
        prompt_lower = prompt.lower()

        # Planner "think" step — return a playbook action
        if "think" in prompt_lower or "what should" in prompt_lower or "goal:" in prompt_lower:
            return (
                "THOUGHT: I should search the codebase for relevant code.\n"
                "PLAYBOOK: code_analyzer\n"
                'PARAMS: {"query": "authentication flow", "repo_id": "test123"}'
            )

        # Planner "finish" step — return a final answer
        if "synthesize" in prompt_lower or "final answer" in prompt_lower or "summarize" in prompt_lower:
            return json.dumps({
                "answer": "The codebase uses JWT-based authentication with middleware in auth.py.",
                "confidence": 0.85,
            })

        # Playbook executor LLM generation
        if "system_prompt" in prompt_lower or "analyze" in prompt_lower or "code context" in prompt_lower:
            return json.dumps({
                "analysis": "This is a well-structured Python application.",
                "key_findings": ["Uses FastAPI", "Has test coverage"],
                "recommendations": ["Add type hints to utils.py"]
            })

        # Fallback
        return "FINISH: No relevant information found."

    def is_available(self) -> bool:
        return True


@pytest.fixture
def mock_llm():
    """Provides a MockLLMDriver instance."""
    return MockLLMDriver()


@pytest.fixture
def mock_llm_with_responses():
    """Factory fixture for MockLLMDriver with custom responses."""
    def _factory(responses: list[str]):
        return MockLLMDriver(responses=responses)
    return _factory


# ---------------------------------------------------------------------------
# Fixtures: Mock Embedder
# ---------------------------------------------------------------------------

class MockEmbedder:
    """Mock embedding generator that returns fixed-dimension vectors."""

    EMBEDDING_DIM = 768
    model_name = "mock-embedder"
    embedding_dim = 768

    def encode_query(self, query: str) -> list[float]:
        """Return a deterministic embedding based on query hash."""
        np.random.seed(hash(query) % (2**31))
        return np.random.randn(self.EMBEDDING_DIM).tolist()

    def encode_document(self, text: str) -> list[float]:
        """Return a deterministic embedding based on text hash."""
        np.random.seed(hash(text) % (2**31))
        return np.random.randn(self.EMBEDDING_DIM).tolist()

    def get_embedding_dim(self) -> int:
        return self.EMBEDDING_DIM

    def generate_embeddings(self, chunks, existing_hashes=None):
        """Mock batch embedding generation for chunks."""
        results = []
        for chunk in chunks:
            emb = self.encode_document(chunk.chunk_text if hasattr(chunk, 'chunk_text') else str(chunk))
            results.append((chunk, emb))
        return results


@pytest.fixture
def mock_embedder():
    """Provides a MockEmbedder instance."""
    return MockEmbedder()


# ---------------------------------------------------------------------------
# Fixtures: Database (in-memory)
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Creates a temporary SQLite database with all tables."""
    from codemind.storage.database import Database
    db_path = tmp_path / "test.db"
    db = Database(str(db_path))
    db.init_db()

    # Also create the jobs table
    from codemind.jobs.job_manager import JobModel  # noqa: F401 — registers model
    db.init_db()  # Re-init to pick up JobModel

    return db


# ---------------------------------------------------------------------------
# Fixtures: LanceDB Storage
# ---------------------------------------------------------------------------

@pytest.fixture
def lance_storage(tmp_path):
    """Provides a temporary LanceDB storage instance."""
    from codemind.storage.lancedb_storage import LanceDBStorage
    lance_dir = tmp_path / "lancedb_test"
    lance_dir.mkdir()
    return LanceDBStorage(str(lance_dir))


# ---------------------------------------------------------------------------
# Fixtures: Graph Service (mock)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_graph_service():
    """Provides a mock GraphQueryService."""
    service = MagicMock()
    service.get_callers.return_value = []
    service.get_callees.return_value = []
    service.get_dependents.return_value = []
    service.get_dependencies.return_value = []
    service.get_file_symbols.return_value = []
    service.expand_context.return_value = {}
    return service


# ---------------------------------------------------------------------------
# Fixtures: Playbook Registry
# ---------------------------------------------------------------------------

@pytest.fixture
def playbook_registry():
    """Loads real playbooks from the playbooks/ directory."""
    from codemind.playbooks import PlaybookRegistry
    return PlaybookRegistry()


# ---------------------------------------------------------------------------
# Fixtures: PlaybookTools
# ---------------------------------------------------------------------------

@pytest.fixture
def playbook_tools(lance_storage, mock_graph_service, mock_embedder, tmp_db):
    """Provides a PlaybookTools instance with mocked dependencies."""
    from codemind.playbooks import PlaybookTools
    return PlaybookTools(lance_storage, mock_graph_service, mock_embedder, tmp_db)


# ---------------------------------------------------------------------------
# Fixtures: FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client(tmp_path, mock_llm, mock_embedder):
    """
    Provides a FastAPI TestClient with all services initialized using mocks.

    Uses the REAL server app and routes, but with temporary storage and mock LLM/embedder.
    This doesn't require a running server, LLM, or GPU.
    """
    from fastapi.testclient import TestClient
    from codemind.storage.lancedb_storage import LanceDBStorage
    from codemind.storage import ManifestManager
    from codemind.jobs import JobManager
    from codemind.graph import SQLiteGraphAdapter
    from codemind.graph.graph_query import GraphQueryService

    # Create temp storage
    db_path = str(tmp_path / "api_test.db")
    lance_dir = str(tmp_path / "lancedb_api_test")
    os.makedirs(lance_dir, exist_ok=True)

    # Use the REAL server app
    from codemind.api.server import app

    # Initialize real-but-temporary storage
    manifest = ManifestManager(db_path=db_path)
    lance = LanceDBStorage(lance_dir)
    job_manager = JobManager(db_path=db_path)
    graph_db = SQLiteGraphAdapter(job_manager.db)
    graph_query = GraphQueryService(graph_db)

    # Wire up app state with mocked/temp infrastructure
    app.state.manifest = manifest
    app.state.lance_storage = lance
    app.state.job_manager = job_manager
    app.state.graph_db = graph_db
    app.state.graph_query = graph_query
    app.state.embedder = mock_embedder

    # Initialize autonomous agents with mock LLM
    from codemind.api.autonomous_agents import init_autonomous_agents, router as autonomous_router
    init_autonomous_agents(lance, graph_query, mock_llm, mock_embedder, manifest, job_manager.db)

    # Check if the autonomous router is already included
    existing_paths = {route.path for route in app.routes}
    if "/api/v1/agents/autonomous" not in existing_paths:
        app.include_router(autonomous_router)

    client = TestClient(app)
    yield client

    # Cleanup
    graph_db.close()

