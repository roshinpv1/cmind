"""
Test autonomous planner agent.

Tests the full think-act-observe loop with real skills.
"""

import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from codemind.skills import SkillRegistry, SkillExecutor, SkillTools
from codemind.agents import PlannerAgent


async def test_autonomous_planner():
    """Test autonomous planner with mock data layer."""
    print("=" * 60)
    print("Testing Autonomous Planner Agent")
    print("=" * 60)
    
    # Initialize components
    print("\n1. Initializing skill system...")
    registry = SkillRegistry(skills_dir="skills")
    print(f"   ✓ Loaded {len(registry)} skills")
    
    # Mock data layer
    print("\n2. Initializing mock data layer...")
    
    class MockStorage:
        def search(self, embedding, repo_id=None, limit=10):
            # Return mock search results based on context
            return [
                {
                    "file_path": "src/auth/middleware.py",
                    "chunk_text": "def authenticate(request):\n    # Verify JWT token\n    token = request.headers.get('Authorization')\n    return verify_token(token)",
                    "score": 0.92
                },
                {
                    "file_path": "src/auth/handlers.py",
                    "chunk_text": "class AuthHandler:\n    def login(self, username, password):\n        # Check credentials\n        user = db.get_user(username)\n        return user.verify_password(password)",
                    "score": 0.87
                }
            ]
    
    class MockGraph:
        def find_files_by_pattern(self, repo_id, file_type=None):
            if file_type == ".py":
                return ["main.py", "src/auth/middleware.py", "src/auth/handlers.py", "src/api/routes.py"]
            return ["README.md", "package.json"]
        
        def filter_by_structure(self, repo_id, filters):
            return ["src/auth/middleware.py", "src/auth/handlers.py"]
    
    class MockLLM:
        def __init__(self):
            self.call_count = 0
        
        async def generate(self, prompt, max_tokens=500):
            self.call_count += 1
            
            # First call: think - select skill
            if self.call_count == 1:
                return """I need to find authentication code in the repository.

SKILL: search_codebase
PARAMS: {"query": "authentication functions", "repo_id": "test_repo", "limit": 5}
REASONING: Searching for authentication-related code will help locate the relevant files."""
            
            # Second call: think - decide to finish
            elif self.call_count == 2:
                return """Based on the search results, we found authentication code in:
- src/auth/middleware.py (JWT token verification)
- src/auth/handlers.py (login functionality)

FINISH: Found authentication code in src/auth/ directory with JWT middleware and login handlers."""
            
            # Final call: synthesis
            else:
                return """The authentication code is located in the src/auth/ directory:

1. **src/auth/middleware.py** - Contains the authenticate() function that verifies JWT tokens from request headers
2. **src/auth/handlers.py** - Contains the AuthHandler class with login() method for credential verification

The system uses JWT-based authentication with token verification."""
    
    class MockEmbedder:
        class MockModel:
            def encode(self, texts):
                return [[0.1] * 384 for _ in texts]
        model = MockModel()
    
    lance_storage = MockStorage()
    graph_service = MockGraph()
    llm_client = MockLLM()
    embedder = MockEmbedder()
    
    print("   ✓ Mock data layer initialized")
    
    # Initialize tools
    print("\n3. Initializing tools and executor...")
    tools = SkillTools(lance_storage, graph_service, llm_client, embedder)
    executor = SkillExecutor(registry, tools)
    print("   ✓ Tools and executor ready")
    
    # Initialize planner
    print("\n4. Initializing planner agent...")
    planner = PlannerAgent(registry, executor, llm_client)
    print("   ✓ Planner agent ready")
    
    # Test autonomous execution
    print("\n5. Testing autonomous execution...")
    print("=" * 60)
    
    goal = "Find all authentication code in the repository"
    repo_id = "test_repo"
    
    result = await planner.execute(goal, repo_id, max_iterations=5)
    
    # Display results
    print("\n" + "=" * 60)
    print("🎯 AUTONOMOUS EXECUTION RESULT")
    print("=" * 60)
    print(f"\nGoal: {result['goal']}")
    print(f"\nAnswer:\n{result['answer']}")
    print(f"\nSteps Taken: {result['steps_taken']}")
    print(f"Iterations: {result['iterations']}")
    print(f"Skills Used: {', '.join(result.get('skills_used', []))}")
    
    if "error" in result:
        print(f"\nError: {result['error']}")
    
    print("\n" + "=" * 60)
    print("✅ Autonomous planner test complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_autonomous_planner())
