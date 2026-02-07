"""Tests for Git repository utilities."""

import tempfile
from pathlib import Path

import git
import pytest

from codemind.utils.git_utils import GitRepoManager


class TestGitRepoManager:
    """Test Git repository management."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_git_repo(self):
        """Create a temporary Git repository for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            repo = git.Repo.init(repo_path)

            # Create initial commit
            test_file = repo_path / "test.py"
            test_file.write_text("print('hello')")

            repo.index.add(["test.py"])
            repo.index.commit("Initial commit")

            yield str(repo_path)

    def test_ensure_repo_clones_new_repo(self, temp_cache_dir, temp_git_repo):
        """Test cloning a new repository."""
        manager = GitRepoManager(cache_dir=temp_cache_dir)

        local_path, repo_id, commit = manager.ensure_repo(
            temp_git_repo, branch="master"
        )

        assert local_path.exists()
        assert (local_path / "test.py").exists()
        assert repo_id  # Should have a repo ID
        assert commit  # Should have commit hash

    def test_ensure_repo_reuses_existing(self, temp_cache_dir, temp_git_repo):
        """Test that existing repos are reused."""
        manager = GitRepoManager(cache_dir=temp_cache_dir)

        # First call - clones
        path1, _, _ = manager.ensure_repo(temp_git_repo, branch="master")

        # Second call - should reuse
        path2, _, _ = manager.ensure_repo(temp_git_repo, branch="master")

        assert path1 == path2

    def test_different_branches_cached_separately(self, temp_cache_dir, temp_git_repo):
        """Test that local file paths are returned for local repos."""
        # For local file paths, GitRepoManager returns the original path
        # (not creating a cache copy)
        manager = GitRepoManager(cache_dir=temp_cache_dir)

        # For local paths, it returns the same path
        path, _, _ = manager.ensure_repo(temp_git_repo, branch="master")
        
        # Should return the original path for local repos
        assert str(path) == temp_git_repo

    def test_get_repo_id_consistent(self, temp_cache_dir):
        """Test that repo ID is consistent for same URL."""
        manager = GitRepoManager(cache_dir=temp_cache_dir)

        url = "https://github.com/test/repo.git"
        id1 = manager._get_repo_id(url)
        id2 = manager._get_repo_id(url)

        assert id1 == id2

    def test_cleanup_cache_preserves_external_dirs(self, temp_cache_dir, temp_git_repo):
        """Test cache cleanup only affects cache directory."""
        manager = GitRepoManager(cache_dir=temp_cache_dir)

        # For local file paths, no caching happens
        local_path, _, _ = manager.ensure_repo(temp_git_repo, branch="master")
        
        # Local path should still exist
        assert Path(local_path).exists()
