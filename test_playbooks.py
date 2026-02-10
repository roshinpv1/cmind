"""
Test script for playbook system.

Verifies:
- Playbook loading from markdown
- Playbook registry functionality
- Playbook formatting for LLM
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from codemind.playbooks import PlaybookRegistry


def test_playbook_loading():
    """Test that playbooks load correctly from markdown files."""
    print("=" * 60)
    print("Testing Playbook System")
    print("=" * 60)
    
    # Initialize registry
    registry = PlaybookRegistry(playbooks_dir="playbooks")
    
    print(f"\n{registry}")
    print(f"Loaded {len(registry)} playbooks")
    
    # List all playbooks
    print("\n📋 Available Playbooks:")
    for playbook_name in registry.list_playbooks():
        playbook = registry.get_playbook(playbook_name)
        print(f"  • {playbook_name}")
        print(f"    Description: {playbook.description[:60]}...")
        print(f"    Deterministic: {playbook.deterministic}")
        print(f"    Prompt Length: {len(playbook.system_prompt)}")
        print(f"    Query Strategy: {len(playbook.search_strategy.queries)} queries")
        print()
    
    # Test deterministic vs non-deterministic
    det_playbooks = registry.get_deterministic_playbooks()
    non_det_playbooks = registry.get_non_deterministic_playbooks()
    
    print(f"✓ Deterministic playbooks: {len(det_playbooks)}")
    for name in det_playbooks:
        print(f"    - {name}")
    
    print(f"\n✗ Non-deterministic playbooks: {len(non_det_playbooks)}")
    for name in non_det_playbooks:
        print(f"    - {name}")
    
    # Test LLM prompt formatting
    print("\n" + "=" * 60)
    print("LLM Prompt Format (truncated):")
    print("=" * 60)
    prompt = registry.get_playbooks_description()
    print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
    
    # Test specific playbook
    print("\n" + "=" * 60)
    print("Detailed Playbook Example: code_analyzer")
    print("=" * 60)
    search_playbook = registry.get_playbook("code_analyzer")
    if search_playbook:
        print(f"Name: {search_playbook.name}")
        print(f"Description: {search_playbook.description}")
        print(f"When to use: {search_playbook.when_to_use}")
        print(f"\nSystem Prompt (snippet):")
        print(f"  {search_playbook.system_prompt[:100]}...")
        print(f"\nSearch Strategy:")
        print(f"  • Queries: {search_playbook.search_strategy.queries}")
        print(f"  • Mode: {search_playbook.search_strategy.mode}")
        print(f"Deterministic: {search_playbook.deterministic}")
    else:
        print("❌ code_analyzer playbook not found!")
    
    print("\n" + "=" * 60)
    print("✅ Playbook system test complete!")
    print("=" * 60)


if __name__ == "__main__":
    test_playbook_loading()
