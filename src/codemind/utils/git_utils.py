"""
Git repository utilities.

Handles cloning and branch management for remote repositories.
"""

import shutil
from pathlib import Path

from git import Repo


class GitRepoManager:
    """Manages Git repository cloning and checkout."""

    def __init__(self, cache_dir: str = "data/repos"):
        """
        Initialize Git repo manager.

        Args:
            cache_dir: Directory to cache cloned repositories
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ensure_repo(self, repo_url: str, branch: str = "main") -> tuple[Path, str, str]:
        """
        Clone or update a repository to cache.

        Args:
            repo_url: Git repository URL or local path
            branch: Branch name to checkout

        Returns:
            Tuple of (local_path, repo_id, current_commit)
        """
        # Check if it's already a local path
        if self._is_local_path(repo_url):
            repo = Repo(repo_url)
            return Path(repo_url), self._get_repo_id(repo_url), repo.head.commit.hexsha

        # It's a remote URL - clone/update it
        repo_name = self._extract_repo_name(repo_url)
        local_path = self.cache_dir / repo_name / branch

        if local_path.exists():
            # Update existing repo
            repo = Repo(local_path)
            repo.remotes.origin.fetch()
            repo.git.checkout(branch)
            repo.remotes.origin.pull()
        else:
            # Clone new repo
            local_path.parent.mkdir(parents=True, exist_ok=True)
            repo = Repo.clone_from(repo_url, local_path, branch=branch, depth=1)

        repo_id = self._get_repo_id(repo_url)
        current_commit = repo.head.commit.hexsha

        return local_path, repo_id, current_commit

    def _is_local_path(self, path: str) -> bool:
        """Check if path is a local directory."""
        return Path(path).exists() and Path(path).is_dir()

    def _extract_repo_name(self, repo_url: str) -> str:
        """Extract repository name from URL."""
        # Handle both HTTPS and SSH URLs
        # https://github.com/user/repo.git -> repo
        # git@github.com:user/repo.git -> repo
        name = repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name

    def _get_repo_id(self, repo_identifier: str) -> str:
        """Generate consistent repo ID from URL or path."""
        import hashlib

        return hashlib.sha256(repo_identifier.encode()).hexdigest()[:16]

    def cleanup_cache(self):
        """Remove all cached repositories."""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
