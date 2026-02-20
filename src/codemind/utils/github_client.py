
"""
GitHub API Client Wrapper.

Provides simplified access to GitHub repository metadata using PyGithub.
"""

import os
from github import Github, Auth
from typing import Dict, Any, Optional

class GitHubClient:
    """Wrapper for GitHub API operations."""
    
    def __init__(self, token: Optional[str] = None):
        """
        Initialize GitHub client.
        
        Args:
            token: GitHub access token. If None, tries to read GIT_ACCESS_TOKEN env var.
        """
        self.token = token or os.environ.get("GIT_ACCESS_TOKEN")
        
        if self.token:
            auth = Auth.Token(self.token)
            self.github = Github(auth=auth, timeout=10, retry=0)
        else:
            # Unauthenticated access (rate limited)
            self.github = Github(timeout=10, retry=0)

    def get_repo_details(self, repo_url: str) -> Dict[str, Any]:
        """
        Fetch details for a repository.
        
        Args:
            repo_url: Full URL to the repository (e.g. https://github.com/user/repo)
            
        Returns:
            Dictionary with metadata (last_pr, stars, etc.)
        """
        try:
            # Extract owner/repo from URL
            # Expected format: https://github.com/owner/repo or https://github.com/owner/repo.git
            if "github.com" not in repo_url:
                return {}
                
            path_parts = repo_url.removesuffix(".git").split("github.com/")
            if len(path_parts) < 2:
                return {}
                
            full_name = path_parts[1]
            
            repo = self.github.get_repo(full_name)
            
            metadata = {
                "stars": repo.stargazers_count,
                "description": repo.description,
                "topics": repo.get_topics(),
                "language": repo.language,
            }
            
            # Get last merged pull request
            # We look at closed PRs sorted by updated time
            pulls = repo.get_pulls(state='closed', sort='updated', direction='desc')
            
            for pr in pulls:
                if pr.merged:
                    metadata["last_pr_title"] = pr.title
                    metadata["last_pr_user"] = pr.user.login
                    metadata["last_pr_merged_at"] = pr.merged_at.isoformat() if pr.merged_at else None
                    metadata["last_pr_url"] = pr.html_url
                    break
                    
            return metadata
            
        except Exception as e:
            # Fail silently on rate limits or network issues to allow indexing to proceed
            print(f"[GITHUB] Warning: Failed to fetch metadata (API limit or network): {e}")
            return {}

    def close(self):
        """Close resources if any."""
        self.github.close()
