"""
Playbook-based execution system.

Provides:
- PlaybookRegistry: Loads and manages playbooks from markdown
- PlaybookExecutor: Executes prompt-based playbooks
- PlaybookTools: Universal code search tool
- PlaybookDefinition: Schema for playbook definitions
- SearchStrategy: Schema for search strategies
"""

from .schema import PlaybookDefinition, SearchStrategy
from .registry import PlaybookRegistry
from .executors import PlaybookExecutor
from .tools import PlaybookTools

__all__ = [
    "PlaybookRegistry",
    "PlaybookExecutor", 
    "PlaybookTools",
    "PlaybookDefinition",
    "SearchStrategy"
]
