"""
Git-based change detection using pygit2.

Uses pygit2 (libgit2) for native diff detection with rename tracking
and line-range extraction for partial re-indexing.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pygit2

from codemind.utils import compute_file_hash

from .file_filters import should_index_file
from .models import ChangeType, FileChange


@dataclass
class LineDiff:
    """Changed line ranges within a file for partial re-chunking."""

    added_ranges: list[tuple[int, int]] = field(default_factory=list)
    deleted_ranges: list[tuple[int, int]] = field(default_factory=list)


class GitDetector:
    """Detects changes using Git history via pygit2."""

    def __init__(self, repo_path: Path):
        """
        Initialize Git detector.

        Args:
            repo_path: Path to Git repository

        Raises:
            pygit2.GitError: If path is not a Git repo
        """
        self.repo_path = Path(repo_path)
        discovered = pygit2.discover_repository(str(self.repo_path))
        if not discovered:
            raise pygit2.GitError(f"Not a valid Git repository: {repo_path}")
        self.repo = pygit2.Repository(discovered)

    @staticmethod
    def is_git_repo(path: Path) -> bool:
        """Check if path is a Git repository."""
        try:
            return pygit2.discover_repository(str(path)) is not None
        except Exception:
            return False

    def get_current_commit(self) -> str:
        """Get current HEAD commit hash."""
        return str(self.repo.head.target)

    def detect_changes(
        self, last_commit: str | None = None
    ) -> tuple[list[FileChange], list[str]]:
        """
        Detect changes between last_commit and HEAD.

        Uses pygit2 diff with rename detection enabled.

        Args:
            last_commit: Previous commit hash. If None, returns all tracked files.

        Returns:
            Tuple of (changed_files, deleted_files)
        """
        if last_commit is None:
            return self._get_all_tracked_files()

        changed_files: list[FileChange] = []
        deleted_files: list[str] = []

        try:
            old_oid = pygit2.Oid(hex=last_commit)
            old_commit = self.repo.get(old_oid)
            new_commit = self.repo.head.peel(pygit2.Commit)
        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid commit hash: {last_commit}") from e

        # Generate diff
        diff = self.repo.diff(old_commit.tree, new_commit.tree)
        # Enable rename/copy detection on the diff
        diff.find_similar(
            flags=(
                pygit2.GIT_DIFF_FIND_RENAMES
                | pygit2.GIT_DIFF_FIND_COPIES
            ),
        )

        for patch in diff:
            delta = patch.delta
            status = delta.status
            old_path = delta.old_file.path
            new_path = delta.new_file.path

            if status == pygit2.GIT_DELTA_DELETED:
                if old_path:
                    deleted_files.append(old_path)

            elif status in (pygit2.GIT_DELTA_ADDED, pygit2.GIT_DELTA_MODIFIED):
                file_path = self.repo_path / new_path
                if file_path.exists() and should_index_file(file_path):
                    try:
                        # Extract changed line ranges from hunks
                        changed_lines = self._extract_line_ranges(patch)

                        changed_files.append(
                            FileChange(
                                path=new_path,
                                change_type=(
                                    ChangeType.ADDED
                                    if status == pygit2.GIT_DELTA_ADDED
                                    else ChangeType.MODIFIED
                                ),
                                content_hash=compute_file_hash(file_path),
                                size_bytes=file_path.stat().st_size,
                                changed_lines=changed_lines,
                            )
                        )
                    except (OSError, PermissionError):
                        pass

            elif status == pygit2.GIT_DELTA_RENAMED:
                # Renamed file — track old → new
                file_path = self.repo_path / new_path
                if file_path.exists() and should_index_file(file_path):
                    try:
                        changed_lines = self._extract_line_ranges(patch)
                        changed_files.append(
                            FileChange(
                                path=new_path,
                                change_type=ChangeType.RENAMED,
                                content_hash=compute_file_hash(file_path),
                                size_bytes=file_path.stat().st_size,
                                old_path=old_path,
                                changed_lines=changed_lines,
                            )
                        )
                    except (OSError, PermissionError):
                        pass

                # Mark old path for cleanup
                if old_path:
                    deleted_files.append(old_path)

            elif status == pygit2.GIT_DELTA_COPIED:
                file_path = self.repo_path / new_path
                if file_path.exists() and should_index_file(file_path):
                    try:
                        changed_files.append(
                            FileChange(
                                path=new_path,
                                change_type=ChangeType.ADDED,
                                content_hash=compute_file_hash(file_path),
                                size_bytes=file_path.stat().st_size,
                            )
                        )
                    except (OSError, PermissionError):
                        pass

        return changed_files, deleted_files

    def _extract_line_ranges(self, patch) -> list[tuple[int, int]] | None:
        """Extract changed line ranges from a diff patch.

        Returns list of (start_line, end_line) tuples for added/modified lines,
        or None if extraction fails.
        """
        try:
            ranges = []
            for hunk in patch.hunks:
                # hunk.new_start is 1-indexed, hunk.new_lines is count
                if hunk.new_lines > 0:
                    ranges.append((hunk.new_start, hunk.new_start + hunk.new_lines - 1))
            return ranges if ranges else None
        except Exception:
            return None

    def _get_all_tracked_files(self) -> tuple[list[FileChange], list[str]]:
        """Get all tracked files in the repository (for initial index)."""
        changed_files: list[FileChange] = []

        head = self.repo.head.peel(pygit2.Commit)
        tree = head.tree

        def _walk_tree(tree, prefix=""):
            for entry in tree:
                full_path = f"{prefix}/{entry.name}" if prefix else entry.name
                if entry.type_str == "tree":
                    subtree = self.repo.get(entry.id)
                    _walk_tree(subtree, full_path)
                elif entry.type_str == "blob":
                    file_path = self.repo_path / full_path
                    if should_index_file(file_path):
                        try:
                            if file_path.exists():
                                changed_files.append(
                                    FileChange(
                                        path=full_path,
                                        change_type=ChangeType.ADDED,
                                        content_hash=compute_file_hash(file_path),
                                        size_bytes=file_path.stat().st_size,
                                    )
                                )
                        except (OSError, PermissionError):
                            pass

        _walk_tree(tree)
        return changed_files, []
