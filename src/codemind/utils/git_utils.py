"""
Git repository utilities.

Handles cloning and branch management for remote repositories.
"""

import shutil
from pathlib import Path
from datetime import datetime, UTC

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

    def ensure_repo(self, repo_url: str, branch: str = "main", token: str | None = None) -> tuple[Path, str, str]:
        """
        Clone or update a repository to cache.

        Args:
            repo_url: Git repository URL or local path
            branch: Branch name to checkout
            token: Optional Git access token for private repos

        Returns:
            Tuple of (local_path, repo_id, current_commit)
        """
        # Check if it's already a local path
        if self._is_local_path(repo_url):
            repo = Repo(repo_url)
            return Path(repo_url), self._get_repo_id(repo_url), repo.head.commit.hexsha

        # Inject token into URL if provided
        final_url = repo_url
        if token and "github.com" in repo_url and "@" not in repo_url:
            # Handle standard HTTPS GitHub URLs: https://github.com/user/repo.git
            # Convert to: https://TOKEN@github.com/user/repo.git
            if repo_url.startswith("https://"):
                final_url = repo_url.replace("https://", f"https://{token}@", 1)
        
        # It's a remote URL - clone/update it
        repo_name = self._extract_repo_name(repo_url)
        local_path = self.cache_dir / repo_name / branch

        if local_path.exists():
            # Update existing repo
            repo = Repo(local_path)
            origin = repo.remotes.origin
            
            # Update remote URL with token if needed (in case token changed)
            if token and final_url != origin.url:
                origin.set_url(final_url)
            
            origin.fetch()
            
            # Checkout branch using object API
            if branch in repo.heads:
                repo.heads[branch].checkout()
            else:
                 # Create local branch from remote
                repo.create_head(branch, origin.refs[branch]).set_tracking_branch(origin.refs[branch]).checkout()
            
            origin.pull()
        else:
            # Clone new repo
            local_path.parent.mkdir(parents=True, exist_ok=True)
            repo = Repo.clone_from(final_url, local_path, branch=branch, depth=1)

        repo_id = self._get_repo_id(repo_url) # Use original URL for ID stability
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
            
    def extract_metadata(self, repo_path: Path) -> dict:
        """
        Extract repository metadata (commit stats, authors).
        
        Args:
            repo_path: Path to local git repository
            
        Returns:
            Dictionary with metadata fields
        """
        try:
            repo = Repo(repo_path)
            if repo.head.is_valid():
                # 1. First commit (approximate creation)
                # Iterate in reverse to find first commit
                # efficient for small repos, but for huge ones 'rev-list --max-parents=0 HEAD' is better
                first_commit_sha = repo.git.rev_list("--max-parents=0", "HEAD").splitlines()[0]
                first_commit = repo.commit(first_commit_sha)
                
                first_commit_at = datetime.fromtimestamp(first_commit.committed_date, UTC)
                first_author = first_commit.author.name
                
                # 2. Last 4 authors
                last_authors = []
                seen_authors = set()
                # Scan last 50 commits to find 4 unique authors
                for commit in repo.iter_commits("HEAD", max_count=50):
                    author = commit.author.name
                    if author not in seen_authors:
                        last_authors.append(author)
                        seen_authors.add(author)
                        if len(last_authors) >= 4:
                            break
                            
                # 3. Total commits
                # 'git rev-list --count HEAD' is highly optimized
                total_commits = int(repo.git.rev_list("--count", "HEAD"))
                
                return {
                    "first_commit_at": first_commit_at,
                    "first_author": first_author,
                    "last_authors": last_authors,  # List of strings
                    "total_commits": total_commits
                }
            return {}
        except Exception as e:
            print(f"[GIT] Metadata extraction failed: {e}")
            return {}
