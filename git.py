
#!/usr/bin/env python3
"""
Self-contained Git Toolkit for Triage and Checkout using pygit2.

Supports:
1. Public GitHub (github.com)
2. GitHub Enterprise SaaS (github.com with SSO/PAT)
3. GitHub Enterprise Server (On-Premise custom domain)
"""

import os
import shutil
import tempfile
from pathlib import Path
import pygit2

# Automatically load environment variables from the nearest .env
try:
    from dotenv import load_dotenv
    if load_dotenv():
        print("[ℹ️ ] Loaded environment variables from .env file.")
except ImportError:
    print("[⚠️ ] python-dotenv not installed. Falling back to system environment variables.")


class UnifiedGitTriage:
    def __init__(self):
        # 1. Base Configuration
        self.flavor = os.getenv("GITHUB_FLAVOR", "PUBLIC").upper()
        self.repo_name = os.getenv("REPO_OWNER_NAME")  # e.g., 'roshinpv1/promptshield'
        self.ghes_host = os.getenv("GITHUB_HOST")      # Required for GHES (e.g. wf.github.com)
        
        # 2. Comprehensive Token Environment Variable Scan
        self.token = self._scan_env_tokens()
        
        if not self.token or not self.repo_name:
            raise ValueError("❌ Missing token or REPO_OWNER_NAME!")

        if self.flavor == "GHES" and not self.ghes_host:
            raise ValueError("❌ GITHUB_HOST environment variable is required when FLAVOR is 'GHES'.")

        # 3. Resolve SSL & URLs based on Flavor
        self.disable_ssl = os.getenv("GITHUB_ENTERPRISE_DISABLE_SSL", "false").lower() == "true"
        
        if self.flavor == "GHES":
            # On-Premise Enterprise Host
            self.clone_url = f"https://{self.ghes_host}/{self.repo_name}.git"
        else:
            # Both Public and Enterprise SaaS (GHEC) use standard github.com
            self.clone_url = f"https://github.com/{self.repo_name}.git"

    def _scan_env_tokens(self) -> str | None:
        """Scan through all valid Git-related environment variables in priority order."""
        token_vars = [
            "ONPREM_WFS_GIT_TOKEN",          # Enterprise specific 
            "ONPREM_GIT_TOKEN",              # Enterprise specific
            "GITHUB_ENTERPRISE_TOKEN",       # Standard action GHES definition
            "CODEMIND_GIT_TOKEN",            # CodeMind architecture specific
            "GITHUB_TOKEN",                  # Most standard definition
            "GH_TOKEN",                      # GitHub CLI standard
            "GIT_ACCESS_TOKEN",              # Generic Definition
            "GITHUB_PERSONAL_ACCESS_TOKEN"   # Generic explicit definition
        ]
        
        for env_var in token_vars:
            val = os.getenv(env_var)
            if val:
                print(f"[🔐] Authenticating using token extracted from: {env_var}")
                return val
        return None

    def execute_and_triage(self):
        """Checkout repository and triage the latest commit."""
        # For Github, the username can be 'x-access-token' and the password is the PAT
        credentials = pygit2.UserPass("x-access-token", self.token)
        callbacks = pygit2.RemoteCallbacks(credentials=credentials)
        
        if self.disable_ssl:
            # Disables strict SSL validation for enterprise hosts with custom certificates
            # Note: pygit2 handles this via libgit2 connection options natively.
            os.environ["GIT_SSL_NO_VERIFY"] = "true"

        target_dir = Path(tempfile.mkdtemp(prefix="git_triage_"))
        
        try:
            print(f"[🔄] Target Flavor: {self.flavor}")
            print(f"[🔄] Target URL: {self.clone_url}")
            print(f"[⬇️ ] Cloning into {target_dir}...")
            
            repo = pygit2.clone_repository(
                self.clone_url, 
                str(target_dir), 
                callbacks=callbacks,
                bare=False
            )
            
            print("[✅] Clone successful.\n")
            self._triage_repository(repo)
            
        except pygit2.GitError as e:
            print(f"\n[❌] Git operation failed: {e}")
            if self.flavor == "GHEC":
                print("Hint: For Enterprise SaaS, verify your PAT has been SSO-authorized via your org page.")
            elif self.flavor == "GHES":
                print(f"Hint: Make sure {self.ghes_host} is accessible on VPN and check GITHUB_ENTERPRISE_DISABLE_SSL.")
                
        finally:
            print(f"\n[🧹] Cleaning up temporary directory {target_dir}")
            shutil.rmtree(target_dir, ignore_errors=True)

    def _triage_repository(self, repo: pygit2.Repository):
        """Analyze the repository metadata and latest diffs natively."""
        head = repo.head
        commit = repo.get(head.target)
        
        print(f"=== REPOSITORY TRIAGE ===")
        print(f"Branch       : {head.shorthand}")
        print(f"Target Hash  : {commit.short_id}")
        print(f"Author       : {commit.author.name} <{commit.author.email}>")
        print(f"Message      : {commit.message.strip().splitlines()[0]}")
        print(f"=========================\n")
        
        print(f"=== DIFF ANALYSIS (Latest Commit vs Parent) ===")
        if not commit.parents:
            print("Initial commit detected. No parent to diff against.")
        else:
            parent = commit.parents[0]
            diff = repo.diff(parent.tree, commit.tree)
            diff.find_similar(flags=pygit2.GIT_DIFF_FIND_RENAMES | pygit2.GIT_DIFF_FIND_COPIES)
            
            added, modified, deleted, renamed = 0, 0, 0, 0
            
            for patch in diff:
                delta = patch.delta
                if delta.status == pygit2.GIT_DELTA_ADDED:
                    added += 1
                    print(f"  [+] {delta.new_file.path}")
                elif delta.status == pygit2.GIT_DELTA_MODIFIED:
                    modified += 1
                    print(f"  [*] {delta.new_file.path} ({patch.hunks[0].new_lines} lines changed)")
                elif delta.status == pygit2.GIT_DELTA_DELETED:
                    deleted += 1
                    print(f"  [-] {delta.old_file.path}")
                elif delta.status == pygit2.GIT_DELTA_RENAMED:
                    renamed += 1
                    print(f"  [>] {delta.old_file.path} -> {delta.new_file.path}")
            
            print(f"\nSummary: {added} Added, {modified} Modified, {deleted} Deleted, {renamed} Renamed files.")


if __name__ == "__main__":
    triage = UnifiedGitTriage()
    triage.execute_and_triage()
