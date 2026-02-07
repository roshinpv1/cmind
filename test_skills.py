"""
Test script for skill system.

Verifies:
- Skill loading from markdown
- Skill registry functionality
- Skill formatting for LLM
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from codemind.skills import SkillRegistry


def test_skill_loading():
    """Test that skills load correctly from markdown files."""
    print("=" * 60)
    print("Testing Skill System")
    print("=" * 60)
    
    # Initialize registry
    registry = SkillRegistry(skills_dir="skills")
    
    print(f"\n{registry}")
    print(f"Loaded {len(registry)} skills")
    
    # List all skills
    print("\n📋 Available Skills:")
    for skill_name in registry.list_skills():
        skill = registry.get_skill(skill_name)
        print(f"  • {skill_name}")
        print(f"    Description: {skill.description[:60]}...")
        print(f"    Deterministic: {skill.deterministic}")
        print(f"    Inputs: {len(skill.inputs)}")
        print(f"    Outputs: {len(skill.outputs)}")
        print()
    
    # Test deterministic vs non-deterministic
    det_skills = registry.get_deterministic_skills()
    non_det_skills = registry.get_non_deterministic_skills()
    
    print(f"✓ Deterministic skills: {len(det_skills)}")
    for name in det_skills:
        print(f"    - {name}")
    
    print(f"\n✗ Non-deterministic skills: {len(non_det_skills)}")
    for name in non_det_skills:
        print(f"    - {name}")
    
    # Test LLM prompt formatting
    print("\n" + "=" * 60)
    print("LLM Prompt Format (truncated):")
    print("=" * 60)
    prompt = registry.get_skills_description()
    print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
    
    # Test specific skill
    print("\n" + "=" * 60)
    print("Detailed Skill Example: search_codebase")
    print("=" * 60)
    search_skill = registry.get_skill("search_codebase")
    if search_skill:
        print(f"Name: {search_skill.name}")
        print(f"Description: {search_skill.description}")
        print(f"When to use: {search_skill.when_to_use}")
        print(f"\nInputs:")
        for inp in search_skill.inputs:
            default_str = f" (default: {inp.default})" if inp.default else ""
            desc_str = f" - {inp.description}" if inp.description else ""
            print(f"  • {inp.name}: {inp.type}{default_str}{desc_str}")
        print(f"\nOutputs:")
        for out in search_skill.outputs:
            desc_str = f" - {out.description}" if out.description else ""
            print(f"  • {out.name}: {out.type}{desc_str}")
        print(f"\nTools: {', '.join(search_skill.tools)}")
        print(f"Deterministic: {search_skill.deterministic}")
    else:
        print("❌ search_codebase skill not found!")
    
    print("\n" + "=" * 60)
    print("✅ Skill system test complete!")
    print("=" * 60)


if __name__ == "__main__":
    test_skill_loading()
