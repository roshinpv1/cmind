"""
Hash-based change detection fallback.

For non-Git repositories, uses content hashing to detect changes.
"""

from pathlib import Path

from codemind.utils import compute_file_hash

from .file_filters import should_index_directory, should_index_file
from .models import ChangeType, FileChange


class HashDetector:
    """Detects changes using content hashing."""

    def __init__(self, repo_path: Path):
        """
        Initialize hash detector.

        Args:
            repo_path: Path to directory to monitor
        """
        self.repo_path = repo_path

    def detect_changes(
        self, last_hashes: dict[str, str] | None = None
    ) -> tuple[list[FileChange], list[str], dict[str, str]]:
        """
        Detect changes by comparing file hashes.

        Args:
            last_hashes: Previous file hashes {relative_path: hash}

        Returns:
            Tuple of (changed_files, deleted_files, current_hashes)
        """
        changed_files: list[FileChange] = []
        deleted_files: list[str] = []
        current_hashes: dict[str, str] = {}

        if last_hashes is None:
            last_hashes = {}

        # Walk directory tree
        current_files = set()
        for file_path in self._walk_directory(self.repo_path):
            rel_path = file_path.relative_to(self.repo_path)
            rel_path_str = str(rel_path).replace("\\", "/")
            current_files.add(rel_path_str)

            # Compute hash
            try:
                file_hash = compute_file_hash(file_path)
                current_hashes[rel_path_str] = file_hash

                # Check if changed
                if rel_path_str not in last_hashes:
                    # New file
                    change_type = ChangeType.ADDED
                elif last_hashes[rel_path_str] != file_hash:
                    # Modified file
                    change_type = ChangeType.MODIFIED
                else:
                    # Unchanged
                    continue

                # Add to changed files
                changed_files.append(
                    FileChange(
                        path=rel_path_str,
                        change_type=change_type,
                        content_hash=file_hash,
                        size_bytes=file_path.stat().st_size,
                    )
                )
            except (OSError, PermissionError):
                # Skip files we can't read
                pass

        # Detect deleted files
        for old_path in last_hashes.keys():
            if old_path not in current_files:
                deleted_files.append(old_path)

        return changed_files, deleted_files, current_hashes

    def _walk_directory(self, root: Path) -> list[Path]:
        """
        Recursively walk directory and return indexable files.

        Args:
            root: Root directory to walk

        Returns:
            List of file paths to index
        """
        files = []

        for item in root.iterdir():
            if item.is_dir():
                if should_index_directory(item):
                    files.extend(self._walk_directory(item))
            elif item.is_file():
                if should_index_file(item):
                    files.append(item)

        return files
