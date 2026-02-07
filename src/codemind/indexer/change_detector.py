"""
Main change detection orchestrator.

Auto-selects Git or hash-based detection and returns structured ChangeSet.
"""

from pathlib import Path

from .git_detector import GitDetector
from .hash_detector import HashDetector
from .models import ChangeSet, DetectionMethod


class ChangeDetector:
    """
    Main interface for detecting repository changes.

    Automatically selects Git-based or hash-based detection.
    """

    def __init__(self, repo_path: str | Path):
        """
        Initialize change detector.

        Args:
            repo_path: Path to repository
        """
        self.repo_path = Path(repo_path).resolve()

        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")

        if not self.repo_path.is_dir():
            raise ValueError(f"Repository path is not a directory: {repo_path}")

    def detect_changes(
        self,
        last_commit: str | None = None,
        last_hashes: dict[str, str] | None = None,
    ) -> ChangeSet:
        """
        Detect changes in repository.

        Args:
            last_commit: Previous Git commit hash (for Git repos)
            last_hashes: Previous file hashes (for non-Git repos)

        Returns:
            ChangeSet with detected changes
        """
        # Check if Git repository
        if GitDetector.is_git_repo(self.repo_path):
            return self._detect_with_git(last_commit)
        else:
            return self._detect_with_hash(last_hashes)

    def _detect_with_git(self, last_commit: str | None = None) -> ChangeSet:
        """Detect changes using Git."""
        detector = GitDetector(self.repo_path)
        changed_files, deleted_files = detector.detect_changes(last_commit)

        return ChangeSet(
            changed_files=changed_files,
            deleted_files=deleted_files,
            detection_method=DetectionMethod.GIT,
            commit_hash=detector.get_current_commit(),
        )

    def _detect_with_hash(self, last_hashes: dict[str, str] | None = None) -> ChangeSet:
        """Detect changes using content hashing."""
        detector = HashDetector(self.repo_path)
        changed_files, deleted_files, current_hashes = detector.detect_changes(last_hashes)

        return ChangeSet(
            changed_files=changed_files,
            deleted_files=deleted_files,
            detection_method=DetectionMethod.HASH,
        )
