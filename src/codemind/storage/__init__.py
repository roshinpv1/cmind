"""
Persistent storage layer for CodeMind.

Provides:
- LanceDB append-only vector storage (Milestone 6)
- Manifest persistence via SQLite (Milestone 2)
- Embedding versioning
- Chunk metadata management
- Atomic state updates

Implemented in Milestones 2 and 6.
"""

from .manifest_manager import ManifestManager
from .models import RepositoryManifest

__all__ = ["ManifestManager", "RepositoryManifest"]
