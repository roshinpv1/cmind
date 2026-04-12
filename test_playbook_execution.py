"""
Test playbook execution with real data layer.

Tests:
- Playbook executor initialization
- Tool execution
- Integration with LanceDB, Kùzu, LLM
"""

import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from codemind.playbooks import PlaybookRegistry, PlaybookExecutor, PlaybookTools
from codemind.storage.lancedb_storage import LanceDBStorage
from codemind.graph.graph_query import GraphQueryService
from codemind.graph.graph_db import GraphifyAdapter
from codemind.llm.factory import get_llm_client


async def test_playbook_execution():
    """Test playbook execution with mock data layer."""
    print("=" * 60)
    print("Testing Playbook Execution Layer")
    print("=" * 60)
    
    # Initialize registry
    print("\n1. Loading playbooks...")
    registry = PlaybookRegistry(playbooks_dir="playbooks")
    print(f"   ✓ Loaded {len(registry)} playbooks")
    
    # Initialize data layer components
    print("\n2. Initializing data layer...")
    try:
        lance_storage = LanceDBStorage("data/lancedb")
        graph_db = GraphifyAdapter()
        graph_service = GraphQueryService(graph_db)
        llm_client = get_llm_client()
        
        # For testing, we need an embedder
        # Try to get it from an existing workflow or create mock
        try:
            from codemind.embeddings.mlx_embedder import get_embedder
            embedder = get_embedder()
        except:
            print("   ⚠ Using mock embedder for testing")
            class MockEmbedder:
                class MockModel:
                    def encode(self, texts):
                        import numpy as np
                        # Return mock embeddings
                        return [np.random.rand(384).tolist() for _ in texts]
                model = MockModel()
            embedder = MockEmbedder()
        
        print("   ✓ Data layer initialized")
    
    except Exception as e:
        print(f"   ✗ Data layer initialization failed: {e}")
        print("   Using mock components for testing...")
        
        # Use mocks
        class MockStorage:
            def search(self, embedding, repo_id=None, limit=10):
                return [{
                    "file_path": "test.py",
                    "chunk_text": "def test(): pass",
                    "score": 0.95
                }]
        
        class MockGraph:
            def find_files_by_pattern(self, repo_id, file_type=None):
                return ["test.py", "main.py"]
            
            def filter_by_structure(self, repo_id, filters):
                return ["test.py"]
        
        class MockLLM:
            async def generate(self, prompt, max_tokens=500):
                return "This is a test response from the LLM."
        
        class MockEmbedder:
            class MockModel:
                def encode(self, texts):
                    import numpy as np
                    return [np.random.rand(384).tolist() for _ in texts]
            model = MockModel()
        
        lance_storage = MockStorage()
        graph_service = MockGraph()
        llm_client = MockLLM()
        embedder = MockEmbedder()
    
    # Initialize tools
    print("\n3. Initializing playbook tools...")
    tools = PlaybookTools(
        lance_storage=lance_storage,
        graph_service=graph_service,
        embedder=embedder
    )
    print("   ✓ Tools initialized")
    
    # Initialize executor
    print("\n4. Initializing playbook executor...")
    executor = PlaybookExecutor(registry, tools, llm_client)
    executor = PlaybookExecutor(registry, tools, llm_client)
    print("   ✓ Executor initialized")
    
    # Test each playbook
    print("\n5. Testing playbook execution...")
    print("=" * 60)
    
    # Test 1: code_analyzer
    print("\n📍 Test 1: code_analyzer")
    result = await executor.execute("code_analyzer", {
        "goal": "Explain the authentication logic",
        "repo_id": "test_repo"
    })
    print(f"   Success: {result['success']}")
    print(f"   Logs: {result['logs'][:2]}...") # Show first few logs
    if result['error']:
        print(f"   Error: {result['error']}")
    
    # Test error handling
    print("\n📍 Test 5: Error handling (invalid playbook)")
    result = await executor.execute("nonexistent_playbook", {})
    print(f"   Success: {result['success']}")
    print(f"   Error: {result['error']}")
    
    print("\n" + "=" * 60)
    print("✅ Playbook execution tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_playbook_execution())
