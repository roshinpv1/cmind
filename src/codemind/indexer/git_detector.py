"""
Git-based change detection.

Uses GitPython to detect changes between commits.
"""

from pathlib import Path

import git
from git.exc import GitCommandError, InvalidGitRepositoryError

from codemind.utils import compute_file_hash

from .file_filters import should_index_file
from .models import ChangeType, FileChange


class GitDetector:
    """Detects changes using Git history."""

    def __init__(self, repo_path: Path):
        """
        Initialize Git detector.

        Args:
            repo_path: Path to Git repository

        Raises:
            InvalidGitRepositoryError: If path is not a Git repo
        """
        self.repo_path = repo_path
        try:
            self.repo = git.Repo(repo_path)
        except InvalidGitRepositoryError as e:
            raise InvalidGitRepositoryError(f"Not a valid Git repository: {repo_path}") from e

    @staticmethod
    def is_git_repo(path: Path) -> bool:
        """Check if path is a Git repository."""
        try:
            git.Repo(path)
            return True
        except InvalidGitRepositoryError:
            return False

    def get_current_commit(self) -> str:
        """Get current HEAD commit hash."""
        return self.repo.head.commit.hexsha

    def detect_changes(self, last_commit: str | None = None) -> tuple[list[FileChange], list[str]]:
        """
        Detect changes between last_commit and HEAD.

        Args:
            last_commit: Previous commit hash. If None, uses initial commit.

        Returns:
            Tuple of (changed_files, deleted_files)
        """
        changed_files: list[FileChange] = []
        deleted_files: list[str] = []

        # Get commit range
        if last_commit is None:
            # First time indexing - get all files
            return self._get_all_tracked_files()

        # Compare commits
        try:
            old_commit = self.repo.commit(last_commit)
            new_commit = self.repo.head.commit
        except (GitCommandError, ValueError) as e:
            raise ValueError(f"Invalid commit hash: {last_commit}") from e

        # Get diff between commits
        diff = old_commit.diff(new_commit)

        # Process changes
        for change in diff:
            # Skip if not a file we should index
            file_path = self.repo_path / change.a_path if change.a_path else None

            if change.change_type == "D":
                # Deleted file
                if change.a_path:
                    deleted_files.append(change.a_path)

            elif change.change_type in ("A", "M"):
                # Added or modified file
                if file_path and should_index_file(file_path):
                    try:
                        changed_files.append(
                            FileChange(
                                path=change.a_path or change.b_path,
                                change_type=(
                                    ChangeType.ADDED
                                    if change.change_type == "A"
                                    else ChangeType.MODIFIED
                                ),
                                content_hash=compute_file_hash(file_path),
                                size_bytes=file_path.stat().st_size,
                            )
                        )
                    except (OSError, PermissionError):
                        # Skip files we can't read
                        pass

        return changed_files, deleted_files

    def _get_all_tracked_files(self) -> tuple[list[FileChange], list[str]]:
        """Get all tracked files in the repository (for initial index)."""
        changed_files: list[FileChange] = []

        # Get all tracked files
        tracked_files = [item.path for item in self.repo.head.commit.tree.traverse()]

        for rel_path in tracked_files:
            file_path = self.repo_path / rel_path

            if should_index_file(file_path):
                try:
                    changed_files.append(
                        FileChange(
                            path=rel_path,
                            change_type=ChangeType.ADDED,
                            content_hash=compute_file_hash(file_path),
                            size_bytes=file_path.stat().st_size,
                        )
                    )
                except (OSError, PermissionError):
                    # Skip files we can't read
                    pass

        return changed_files, []
