"""
Multi-platform Git integration.

Provides repository search and branch listing across GitHub, GitHub Enterprise,
GitLab, Bitbucket, and Azure DevOps.
"""

import os
from typing import Optional

import requests
import urllib3


def _sort_by_priority(repos: list) -> list:
    """Sort repos: private first, then by most recently updated."""

    def key(r):
        return (not r.get("private", False), r.get("updated_at", ""))

    repos.sort(key=key, reverse=True)
    return repos


class GitIntegration:
    """Multi-platform Git repository search and branch listing.

    Supports:
    - GitHub.com (public API)
    - GitHub Enterprise (configurable SSL, token fallback)
    - GitLab.com
    - Bitbucket.org
    """

    # Platform-specific default timeouts (seconds)
    _TIMEOUTS = {
        "github.com": 120,
        "gitlab.com": 120,
        "bitbucket.org": 120,
        "dev.azure.com": 120,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_repositories(
        self,
        keywords: list[str],
        git_endpoint: str = "github.com",
        limit: int = 20,
        github_token: Optional[str] = None,
    ) -> list[dict]:
        """Search repositories across supported Git platforms.

        Args:
            keywords: Search terms
            git_endpoint: Git host (github.com, gitlab.com, bitbucket.org, etc.)
            limit: Max results
            github_token: Access token (required for private repos / enterprise)

        Returns:
            List of repo dicts with keys: name, owner, description, stars,
            forks, language, html_url, clone_url, git_endpoint, private, updated_at
        """
        token = github_token or self._resolve_token(git_endpoint)

        if git_endpoint == "github.com":
            return self._search_github(keywords, limit, token)
        elif "github" in git_endpoint:
            return self._search_github_enterprise(keywords, limit, git_endpoint, token)
        elif git_endpoint == "gitlab.com":
            return self._search_gitlab(keywords, limit)
        elif git_endpoint == "bitbucket.org":
            return self._search_bitbucket(keywords, limit)
        else:
            raise ValueError(f"Unsupported git endpoint: {git_endpoint}")

    def list_branches(
        self,
        owner: str,
        name: str,
        git_endpoint: str = "github.com",
        github_token: Optional[str] = None,
    ) -> list[dict]:
        """List branches for a repository.

        Returns:
            List of dicts with keys: name, commit_sha, is_default, protected
        """
        # Build a synthetic URL for token resolution (GitSaaS needs org from URL)
        repo_url = f"https://{git_endpoint}/{owner}/{name}"
        token = github_token or self._resolve_token(git_endpoint, repo_url)

        if git_endpoint == "github.com":
            return self._list_github_branches(owner, name, token)
        elif "github" in git_endpoint:
            return self._list_github_enterprise_branches(owner, name, git_endpoint, token)
        elif git_endpoint == "gitlab.com":
            return self._list_gitlab_branches(owner, name)
        elif git_endpoint == "bitbucket.org":
            return self._list_bitbucket_branches(owner, name)
        else:
            raise ValueError(f"Unsupported git endpoint: {git_endpoint}")

    # ------------------------------------------------------------------
    # GitHub.com search
    # ------------------------------------------------------------------

    def _search_github(
        self, keywords: list[str], limit: int, token: Optional[str]
    ) -> list[dict]:
        """Search GitHub.com — user's private repos first, then public search."""
        repos: list[dict] = []

        # Step 1: user's accessible repos (includes private)
        if token:
            private = self._search_user_repos(keywords, limit, token, "github.com")
            repos.extend(private)

        # Step 2: fill remaining from public search API
        remaining = limit - len(repos)
        if remaining > 0:
            query = " ".join(keywords) + " in:name,description,readme"
            if not token:
                query += " is:public"

            headers = self._github_headers(token)
            try:
                resp = requests.get(
                    "https://api.github.com/search/repositories",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": min(remaining, 100)},
                    headers=headers,
                    timeout=self._timeout("github.com"),
                )
                resp.raise_for_status()
                existing = {f"{r['owner']}/{r['name']}" for r in repos}
                for item in resp.json().get("items", []):
                    key = f"{item['owner']['login']}/{item['name']}"
                    if key not in existing:
                        repos.append(self._map_github_repo(item, "github.com"))
                        if len(repos) >= limit:
                            break
            except Exception as e:
                print(f"[GIT-SEARCH] GitHub search error: {e}")

        return _sort_by_priority(repos)[:limit]

    # ------------------------------------------------------------------
    # GitHub Enterprise search
    # ------------------------------------------------------------------

    def _search_github_enterprise(
        self, keywords: list[str], limit: int, endpoint: str, token: Optional[str]
    ) -> list[dict]:
        """Search GitHub Enterprise — user repos + search API."""
        repos: list[dict] = []

        if token:
            user_repos = self._search_user_repos(keywords, limit, token, endpoint)
            repos.extend(user_repos)

        remaining = limit - len(repos)
        if remaining > 0:
            query = " ".join(keywords) + " in:name,description,readme"
            headers = self._github_headers(token)
            verify = self._enterprise_ssl_verify(endpoint)

            try:
                resp = requests.get(
                    f"https://{endpoint}/api/v3/search/repositories",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": min(remaining, 100)},
                    headers=headers,
                    timeout=self._timeout(endpoint),
                    verify=verify,
                )
                resp.raise_for_status()
                existing = {f"{r['owner']}/{r['name']}" for r in repos}
                for item in resp.json().get("items", []):
                    key = f"{item['owner']['login']}/{item['name']}"
                    if key not in existing:
                        repos.append(self._map_github_repo(item, endpoint))
                        if len(repos) >= limit:
                            break
            except Exception as e:
                print(f"[GIT-SEARCH] Enterprise search error ({endpoint}): {e}")

        return _sort_by_priority(repos)[:limit]

    # ------------------------------------------------------------------
    # User-accessible repos (private-first search)
    # ------------------------------------------------------------------

    def _search_user_repos(
        self,
        keywords: list[str],
        limit: int,
        token: str,
        endpoint: str,
    ) -> list[dict]:
        """Search through authenticated user's repos for keyword matches."""
        repos: list[dict] = []
        is_enterprise = endpoint != "github.com"
        base = f"https://{endpoint}/api/v3" if is_enterprise else "https://api.github.com"
        headers = self._github_headers(token)
        verify = self._enterprise_ssl_verify(endpoint) if is_enterprise else True

        for affiliation in ("owner", "collaborator", "organization_member"):
            if len(repos) >= limit:
                break
            for page in range(1, 4):  # up to 300 repos
                if len(repos) >= limit:
                    break
                try:
                    resp = requests.get(
                        f"{base}/user/repos",
                        params={
                            "affiliation": affiliation,
                            "type": "all",
                            "sort": "updated",
                            "direction": "desc",
                            "per_page": 100,
                            "page": page,
                        },
                        headers=headers,
                        timeout=self._timeout(endpoint),
                        verify=verify,
                    )
                    if resp.status_code != 200:
                        break
                    items = resp.json()
                    if not items:
                        break
                    existing = {f"{r['owner']}/{r['name']}" for r in repos}
                    for item in items:
                        if self._matches_keywords(item, keywords):
                            key = f"{item['owner']['login']}/{item['name']}"
                            if key not in existing:
                                repos.append(self._map_github_repo(item, endpoint))
                                existing.add(key)
                                if len(repos) >= limit:
                                    break
                except Exception as e:
                    print(f"[GIT-SEARCH] User repos error ({affiliation}): {e}")
                    break

        return repos

    # ------------------------------------------------------------------
    # GitLab
    # ------------------------------------------------------------------

    def _search_gitlab(self, keywords: list[str], limit: int) -> list[dict]:
        headers = {"Accept": "application/json"}
        token = os.getenv("GITLAB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = requests.get(
                "https://gitlab.com/api/v4/projects",
                params={
                    "search": " ".join(keywords),
                    "order_by": "star_count",
                    "sort": "desc",
                    "per_page": min(limit, 100),
                    "visibility": "public",
                },
                headers=headers,
                timeout=self._timeout("gitlab.com"),
            )
            resp.raise_for_status()
            return [
                {
                    "name": r["name"],
                    "owner": r["namespace"]["name"],
                    "description": r.get("description", ""),
                    "stars": r.get("star_count", 0),
                    "forks": r.get("forks_count", 0),
                    "language": "",
                    "html_url": r["web_url"],
                    "clone_url": r["http_url_to_repo"],
                    "git_endpoint": "gitlab.com",
                    "private": r.get("visibility", "public") != "public",
                    "updated_at": r.get("last_activity_at", ""),
                }
                for r in resp.json()
            ][:limit]
        except Exception as e:
            print(f"[GIT-SEARCH] GitLab error: {e}")
            return []

    # ------------------------------------------------------------------
    # Bitbucket
    # ------------------------------------------------------------------

    def _search_bitbucket(self, keywords: list[str], limit: int) -> list[dict]:
        try:
            resp = requests.get(
                "https://api.bitbucket.org/2.0/repositories",
                params={
                    "q": f'name ~ "{" ".join(keywords)}"',
                    "sort": "-updated_on",
                    "pagelen": min(limit, 100),
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout("bitbucket.org"),
            )
            resp.raise_for_status()
            return [
                {
                    "name": r["name"],
                    "owner": r["owner"]["display_name"],
                    "description": r.get("description", ""),
                    "stars": 0,
                    "forks": 0,
                    "language": r.get("language", ""),
                    "html_url": r["links"]["html"]["href"],
                    "clone_url": r["links"]["clone"][0]["href"],
                    "git_endpoint": "bitbucket.org",
                    "private": r.get("is_private", False),
                    "updated_at": r.get("updated_on", ""),
                }
                for r in resp.json().get("values", [])
            ][:limit]
        except Exception as e:
            print(f"[GIT-SEARCH] Bitbucket error: {e}")
            return []

    # ------------------------------------------------------------------
    # Branch listing
    # ------------------------------------------------------------------

    def _list_github_branches(
        self, owner: str, name: str, token: Optional[str]
    ) -> list[dict]:
        headers = self._github_headers(token)
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{owner}/{name}/branches",
                headers=headers,
                timeout=self._timeout("github.com"),
            )
            resp.raise_for_status()

            # Get default branch
            default_branch = self._get_default_branch(
                f"https://api.github.com/repos/{owner}/{name}",
                headers, True,
            )

            return [
                {
                    "name": b["name"],
                    "commit_sha": b["commit"]["sha"],
                    "is_default": b["name"] == default_branch,
                    "protected": b.get("protected", False),
                }
                for b in resp.json()
            ]
        except Exception as e:
            print(f"[GIT-BRANCHES] GitHub error: {e}")
            return []

    def _list_github_enterprise_branches(
        self, owner: str, name: str, endpoint: str, token: Optional[str]
    ) -> list[dict]:
        headers = self._github_headers(token)
        verify = self._enterprise_ssl_verify(endpoint)
        try:
            resp = requests.get(
                f"https://{endpoint}/api/v3/repos/{owner}/{name}/branches",
                headers=headers,
                timeout=self._timeout(endpoint),
                verify=verify,
            )
            resp.raise_for_status()

            default_branch = self._get_default_branch(
                f"https://{endpoint}/api/v3/repos/{owner}/{name}",
                headers, verify,
            )

            return [
                {
                    "name": b["name"],
                    "commit_sha": b["commit"]["sha"],
                    "is_default": b["name"] == default_branch,
                    "protected": b.get("protected", False),
                }
                for b in resp.json()
            ]
        except Exception as e:
            print(f"[GIT-BRANCHES] Enterprise error ({endpoint}): {e}")
            return []

    def _list_gitlab_branches(self, owner: str, name: str) -> list[dict]:
        project_id = f"{owner}%2F{name}"
        headers = {"Accept": "application/json"}
        token = os.getenv("GITLAB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = requests.get(
                f"https://gitlab.com/api/v4/projects/{project_id}/repository/branches",
                headers=headers,
                timeout=self._timeout("gitlab.com"),
            )
            resp.raise_for_status()
            return [
                {
                    "name": b["name"],
                    "commit_sha": b["commit"]["id"],
                    "is_default": b.get("default", False),
                    "protected": b.get("protected", False),
                }
                for b in resp.json()
            ]
        except Exception as e:
            print(f"[GIT-BRANCHES] GitLab error: {e}")
            return []

    def _list_bitbucket_branches(self, owner: str, name: str) -> list[dict]:
        try:
            resp = requests.get(
                f"https://api.bitbucket.org/2.0/repositories/{owner}/{name}/refs/branches",
                headers={"Accept": "application/json"},
                timeout=self._timeout("bitbucket.org"),
            )
            resp.raise_for_status()
            return [
                {
                    "name": b["name"],
                    "commit_sha": b["target"]["hash"],
                    "is_default": b["name"] in ("main", "master"),
                    "protected": False,
                }
                for b in resp.json().get("values", [])
            ]
        except Exception as e:
            print(f"[GIT-BRANCHES] Bitbucket error: {e}")
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_token(self, endpoint: str = "github.com", repo_url: str = "") -> Optional[str]:
        """Resolve token via token_manager (GitSaaS → static env vars)."""
        from .token_manager import resolve_token

        # If we have a repo URL, try GitSaaS dynamic token first
        if repo_url:
            token = resolve_token(repo_url)
            if token:
                return token

        # For search (no specific repo URL), try with a generic endpoint URL
        generic_url = f"https://{endpoint}/org/repo"
        return resolve_token(generic_url)

    def _github_headers(self, token: Optional[str]) -> dict:
        h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Discovery Agent/1.0"}
        if token:
            h["Authorization"] = f"token {token}"
        return h

    def _enterprise_ssl_verify(self, endpoint: str) -> bool:
        """Determine SSL verification setting for an enterprise endpoint."""
        if os.getenv("GITHUB_ENTERPRISE_DISABLE_SSL", "false").lower() == "true":
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            return False
        ca = os.getenv("GITHUB_ENTERPRISE_CA_BUNDLE")
        if ca and os.path.exists(ca):
            return ca  # requests accepts path as verify param
        return True

    def _timeout(self, endpoint: str) -> int:
        return self._TIMEOUTS.get(endpoint, 180)

    def _get_default_branch(self, repo_api_url: str, headers: dict, verify) -> Optional[str]:
        try:
            resp = requests.get(repo_api_url, headers=headers, timeout=30, verify=verify)
            if resp.status_code == 200:
                return resp.json().get("default_branch")
        except Exception:
            pass
        return None

    def _matches_keywords(self, repo: dict, keywords: list[str]) -> bool:
        """Check if a repo dict matches any keyword."""
        text = " ".join(
            filter(None, [
                repo.get("name", ""),
                repo.get("description", ""),
                repo.get("language", ""),
                " ".join(repo.get("topics", []) or []),
            ])
        ).lower()
        return any(kw.lower() in text for kw in keywords)

    def _map_github_repo(self, item: dict, endpoint: str) -> dict:
        """Map GitHub API repo object to our standard dict."""
        return {
            "name": item["name"],
            "owner": item["owner"]["login"],
            "description": item.get("description", ""),
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "language": item.get("language", ""),
            "html_url": item["html_url"],
            "clone_url": item["clone_url"],
            "git_endpoint": endpoint,
            "private": item.get("private", False),
            "updated_at": item.get("updated_at", ""),
        }
