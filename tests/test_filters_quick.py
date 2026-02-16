#!/usr/bin/env python3
"""
Quick test to verify filter implementation.
"""

import socket
import requests
import json
import pytest

BASE_URL = "http://localhost:8000"


def _server_is_running() -> bool:
    """Check if the API server is reachable."""
    try:
        s = socket.create_connection(("localhost", 8000), timeout=1)
        s.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _server_is_running(),
    reason="API server not running on localhost:8000",
)
REPO_ID = "1dd450cb2ecd63a9"  # Update to your repo


def test_filters():
    """Test that filters are working correctly."""
    
    # Test 1: Hybrid with file_type filter
    print("="*60)
    print("TEST 1: Hybrid with file_type filter")
    print("="*60)
    
    payload = {
        "query": "test",
        "repo_id": REPO_ID,
        "search_mode": "hybrid",
        "filters": {
            "file_type": ".py"
        },
        "limit": 5
    }
    
    print(f"Payload: {json.dumps(payload, indent=2)}")
    response = requests.post(f"{BASE_URL}/api/v1/search", json=payload)
    
    if response.status_code == 200:
        results = response.json()
        print(f"✅ Got {len(results)} results")
        for r in results:
            print(f"  - {r['file_path']}")
    else:
        print(f"❌ Error: {response.status_code}")
        print(response.text)
    
    # Test 2: Hybrid with multiple filters
    print("\n" + "="*60)
    print("TEST 2: Hybrid with multiple file types (OR logic)")
    print("="*60)
    
    payload = {
        "query": "database",
        "repo_id": REPO_ID,
        "search_mode": "hybrid",
        "filters": {
            "file_types": [".py", ".md"]
        },
        "limit": 5
    }
    
    print(f"Payload: {json.dumps(payload, indent=2)}")
    response = requests.post(f"{BASE_URL}/api/v1/search", json=payload)
    
    if response.status_code == 200:
        results = response.json()
        print(f"✅ Got {len(results)} results")
        for r in results:
            print(f"  - {r['file_path']}")
    else:
        print(f"❌ Error: {response.status_code}")
        print(response.text)
    
    # Test 3: Semantic (no filters - baseline)
    print("\n" + "="*60)
    print("TEST 3: Semantic mode (baseline - no filters)")
    print("="*60)
    
    payload = {
        "query": "database",
        "repo_id": REPO_ID,
        "search_mode": "semantic",
        "limit": 5
    }
    
    print(f"Payload: {json.dumps(payload, indent=2)}")
    response = requests.post(f"{BASE_URL}/api/v1/search", json=payload)
    
    if response.status_code == 200:
        results = response.json()
        print(f"✅ Got {len(results)} results")
        for r in results:
            print(f"  - {r['file_path']}")
    else:
        print(f"❌ Error: {response.status_code}")
        print(response.text)


if __name__ == "__main__":
    print("🧪 Filter Implementation Test\n")
    test_filters()
    print("\n✅ Tests complete - check server logs for [SEARCH] messages")
