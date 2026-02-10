
import sys
import os
import shutil
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from codemind.utils.git_utils import GitRepoManager

def test_metadata_extraction():
    print("Testing Metadata Extraction...")
    
    # Use a small public repo
    repo_url = "https://github.com/octocat/Hello-World.git"
    branch = "master"
    
    # Setup cache dir
    cache_dir = "data/repos_test_metadata"
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
        
    try:
        manager = GitRepoManager(cache_dir=cache_dir)
        
        # 1. Clone
        print(f"1. Cloning {repo_url}...")
        path, _, _ = manager.ensure_repo(repo_url, branch)
        
        # 2. Extract Metadata
        print(f"2. Extracting metadata from {path}...")
        metadata = manager.extract_metadata(path)
        
        print("\n[METADATA RESULTS]")
        for k, v in metadata.items():
            print(f"{k}: {v}")
            
        # 3. Assertions
        assert "first_commit_at" in metadata
        assert isinstance(metadata["first_commit_at"], datetime)
        assert "first_author" in metadata
        assert metadata["first_author"] == "The Octocat" # Known author of first commit
        assert "total_commits" in metadata
        assert metadata["total_commits"] > 0
        assert "last_authors" in metadata
        assert isinstance(metadata["last_authors"], list)
        
        print("\n✅ PASSED: Metadata extraction successful.")
        
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Cleanup
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)

if __name__ == "__main__":
    test_metadata_extraction()
