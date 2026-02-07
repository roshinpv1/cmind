"""
Skill Registry - Auto-discover and manage skills from Markdown files.

The registry:
- Loads skills from the skills/ directory
- Provides access to skill definitions
- Supports hot-reloading
- Formats skills for LLM prompts
"""

from pathlib import Path
from typing import Dict, Optional, List
from .schema import SkillDefinition


class SkillRegistry:
    """
    Central registry for all available skills.
    
    Skills are auto-discovered from Markdown files in the skills directory.
    """
    
    def __init__(self, skills_dir: Path | str = "skills"):
        """
        Initialize skill registry.
        
        Args:
            skills_dir: Directory containing skill MD files
        """
        self.skills_dir = Path(skills_dir)
        self.skills: Dict[str, SkillDefinition] = {}
        self._load_skills()
    
    def _load_skills(self) -> None:
        """Load all skills from markdown files."""
        from .parsers import parse_skill_markdown
        
        # Create directory if it doesn't exist
        if not self.skills_dir.exists():
            print(f"[SKILLS] Creating skills directory: {self.skills_dir}")
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            return
        
        # Load each .md file
        skill_files = list(self.skills_dir.glob("*.md"))
        
        if not skill_files:
            print(f"[SKILLS] No skill files found in {self.skills_dir}")
            return
        
        loaded = 0
        failed = 0
        
        for skill_file in skill_files:
            try:
                skill = parse_skill_markdown(skill_file)
                if skill:
                    self.skills[skill.name] = skill
                    print(f"[SKILLS] ✓ Loaded: {skill.name}")
                    loaded += 1
                else:
                    print(f"[SKILLS] ✗ Failed to parse {skill_file.name}")
                    failed += 1
            except Exception as e:
                print(f"[SKILLS] ✗ Failed to load {skill_file.name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
        
        print(f"[SKILLS] Registry initialized: {loaded} skills loaded, {failed} failed")
    
    def get_skill(self, name: str) -> Optional[SkillDefinition]:
        """
        Get skill definition by name.
        
        Args:
            name: Skill identifier
            
        Returns:
            SkillDefinition if found, None otherwise
        """
        return self.skills.get(name)
    
    def list_skills(self) -> List[str]:
        """
        Get list of all available skill names.
        
        Returns:
            List of skill names sorted alphabetically
        """
        return sorted(self.skills.keys())
    
    def get_skills_description(self) -> str:
        """
        Format all skills for LLM prompt.
        
        Returns:
            Formatted string describing all skills
        """
        if not self.skills:
            return "No skills available."
        
        descriptions = []
        for name in sorted(self.skills.keys()):
            skill = self.skills[name]
            descriptions.append(skill.to_prompt_description())
        
        return "\n\n---\n\n".join(descriptions)
    
    def get_deterministic_skills(self) -> List[str]:
        """
        Get list of deterministic skills.
        
        Returns:
            List of deterministic skill names
        """
        return [
            name for name, skill in self.skills.items()
            if skill.deterministic
        ]
    
    def get_non_deterministic_skills(self) -> List[str]:
        """
        Get list of non-deterministic (LLM-based) skills.
        
        Returns:
            List of non-deterministic skill names
        """
        return [
            name for name, skill in self.skills.items()
            if not skill.deterministic
        ]
    
    def reload(self) -> None:
        """
        Reload all skills from disk.
        
        Useful for hot-reloading during development.
        """
        print("[SKILLS] Reloading skills...")
        self.skills.clear()
        self._load_skills()
    
    def __len__(self) -> int:
        """Number of loaded skills."""
        return len(self.skills)
    
    def __contains__(self, name: str) -> bool:
        """Check if skill exists."""
        return name in self.skills
    
    def __repr__(self) -> str:
        return f"SkillRegistry({len(self.skills)} skills loaded from {self.skills_dir})"
