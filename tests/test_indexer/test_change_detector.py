"""
Comprehensive tests for change detection.

Tests Git-based and hash-based change detection strategies.
"""

from pathlib import Path

import git
import pytest

from codemind.indexer import ChangeDetector, ChangeType, DetectionMethod


class TestGitDetection:
    """Tests for Git-based change detection."""

    @pytest.fixture
    def git_repo(self, tmp_path: Path) -> Path:
        """Create a temporary Git repository."""
        repo_path = tmp_path / "git_repo"
        repo_path.mkdir()

        # Initialize Git repo
        repo = git.Repo.init(repo_path)

        # Create initial file
        (repo_path / "initial.py").write_text("def initial(): pass")
        repo.index.add(["initial.py"])
        repo.index.commit("Initial commit")

        return repo_path

    def test_detect_added_files(self, git_repo: Path):
        """Test detection of newly added files."""
        detector = ChangeDetector(git_repo)

        # Get initial state
        initial_changes = detector.detect_changes()
        initial_commit = initial_changes.commit_hash

        # Add new file
        (git_repo / "new_file.py").write_text("def new(): pass")
        repo = git.Repo(git_repo)
        repo.index.add(["new_file.py"])
        repo.index.commit("Add new file")

        # Detect changes
        changes = detector.detect_changes(last_commit=initial_commit)

        assert changes.detection_method == DetectionMethod.GIT
        assert len(changes.changed_files) == 1
        assert changes.changed_files[0].path == "new_file.py"
        assert changes.changed_files[0].change_type == ChangeType.ADDED
        assert len(changes.deleted_files) == 0

    def test_detect_modified_files(self, git_repo: Path):
        """Test detection of modified files."""
        detector = ChangeDetector(git_repo)

        initial_changes = detector.detect_changes()
        initial_commit = initial_changes.commit_hash

        # Modify existing file
        (git_repo / "initial.py").write_text("def initial(): return 'modified'")
        repo = git.Repo(git_repo)
        repo.index.add(["initial.py"])
        repo.index.commit("Modify file")

        # Detect changes
        changes = detector.detect_changes(last_commit=initial_commit)

        assert len(changes.changed_files) == 1
        assert changes.changed_files[0].path == "initial.py"
        assert changes.changed_files[0].change_type == ChangeType.MODIFIED

    def test_detect_deleted_files(self, git_repo: Path):
        """Test detection of deleted files."""
        detector = ChangeDetector(git_repo)

        initial_changes = detector.detect_changes()
        initial_commit = initial_changes.commit_hash

        # Delete file
        (git_repo / "initial.py").unlink()
        repo = git.Repo(git_repo)
        repo.index.remove(["initial.py"])
        repo.index.commit("Delete file")

        # Detect changes
        changes = detector.detect_changes(last_commit=initial_commit)

        assert len(changes.deleted_files) == 1
        assert "initial.py" in changes.deleted_files
        assert len(changes.changed_files) == 0

    def test_no_changes_produces_empty_result(self, git_repo: Path):
        """Test that running twice without changes produces empty result."""
        detector = ChangeDetector(git_repo)

        changes1 = detector.detect_changes()
        commit_hash = changes1.commit_hash

        # No changes made
        changes2 = detector.detect_changes(last_commit=commit_hash)

        assert changes2.is_empty()
        assert len(changes2.changed_files) == 0
        assert len(changes2.deleted_files) == 0

    def test_multiple_changes_in_commit(self, git_repo: Path):
        """Test detection of multiple changes in single commit."""
        detector = ChangeDetector(git_repo)

        initial_changes = detector.detect_changes()
        initial_commit = initial_changes.commit_hash

        # Make multiple changes
        (git_repo / "file1.py").write_text("def one(): pass")
        (git_repo / "file2.py").write_text("def two(): pass")
        (git_repo / "initial.py").write_text("def initial(): return 'updated'")

        repo = git.Repo(git_repo)
        repo.index.add(["file1.py", "file2.py", "initial.py"])
        repo.index.commit("Multiple changes")

        changes = detector.detect_changes(last_commit=initial_commit)

        assert len(changes.changed_files) == 3
        change_paths = {c.path for c in changes.changed_files}
        assert change_paths == {"file1.py", "file2.py", "initial.py"}


class TestHashDetection:
    """Tests for hash-based change detection."""

    @pytest.fixture
    def non_git_dir(self, tmp_path: Path) -> Path:
        """Create a temporary non-Git directory."""
        dir_path = tmp_path / "non_git"
        dir_path.mkdir()
        (dir_path / "file1.py").write_text("def one(): pass")
        return dir_path

    def test_fallback_to_hash_for_non_git(self, non_git_dir: Path):
        """Test that non-Git repos use hash detection."""
        detector = ChangeDetector(non_git_dir)
        changes = detector.detect_changes()

        assert changes.detection_method == DetectionMethod.HASH
        assert len(changes.changed_files) == 1

    def test_first_run_all_files_added(self, non_git_dir: Path):
        """Test that first run treats all files as added."""
        detector = ChangeDetector(non_git_dir)
        changes = detector.detect_changes()

        assert len(changes.changed_files) == 1
        assert changes.changed_files[0].change_type == ChangeType.ADDED
        assert changes.changed_files[0].path == "file1.py"

    def test_incremental_detection(self, non_git_dir: Path):
        """Test incremental hash-based detection."""
        detector = ChangeDetector(non_git_dir)

        # First run
        changes1 = detector.detect_changes()
        hashes1 = {c.path: c.content_hash for c in changes1.changed_files}

        # Add new file
        (non_git_dir / "file2.py").write_text("def two(): pass")

        # Second run
        changes2 = detector.detect_changes(last_hashes=hashes1)

        assert len(changes2.changed_files) == 1
        assert changes2.changed_files[0].path == "file2.py"
        assert changes2.changed_files[0].change_type == ChangeType.ADDED

    def test_deleted_file_detection(self, non_git_dir: Path):
        """Test detection of deleted files with hash method."""
        detector = ChangeDetector(non_git_dir)

        # First run
        changes1 = detector.detect_changes()
        hashes1 = {c.path: c.content_hash for c in changes1.changed_files}

        # Delete file
        (non_git_dir / "file1.py").unlink()

        # Second run
        changes2 = detector.detect_changes(last_hashes=hashes1)

        assert len(changes2.deleted_files) == 1
        assert "file1.py" in changes2.deleted_files

    def test_hash_no_changes_empty_result(self, non_git_dir: Path):
        """Test that hash detection returns empty result when no changes."""
        detector = ChangeDetector(non_git_dir)

        changes1 = detector.detect_changes()
        hashes1 = {c.path: c.content_hash for c in changes1.changed_files}

        # No changes made
        changes2 = detector.detect_changes(last_hashes=hashes1)

        assert changes2.is_empty()


class TestFileFiltering:
    """Tests for file filtering logic."""

    def test_ignores_binary_files(self, tmp_path: Path):
        """Test that binary files are ignored."""
        (tmp_path / "test.pyc").write_bytes(b"\x00\x01\x02")
        (tmp_path / "test.py").write_text("def test(): pass")

        detector = ChangeDetector(tmp_path)
        changes = detector.detect_changes()

        # Should only detect .py file
        assert len(changes.changed_files) == 1
        assert changes.changed_files[0].path == "test.py"

    def test_ignores_non_code_files(self, tmp_path: Path):
        """Test that non-code files are ignored."""
        (tmp_path / "image.png").write_bytes(b"fake image")
        (tmp_path / "doc.pdf").write_bytes(b"fake pdf")
        (tmp_path / "script.py").write_text("print('hello')")

        detector = ChangeDetector(tmp_path)
        changes = detector.detect_changes()

        assert len(changes.changed_files) == 1
        assert changes.changed_files[0].path == "script.py"

    def test_ignores_build_directories(self, tmp_path: Path):
        """Test that build directories are ignored."""
        build_dir = tmp_path / "__pycache__"
        build_dir.mkdir()
        (build_dir / "cached.pyc").write_bytes(b"\x00")

        (tmp_path / "main.py").write_text("def main(): pass")

        detector = ChangeDetector(tmp_path)
        changes = detector.detect_changes()

        assert len(changes.changed_files) == 1
        assert changes.changed_files[0].path == "main.py"


class TestDeterminism:
    """Tests for deterministic behavior."""

    def test_hash_stability(self, tmp_path: Path):
        """Test that same content produces same hash."""
        file_path = tmp_path / "test.py"
        content = "def test(): pass\n"

        # Create file
        file_path.write_text(content)

        detector = ChangeDetector(tmp_path)
        changes1 = detector.detect_changes()
        hash1 = changes1.changed_files[0].content_hash

        # Delete and recreate with same content
        file_path.unlink()
        file_path.write_text(content)

        changes2 = detector.detect_changes()
        hash2 = changes2.changed_files[0].content_hash

        assert hash1 == hash2

    def test_modified_file_detected_reliably(self, tmp_path: Path):
        """Test that modified files are always detected."""
        file_path = tmp_path / "test.py"
        file_path.write_text("original")

        detector = ChangeDetector(tmp_path)
        changes1 = detector.detect_changes()
        hashes1 = {c.path: c.content_hash for c in changes1.changed_files}

        # Modify file
        file_path.write_text("modified")

        changes2 = detector.detect_changes(last_hashes=hashes1)

        assert len(changes2.changed_files) == 1
        assert changes2.changed_files[0].change_type == ChangeType.MODIFIED
        assert changes2.changed_files[0].content_hash != hashes1["test.py"]
