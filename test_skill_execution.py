"""
Test skill execution with real data layer.

Tests:
- Skill executor initialization
- Tool execution
- Integration with LanceDB, Kùzu, LLM
"""

import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from codemind.skills import SkillRegistry, SkillExecutor, SkillTools
from codemind.storage.lancedb_storage import LanceDBStorage
from codemind.graph.graph_query import GraphQueryService
from codemind.graph.kuzu_graph import KuzuGraphDB
from codemind.llm.factory import get_llm_client


async def test_skill_execution():
    """Test skill execution with mock data layer."""
    print("=" * 60)
    print("Testing Skill Execution Layer")
    print("=" * 60)
    
    # Initialize registry
    print("\n1. Loading skills...")
    registry = SkillRegistry(skills_dir="skills")
    print(f"   ✓ Loaded {len(registry)} skills")
    
    # Initialize data layer components
    print("\n2. Initializing data layer...")
    try:
        lance_storage = LanceDBStorage("data/lancedb")
        graph_db = KuzuGraphDB("data/kuzu_graph")
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
    print("\n3. Initializing skill tools...")
    tools = SkillTools(
        lance_storage=lance_storage,
        graph_service=graph_service,
        llm_client=llm_client,
        embedder=embedder
    )
    print("   ✓ Tools initialized")
    
    # Initialize executor
    print("\n4. Initializing skill executor...")
    executor = SkillExecutor(registry, tools)
    print("   ✓ Executor initialized")
    print(f"   ✓ {len(executor.tool_handlers)} tool handlers registered")
    
    # Test each skill
    print("\n5. Testing skill execution...")
    print("=" * 60)
    
    # Test 1: search_codebase
    print("\n📍 Test 1: search_codebase")
    result = await executor.execute("search_codebase", {
        "query": "authentication functions",
        "repo_id": "test_repo",
        "limit": 5,
        "mode": "semantic"
    })
    print(f"   Success: {result['success']}")
    print(f"   Results: {result['outputs'].get('count', 0)}")
    print(f"   Logs: {result['logs']}")
    if result['error']:
        print(f"   Error: {result['error']}")
    
    # Test 2: analyze_structure
    print("\n📍 Test 2: analyze_structure")
    result = await executor.execute("analyze_structure", {
        "repo_id": "test_repo",
        "analysis_type": "files"
    })
    print(f"   Success: {result['success']}")
    print(f"   Total files: {result['outputs'].get('total_files', 0)}")
    print(f"   File types: {result['outputs'].get('file_types', [])}")
    print(f"   Logs: {result['logs']}")
    
    # Test 3: explain_code (LLM-based)
    print("\n📍 Test 3: explain_code (LLM)")
    result = await executor.execute("explain_code", {
        "file_path": "test.py",
        "repo_id": "test_repo"
    })
    print(f"   Success: {result['success']}")
    print(f"   Language: {result['outputs'].get('language', 'unknown')}")
    print(f"   Explanation: {result['outputs'].get('explanation', '')[:100]}...")
    print(f"   Logs: {result['logs']}")
    
    # Test 4: generate_documentation (composite)
    print("\n📍 Test 4: generate_documentation (composite)")
    result = await executor.execute("generate_documentation", {
        "repo_id": "test_repo",
        "doc_type": "readme",
        "include_examples": True
    })
    print(f"   Success: {result['success']}")
    print(f"   Word count: {result['outputs'].get('word_count', 0)}")
    print(f"   Sections: {result['outputs'].get('sections', [])}")
    print(f"   Logs: {result['logs']}")
    
    # Test error handling
    print("\n📍 Test 5: Error handling (invalid skill)")
    result = await executor.execute("nonexistent_skill", {})
    print(f"   Success: {result['success']}")
    print(f"   Error: {result['error']}")
    
    print("\n" + "=" * 60)
    print("✅ Skill execution tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_skill_execution())
