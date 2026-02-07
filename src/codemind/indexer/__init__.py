"""
Incremental change detection and file loading.

Provides:
- Git-based change detection (diff between commits)
- Content-hash fallback for non-Git repositories
- File state tracking (added, modified, deleted)
- Structured change set output

Implemented in Milestone 1.
"""

from .change_detector import ChangeDetector
from .models import ChangeSet, ChangeType, DetectionMethod, FileChange

__all__ = [
    "ChangeDetector",
    "ChangeSet",
    "ChangeType",
    "DetectionMethod",
    "FileChange",
]
