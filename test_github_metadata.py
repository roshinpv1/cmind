
import os
import shutil
from pathlib import Path
from codemind.utils.git_utils import GitRepoManager

def test_metadata_extraction():
    repo_url = "https://github.com/octocat/Hello-World"
    # Ensure no token leaks, public access is fine for Hello-World
    
    manager = GitRepoManager(cache_dir="test_repos_cache")
    print(f"Cloning {repo_url}...")
    local_path, repo_id, _ = manager.ensure_repo(repo_url, branch="master")
    
    print("Extracting metadata...")
    metadata = manager.extract_metadata(local_path)
    
    print("\n--- Metadata ---")
    print(f"First Author: {metadata.get('first_author')}")
    print(f"Total Commits: {metadata.get('total_commits')}")
    print(f"Last PR Title: {metadata.get('last_pr_title')}")
    print(f"Last PR User: {metadata.get('last_pr_user')}")
    
    if metadata.get('last_pr_title'):
        print("\n✅ GitHub Metadata successfully fetched!")
    else:
        print("\n⚠️  GitHub Metadata missing (maybe rate limited or no closed PRs?)")

    # Cleanup
    manager.cleanup_cache()

if __name__ == "__main__":
    test_metadata_extraction()
