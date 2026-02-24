"""
Diagnostic script to check why endpoints might not be registering.
Run on the enterprise system: python3 scripts/diagnose_routes.py
"""
import sys
import os

# Ensure src is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

print(f"Python version: {sys.version}")

try:
    import pydantic
    print(f"Pydantic version: {pydantic.VERSION}")
except ImportError:
    print("Pydantic NOT installed!")

print("\n--- Attempting to load server module ---")
try:
    # Load .env first
    from dotenv import load_dotenv
    load_dotenv()
    
    from codemind.api.server import app
    
    print(f"\n✅ Server module loaded successfully")
    print(f"\n--- Registered Routes ---")
    count = 0
    for route in app.routes:
        if hasattr(route, "methods"):
            for method in route.methods:
                path = route.path
                if any(x in path for x in ['/repos', '/catalogs', '/debug']):
                    print(f"  {method:6s} {path}")
                count += 1
    print(f"\nTotal routes: {count}")
    
    # Check specific endpoints
    repo_detail_found = any(
        hasattr(r, 'path') and r.path == '/api/v1/repos/{repo_id}' 
        for r in app.routes
    )
    catalogs_list_found = any(
        hasattr(r, 'path') and r.path == '/api/v1/catalogs/list' 
        for r in app.routes
    )
    print(f"\nGET /api/v1/repos/{{repo_id}}: {'✅ FOUND' if repo_detail_found else '❌ MISSING'}")
    print(f"GET /api/v1/catalogs/list:    {'✅ FOUND' if catalogs_list_found else '❌ MISSING'}")
    
except Exception as e:
    print(f"\n❌ Failed to load server module: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print("\n--- Testing model class definitions ---")
try:
    from pydantic import BaseModel
    
    class TestParent(BaseModel):
        name: str | None = None
        status: str = "ok"
        
    class TestChild(TestParent):
        org: str | None = None
        
    print("✅ Model inheritance with str | None works")
except Exception as e:
    print(f"❌ Model inheritance failed: {e}")

try:
    from pydantic import BaseModel
    
    class TestParent2(BaseModel):
        last_pr: str | None = None
        
    class TestChild2(TestParent2):
        last_pr: str | None = None  # DUPLICATE field
        
    print("✅ Duplicate field redefinition works")
except Exception as e:
    print(f"❌ Duplicate field redefinition FAILS: {e}")
    print("   This is likely the root cause!")
