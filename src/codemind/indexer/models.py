"""
Data models for change detection.

Defines structured output formats for incremental change detection.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ChangeType(str, Enum):
    """Type of file change detected."""

    ADDED = "added"
    MODIFIED = "modified"
    RENAMED = "renamed"


class DetectionMethod(str, Enum):
    """Method used for change detection."""

    GIT = "git"
    HASH = "hash"


class FileChange(BaseModel):
    """Represents a single file change."""

    model_config = ConfigDict(use_enum_values=True)

    path: str = Field(..., description="Relative path to the changed file")
    change_type: ChangeType = Field(..., description="Type of change (added/modified/renamed)")
    content_hash: str = Field(..., description="SHA-256 hash of file content")
    size_bytes: int = Field(..., description="File size in bytes")
    old_path: str | None = Field(None, description="Previous path if renamed")
    changed_lines: list[tuple[int, int]] | None = Field(
        None, description="Changed line ranges (start, end) for partial re-indexing"
    )


class ChangeSet(BaseModel):
    """
    Structured output from change detection.

    Contains all detected changes with metadata about how they were detected.
    """

    model_config = ConfigDict(use_enum_values=True)

    changed_files: list[FileChange] = Field(
        default_factory=list, description="Files that were added or modified"
    )
    deleted_files: list[str] = Field(default_factory=list, description="Files that were deleted")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When changes were detected"
    )
    detection_method: DetectionMethod = Field(
        ..., description="Method used for detection (git/hash)"
    )
    commit_hash: str | None = Field(None, description="Git commit hash if using git detection")

    def is_empty(self) -> bool:
        """Check if changeset contains any changes."""
        return len(self.changed_files) == 0 and len(self.deleted_files) == 0
