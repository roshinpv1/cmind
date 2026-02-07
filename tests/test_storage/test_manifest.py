"""
Tests for manifest persistence.

Tests repository and file manifest CRUD operations.
"""

from pathlib import Path

import pytest

from codemind.indexer.models import ChangeType, FileChange
from codemind.storage import ManifestManager


class TestRepositoryManifest:
    """Tests for repository manifest operations."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> ManifestManager:
        """Create temporary manifest manager."""
        db_path = tmp_path / "test.db"
        return ManifestManager(db_path)

    @pytest.fixture
    def test_repo_path(self, tmp_path: Path) -> Path:
        """Create test repository path."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        return repo

    def test_create_repository(self, manager: ManifestManager, test_repo_path: Path):
        """Test creating a repository manifest."""
        repo = manager.create_repository(str(test_repo_path))

        assert repo is not None
        assert repo.repo_path == str(test_repo_path.resolve())
        assert repo.repo_id is not None
        assert repo.total_files_indexed == 0
        assert repo.embedding_model == "all-MiniLM-L6-v2"
        assert repo.embedding_version == 1

    def test_get_repository_by_path(self, manager: ManifestManager, test_repo_path: Path):
        """Test retrieving repository by path."""
        created = manager.create_repository(str(test_repo_path))
        retrieved = manager.get_repository(str(test_repo_path))

        assert retrieved is not None
        assert retrieved.repo_id == created.repo_id
        assert retrieved.repo_path == created.repo_path

    def test_get_repository_by_id(self, manager: ManifestManager, test_repo_path: Path):
        """Test retrieving repository by ID."""
        created = manager.create_repository(str(test_repo_path))
        retrieved = manager.get_repository_by_id(created.repo_id)

        assert retrieved is not None
        assert retrieved.repo_id == created.repo_id

    def test_get_nonexistent_repository(self, manager: ManifestManager):
        """Test retrieving nonexistent repository returns None."""
        result = manager.get_repository("/nonexistent/path")
        assert result is None

    def test_update_repository(self, manager: ManifestManager, test_repo_path: Path):
        """Test updating repository manifest."""
        repo = manager.create_repository(str(test_repo_path))
        repo_id = repo.repo_id

        updated = manager.update_repository(
            repo_id, last_commit_hash="abc123", total_files=10, embedding_version=2
        )

        assert updated is not None
        assert updated.last_commit_hash == "abc123"
        assert updated.total_files_indexed == 10
        assert updated.embedding_version == 2

    def test_delete_repository(self, manager: ManifestManager, test_repo_path: Path):
        """Test deleting repository manifest."""
        repo = manager.create_repository(str(test_repo_path))
        repo_id = repo.repo_id

        # Delete
        result = manager.delete_repository(repo_id)
        assert result is True

        # Verify deleted
        retrieved = manager.get_repository_by_id(repo_id)
        assert retrieved is None

    def test_delete_nonexistent_repository(self, manager: ManifestManager):
        """Test deleting nonexistent repository returns False."""
        result = manager.delete_repository("nonexistent_id")
        assert result is False


class TestFileManifest:
    """Tests for file manifest operations."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> ManifestManager:
        """Create temporary manifest manager."""
        db_path = tmp_path / "test.db"
        return ManifestManager(db_path)

    @pytest.fixture
    def repo_id(self, manager: ManifestManager, tmp_path: Path) -> str:
        """Create test repository and return ID."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        repo = manager.create_repository(str(repo_path))
        return repo.repo_id

    def test_update_changed_files(self, manager: ManifestManager, repo_id: str):
        """Test updating changed files."""
        changed_files = [
            FileChange(
                path="file1.py",
                change_type=ChangeType.ADDED,
                content_hash="hash1",
                size_bytes=100,
            ),
            FileChange(
                path="file2.py",
                change_type=ChangeType.ADDED,
                content_hash="hash2",
                size_bytes=200,
            ),
        ]

        manager.update_files(repo_id, changed_files, [])

        # Verify files were created
        hashes = manager.get_file_hashes(repo_id)
        assert len(hashes) == 2
        assert hashes["file1.py"] == "hash1"
        assert hashes["file2.py"] == "hash2"

    def test_update_modified_file(self, manager: ManifestManager, repo_id: str):
        """Test updating a modified file."""
        # Add initial file
        initial = [
            FileChange(
                path="file1.py",
                change_type=ChangeType.ADDED,
                content_hash="hash1",
                size_bytes=100,
            )
        ]
        manager.update_files(repo_id, initial, [])

        # Modify file
        modified = [
            FileChange(
                path="file1.py",
                change_type=ChangeType.MODIFIED,
                content_hash="hash1_modified",
                size_bytes=150,
            )
        ]
        manager.update_files(repo_id, modified, [])

        # Verify hash updated
        hashes = manager.get_file_hashes(repo_id)
        assert hashes["file1.py"] == "hash1_modified"

    def test_mark_deleted_files(self, manager: ManifestManager, repo_id: str):
        """Test marking files as deleted."""
        # Add files
        files = [
            FileChange(
                path="file1.py",
                change_type=ChangeType.ADDED,
                content_hash="hash1",
                size_bytes=100,
            ),
            FileChange(
                path="file2.py",
                change_type=ChangeType.ADDED,
                content_hash="hash2",
                size_bytes=200,
            ),
        ]
        manager.update_files(repo_id, files, [])

        # Delete file1
        manager.update_files(repo_id, [], ["file1.py"])

        # Verify only file2 is active
        hashes = manager.get_file_hashes(repo_id)
        assert len(hashes) == 1
        assert "file2.py" in hashes
        assert "file1.py" not in hashes

    def test_get_file_hashes_empty(self, manager: ManifestManager, repo_id: str):
        """Test getting file hashes for repository with no files."""
        hashes = manager.get_file_hashes(repo_id)
        assert hashes == {}


class TestPersistence:
    """Tests for persistence across sessions."""

    def test_state_persists_across_sessions(self, tmp_path: Path):
        """Test that state is preserved when creating new manager instance."""
        db_path = tmp_path / "persistent.db"
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        # First session - create repository
        manager1 = ManifestManager(db_path)
        repo1 = manager1.create_repository(str(repo_path))
        repo_id = repo1.repo_id

        files = [
            FileChange(
                path="file1.py",
                change_type=ChangeType.ADDED,
                content_hash="hash1",
                size_bytes=100,
            )
        ]
        manager1.update_files(repo_id, files, [])
        manager1.update_repository(repo_id, last_commit_hash="commit123")

        # Second session - retrieve state
        manager2 = ManifestManager(db_path)
        repo2 = manager2.get_repository(str(repo_path))

        assert repo2 is not None
        assert repo2.repo_id == repo_id
        assert repo2.last_commit_hash == "commit123"
        assert repo2.total_files_indexed == 1

        hashes = manager2.get_file_hashes(repo_id)
        assert hashes["file1.py"] == "hash1"


class TestIntegration:
    """Integration tests with change detection."""

    def test_manifest_drives_incremental_detection(self, tmp_path: Path):
        """Test that manifest state drives incremental detection."""
        from codemind.indexer import ChangeDetector

        db_path = tmp_path / "test.db"
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        # Create test files
        (repo_path / "file1.py").write_text("def test(): pass")

        manager = ManifestManager(db_path)
        detector = ChangeDetector(repo_path)

        # First detection - no manifest
        changes1 = detector.detect_changes()
        assert len(changes1.changed_files) == 1

        # Create manifest
        repo = manager.create_repository(str(repo_path))
        manager.update_files(repo.repo_id, changes1.changed_files, [])

        # Get persisted hashes
        hashes = manager.get_file_hashes(repo.repo_id)

        # Second detection - with manifest (no changes)
        changes2 = detector.detect_changes(last_hashes=hashes)
        assert changes2.is_empty()

        # Modify file
        (repo_path / "file1.py").write_text("def test(): return 42")

        # Third detection - should detect change
        changes3 = detector.detect_changes(last_hashes=hashes)
        assert len(changes3.changed_files) == 1
        assert changes3.changed_files[0].change_type == ChangeType.MODIFIED
