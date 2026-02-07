#!/usr/bin/env python3
"""
Test script for advanced filter features.
Run this after indexing a repository to test all filter types.
"""

import requests
import json

BASE_URL = "http://localhost:8000"

# Replace with your actual repo_id
REPO_ID = "5d0cf96ff9e69e48"  # Update this!


def test_filter(name: str, filter_config: dict):
    """Test a filter configuration."""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    print(f"Filter: {json.dumps(filter_config, indent=2)}")
    
    response = requests.post(
        f"{BASE_URL}/api/v1/search",
        json=filter_config
    )
    
    if response.status_code == 200:
        results = response.json()
        print(f"✅ Success: {len(results)} results")
        
        # Show first 2 results
        for i, result in enumerate(results[:2], 1):
            print(f"\n  Result {i}:")
            print(f"    File: {result['file_path']}")
            print(f"    Score: {result['score']:.3f}")
            if result.get('context'):
                print(f"    Classes: {result['context'].get('classes', [])}")
                print(f"    Functions: {result['context'].get('functions', [])}")
    else:
        print(f"❌ Failed: {response.status_code}")
        print(f"   {response.text}")


def main():
    print("🧪 Advanced Filters Test Suite")
    print(f"Testing against repo: {REPO_ID}")
    
    # Test 1: OR Logic - Multiple file types
    test_filter(
        "OR Logic - Multiple File Types",
        {
            "query": "configuration",
            "repo_id": REPO_ID,
            "search_mode": "hybrid",
            "filters": {
                "file_types": [".py", ".yaml", ".json"]
            },
            "limit": 5
        }
    )
    
    # Test 2: Multiple patterns (OR)
    test_filter(
        "OR Logic - Multiple Patterns",
        {
            "query": "testing",
            "repo_id": REPO_ID,
            "search_mode": "hybrid",
            "filters": {
                "file_patterns": ["test_", "spec_", "_test"]
            },
            "limit": 5
        }
    )
    
    # Test 3: Regex pattern
    test_filter(
        "Regex Pattern - Test files only",
        {
            "query": "authentication",
            "repo_id": REPO_ID,
            "search_mode": "hybrid",
            "filters": {
                "file_pattern_regex": ".*test.*\\.py$"
            },
            "limit": 5
        }
    )
    
    # Test 4: Exclusion filters
    test_filter(
        "Exclusion Filters - Exclude tests and cache",
        {
            "query": "database",
            "repo_id": REPO_ID,
            "search_mode": "hybrid",
            "filters": {
                "file_type": ".py",
                "exclude_patterns": ["test_", "__pycache__", "migration"]
            },
            "limit": 5
        }
    )
    
    # Test 5: Multiple class names (OR)
    test_filter(
        "OR Logic - Multiple Classes",
        {
            "query": "handle request",
            "repo_id": REPO_ID,
            "search_mode": "hybrid",
            "filters": {
                "class_names": ["FastAPI", "APIRouter", "Request"]
            },
            "expand_context": True,
            "limit": 5
        }
    )
    
    # Test 6: Complex combination
    test_filter(
        "Complex Filter - Everything combined",
        {
            "query": "routing logic",
            "repo_id": REPO_ID,
            "search_mode": "hybrid",
            "filters": {
                "file_types": [".py", ".js"],
                "file_patterns": ["api", "route"],
                "exclude_patterns": ["test_", "node_modules"],
                "class_names": ["Router", "APIRouter"]
            },
            "expand_context": True,
            "limit": 5
        }
    )
    
    # Test 7: Structural only with exclusions
    test_filter(
        "Structural Search with Exclusions",
        {
            "query": "files",
            "repo_id": REPO_ID,
            "search_mode": "structural",
            "filters": {
                "file_type": ".py",
                "exclude_pattern_regex": ".*(test|cache|migration).*"
            },
            "expand_context": True,
            "limit": 10
        }
    )
    
    print("\n" + "="*60)
    print("✅ Test suite complete!")
    print("="*60)


if __name__ == "__main__":
    main()
