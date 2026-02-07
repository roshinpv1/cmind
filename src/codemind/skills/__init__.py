"""
Skill-based execution system.

Provides:
- SkillRegistry: Loads and manages skills from markdown
- SkillExecutor: Executes prompt-based skills
- SkillTools: Universal code search tool
- SkillDefinition: Schema for skill definitions
- SearchStrategy: Schema for search strategies
"""

from .schema import SkillDefinition, SearchStrategy
from .registry import SkillRegistry
from .executors import SkillExecutor
from .tools import SkillTools

__all__ = [
    "SkillRegistry",
    "SkillExecutor", 
    "SkillTools",
    "SkillDefinition",
    "SearchStrategy"
]
