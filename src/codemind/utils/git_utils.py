"""
Git repository utilities.

Consolidated module for all Git operations:
- Token management (GitSaaS JWT + static env vars)
- Repository cloning, updating, and caching
- GitHub metadata extraction (via PyGithub)
- Multi-platform repository search (GitHub, Enterprise, GitLab, Bitbucket)
- Branch listing across platforms

Enterprise-hardened with configurable timeouts, SSL handling, and fallback strategies.
"""

import hashlib
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import jwt
import requests
import urllib3
from git import Repo
from github import Auth, Github


# ===========================================================================
# Configuration
# ===========================================================================

GIT_CLONE_TIMEOUT = int(os.getenv("CODEMIND_GIT_CLONE_TIMEOUT", "300"))
GIT_FETCH_TIMEOUT = int(os.getenv("CODEMIND_GIT_FETCH_TIMEOUT", "180"))
GITHUB_API_TIMEOUT = int(os.getenv("CODEMIND_GITHUB_API_TIMEOUT", "120"))

# Token env var resolution order
_TOKEN_ENV_VARS = (
    "GIT_ACCESS_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "GH_TOKEN",
    "ONPREM_GIT_TOKEN",
    "ONPREM_XYS_GIT_TOKEN",
)


# ===========================================================================
# Exceptions
# ===========================================================================

class GitTimeoutError(Exception):
    """Raised when a git operation exceeds its configured timeout."""


class GitAuthError(Exception):
    """Raised when git authentication fails."""

class GitRepoNotFoundError(Exception):
    """Raised when the target repository cannot be found."""


# ===========================================================================
# Token Management (GitSaaS + static env vars)
# ===========================================================================

_token_cache: dict[str, dict] = {}
_CACHE_REFRESH_BUFFER = 600  # Refresh 10 min before expiry


def resolve_token(repo_url: str) -> Optional[str]:
    """Resolve the best available token for a repo URL.

    Priority:
      1. GitSaaS dynamic token (if APP_INSTALLATION_ID + GITSAAS_PRIVATE_KEY set)
      2. Static env vars (see _TOKEN_ENV_VARS)
    """
    # Try GitSaaS first
    gitsaas = _get_gitsaas_token(repo_url)
    if gitsaas:
        return gitsaas

    # Fall back to static tokens
    for var in _TOKEN_ENV_VARS:
        val = os.getenv(var)
        if val:
            return val
    return None


def _get_gitsaas_token(repo_url: str) -> Optional[str]:
    """Get a GitSaaS installation token for the org that owns this repo."""
    app_id = os.getenv("APP_INSTALLATION_ID")
    private_key_raw = os.getenv("GITSAAS_PRIVATE_KEY")

    if not app_id or not private_key_raw:
        return None

    org_name = _extract_org(repo_url)
    if not org_name:
        return None

    cached = _token_cache.get(org_name.lower())
    if cached and cached["expires_at"] - time.time() > _CACHE_REFRESH_BUFFER:
        return cached["token"]

    pem_key = _format_pem_key(private_key_raw)

    try:
        token_data = _fetch_installation_token(app_id, pem_key, org_name, repo_url)
        if token_data:
            _token_cache[org_name.lower()] = token_data
            print(f"[TOKEN] ✅ GitSaaS token obtained for org '{org_name}' "
                  f"(expires in {int(token_data['expires_at'] - time.time())}s)")
            return token_data["token"]
    except Exception as e:
        print(f"[TOKEN] ❌ GitSaaS token generation failed: {e}")

    return None


def _generate_jwt(app_id: str, private_key_pem: str) -> str:
    """Generate a short-lived JWT for GitHub App authentication (10 min)."""
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": app_id}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def _fetch_installation_token(
    app_id: str, private_key_pem: str, org_name: str, repo_url: str
) -> Optional[dict]:
    """Fetch a short-lived installation access token for the given org."""
    jwt_token = _generate_jwt(app_id, private_key_pem)
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Discovery Agent/1.0",
    }

    parsed = urlparse(repo_url)
    hostname = parsed.netloc.lower()
    is_enterprise = "github" in hostname and hostname != "github.com"
    api_base = f"https://{hostname}/api/v3" if is_enterprise else "https://api.github.com"

    verify = True
    if is_enterprise and os.getenv("GITHUB_ENTERPRISE_DISABLE_SSL", "false").lower() == "true":
        verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.headers.update(headers)

    try:
        app_resp = session.get(f"{api_base}/app", verify=verify, timeout=30)
        app_resp.raise_for_status()
        print(f"[TOKEN] GitHub App verified: {app_resp.json().get('name', 'unknown')}")

        inst_resp = session.get(f"{api_base}/app/installations", verify=verify, timeout=30)
        inst_resp.raise_for_status()

        installation_id = None
        for inst in inst_resp.json():
            if inst.get("account", {}).get("login", "").lower() == org_name.lower():
                installation_id = inst["id"]
                break

        if not installation_id:
            print(f"[TOKEN] ⚠️ No installation found for org '{org_name}'")
            return None

        token_resp = session.post(
            f"{api_base}/app/installations/{installation_id}/access_tokens",
            verify=verify, timeout=30,
        )
        token_resp.raise_for_status()
        data = token_resp.json()

        expires_at_str = data.get("expires_at", "")
        try:
            expires_at = datetime.fromisoformat(
                expires_at_str.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            expires_at = time.time() + 3600

        return {"token": data["token"], "expires_at": expires_at}
    except Exception as e:
        print(f"[TOKEN] GitSaaS auth error: {e}")
        raise
    finally:
        session.close()


def _extract_org(repo_url: str) -> Optional[str]:
    """Extract org/owner name from a GitHub URL."""
    match = re.match(r"https?://[^/]+/([^/]+)/", repo_url)
    return match.group(1) if match else None


def _format_pem_key(raw_key: str) -> str:
    """Wrap a raw private key string in PEM markers if missing."""
    raw_key = raw_key.strip()
    if raw_key.startswith("-----BEGIN"):
        return raw_key
    return f"-----BEGIN RSA PRIVATE KEY-----\n{raw_key}\n-----END RSA PRIVATE KEY-----"


# ===========================================================================
# GitRepoManager — clone, update, cache, metadata
# ===========================================================================

class GitRepoManager:
    """Manages Git repository cloning and checkout.

    Enterprise-hardened features:
    - Configurable timeouts (CODEMIND_GIT_CLONE_TIMEOUT, etc.)
    - SSL bypass for GitHub Enterprise (GITHUB_ENTERPRISE_DISABLE_SSL)
    - ZIP-download fallback when git clone fails for GitHub URLs
    - Classified error messages (timeout, auth, not-found, SSL)
    - Automatic token resolution (GitSaaS → static env vars)
    """

    def __init__(self, cache_dir: str = os.getenv("CODEMIND_REPOS_PATH", "data/repos")):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ensure_repo(
        self, repo_url: str, branch: str = "main", token: str | None = None,
    ) -> tuple[Path, str, str]:
        """Clone or update a repository to cache.

        Returns: (local_path, repo_id, current_commit)
        """
        if self._is_local_path(repo_url):
            repo = Repo(repo_url)
            return Path(repo_url), self._get_repo_id(repo_url), repo.head.commit.hexsha

        # Resolve token: explicit > GitSaaS > static env vars
        if not token:
            token = resolve_token(repo_url)

        final_url = self._build_auth_url(repo_url, token)
        repo_name = self._extract_repo_name(repo_url)
        local_path = self.cache_dir / repo_name / branch

        if local_path.exists():
            self._update_existing(local_path, final_url, branch, token)
        else:
            self._clone_new(repo_url, final_url, branch, local_path, token)

        repo = Repo(local_path)
        return local_path, self._get_repo_id(repo_url), repo.head.commit.hexsha

    # --- Clone / Update ---

    def _update_existing(self, local_path, final_url, branch, token):
        repo = Repo(local_path)
        origin = repo.remotes.origin
        if token and final_url != origin.url:
            origin.set_url(final_url)

        try:
            self._run_git(
                ["git", "fetch", "origin"], cwd=str(local_path),
                timeout=GIT_FETCH_TIMEOUT, repo_url=final_url,
            )
        except GitTimeoutError:
            raise
        except Exception as e:
            raise self._classify_error(e, final_url)

        if branch in repo.heads:
            repo.heads[branch].checkout()
        else:
            repo.create_head(branch, origin.refs[branch]).set_tracking_branch(
                origin.refs[branch]
            ).checkout()
        origin.pull()

    def _clone_new(self, repo_url, final_url, branch, local_path, token):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._run_git(
                ["git", "clone", "--depth", "1", "--branch", branch,
                 "--single-branch", final_url, str(local_path)],
                timeout=GIT_CLONE_TIMEOUT, repo_url=repo_url,
            )
            print(f"[GIT] ✅ Cloned {repo_url} → {local_path}")
        except GitTimeoutError:
            if local_path.exists():
                shutil.rmtree(local_path, ignore_errors=True)
            raise
        except Exception as clone_err:
            if local_path.exists():
                shutil.rmtree(local_path, ignore_errors=True)
            if self._is_github_url(repo_url):
                print(f"[GIT] ⚠️ Clone failed, falling back to ZIP: {clone_err}")
                try:
                    self._download_zip_fallback(repo_url, branch, token, local_path)
                    print(f"[GIT] ✅ ZIP fallback succeeded for {repo_url}")
                    return
                except Exception as zip_err:
                    raise Exception(
                        f"Both git clone and ZIP fallback failed. "
                        f"Clone: {clone_err}, ZIP: {zip_err}"
                    )
            else:
                raise self._classify_error(clone_err, repo_url)

    # --- Subprocess runner ---

    def _run_git(self, cmd, timeout, repo_url="", cwd=None):
        env = os.environ.copy()
        if self._is_enterprise_github(repo_url):
            if os.getenv("GITHUB_ENTERPRISE_DISABLE_SSL", "false").lower() == "true":
                env["GIT_SSL_NO_VERIFY"] = "true"
            ca = os.getenv("GITHUB_ENTERPRISE_CA_BUNDLE")
            if ca and os.path.exists(ca):
                env["GIT_SSL_CAINFO"] = ca

        safe_cmd = " ".join(cmd)
        for secret in (os.getenv("GIT_ACCESS_TOKEN", ""), os.getenv("GITHUB_TOKEN", "")):
            if secret:
                safe_cmd = safe_cmd.replace(secret, "***")
        print(f"[GIT] Running ({timeout}s timeout): {safe_cmd}")

        try:
            return subprocess.run(
                cmd, cwd=cwd, env=env, timeout=timeout,
                capture_output=True, text=True, check=True,
            )
        except subprocess.TimeoutExpired:
            raise GitTimeoutError(f"Git operation timed out after {timeout}s")
        except subprocess.CalledProcessError as e:
            raise Exception(e.stderr.strip() or str(e))

    # --- ZIP fallback ---

    def _download_zip_fallback(self, repo_url, branch, token, target_dir):
        owner, repo_name = self._parse_github_url(repo_url)
        parsed = urlparse(repo_url)
        hostname = parsed.netloc.lower()

        if self._is_enterprise_github(repo_url):
            api_url = f"https://{hostname}/api/v3/repos/{owner}/{repo_name}/zipball/{branch}"
        else:
            api_url = f"https://api.github.com/repos/{owner}/{repo_name}/zipball/{branch}"

        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Discovery Agent/1.0"}
        if token:
            headers["Authorization"] = f"token {token}"

        verify = True
        if self._is_enterprise_github(repo_url):
            if os.getenv("GITHUB_ENTERPRISE_DISABLE_SSL", "false").lower() == "true":
                verify = False

        for attempt in range(3):
            try:
                resp = requests.get(
                    api_url, headers=headers, stream=True,
                    timeout=(30, GITHUB_API_TIMEOUT), verify=verify, allow_redirects=True,
                )
                resp.raise_for_status()
                break
            except requests.exceptions.SSLError:
                if not verify:
                    raise
                verify = False
                continue
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
        else:
            raise Exception("ZIP download failed after 3 retries")

        target_dir.mkdir(parents=True, exist_ok=True)
        zip_path = target_dir / "repo.zip"
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
        zip_path.unlink()

        # Flatten GitHub's subdirectory
        extracted_dirs = [d for d in target_dir.iterdir() if d.is_dir()]
        if extracted_dirs:
            extracted = extracted_dirs[0]
            tmp = target_dir.with_name(target_dir.name + "_flat")
            if tmp.exists():
                shutil.rmtree(tmp)
            shutil.move(str(extracted), str(tmp))
            shutil.rmtree(target_dir)
            shutil.move(str(tmp), str(target_dir))

    # --- URL helpers ---

    def _build_auth_url(self, repo_url, token):
        if not token or "@" in repo_url or not repo_url.startswith("https://"):
            return repo_url
        if "github" in urlparse(repo_url).netloc.lower():
            return repo_url.replace("https://", f"https://{token}@", 1)
        return repo_url

    def _is_github_url(self, url):
        try:
            return "github" in urlparse(url).netloc.lower()
        except Exception:
            return False

    def _is_enterprise_github(self, url):
        try:
            h = urlparse(url).netloc.lower()
            return "github" in h and h != "github.com"
        except Exception:
            return False

    def _parse_github_url(self, repo_url):
        url = repo_url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        parts = urlparse(url).path.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {repo_url}")
        return parts[0], parts[1]

    def _classify_error(self, error, repo_url):
        msg = str(error).lower()
        if "authentication failed" in msg or "could not read" in msg:
            return GitAuthError(f"Authentication failed for {repo_url}")
        if "repository not found" in msg or "does not exist" in msg:
            return GitRepoNotFoundError(f"Repository not found: {repo_url}")
        if "ssl certificate" in msg:
            return Exception(f"SSL error for {repo_url}. Set GITHUB_ENTERPRISE_DISABLE_SSL=true")
        if "timeout" in msg:
            return GitTimeoutError(str(error))
        return error

    def _is_local_path(self, path):
        return Path(path).exists() and Path(path).is_dir()

    def _extract_repo_name(self, repo_url):
        name = repo_url.rstrip("/").split("/")[-1]
        return name[:-4] if name.endswith(".git") else name

    def _get_repo_id(self, repo_identifier):
        return hashlib.sha256(repo_identifier.encode()).hexdigest()[:16]

    def cleanup_cache(self):
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)

    # --- Metadata extraction ---

    def extract_metadata(self, repo_path: Path) -> dict:
        """Extract repository metadata (commit stats, authors, GitHub details)."""
        try:
            repo = Repo(repo_path)
            if not repo.head.is_valid():
                return {}

            first_sha = repo.git.rev_list("--max-parents=0", "HEAD").splitlines()[0]
            first_commit = repo.commit(first_sha)

            last_authors, seen = [], set()
            for c in repo.iter_commits("HEAD", max_count=50):
                if c.author.name not in seen:
                    last_authors.append(c.author.name)
                    seen.add(c.author.name)
                    if len(last_authors) >= 4:
                        break

            meta: dict[str, Any] = {
                "first_commit_at": datetime.fromtimestamp(first_commit.committed_date, UTC),
                "first_author": first_commit.author.name,
                "last_authors": last_authors,
                "total_commits": int(repo.git.rev_list("--count", "HEAD")),
            }

            # GitHub metadata (stars, PRs)
            try:
                remote_url = repo.remotes.origin.url
                if "github" in remote_url.lower():
                    gh = GitHubClient()
                    meta.update(gh.get_repo_details(remote_url))
                    gh.close()
            except Exception as e:
                print(f"[GIT] GitHub metadata fetch failed: {e}")

            return meta
        except Exception as e:
            print(f"[GIT] Metadata extraction failed: {e}")
            return {}


# ===========================================================================
# GitHubClient — PyGithub metadata wrapper
# ===========================================================================

class GitHubClient:
    """Fetch GitHub repo metadata (stars, PRs, topics) via PyGithub."""

    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = token or self._resolve_static_token()
        kwargs: dict[str, Any] = {"timeout": 10, "retry": 0}
        if self.token:
            kwargs["auth"] = Auth.Token(self.token)
        if base_url:
            kwargs["base_url"] = base_url
        self.github = Github(**kwargs)

    @staticmethod
    def _resolve_static_token() -> Optional[str]:
        for var in _TOKEN_ENV_VARS:
            val = os.environ.get(var)
            if val:
                return val
        return None

    def get_repo_details(self, repo_url: str) -> dict[str, Any]:
        """Fetch metadata for a GitHub repository."""
        try:
            url_clean = repo_url.removesuffix(".git")
            full_name = None
            if "github.com/" in url_clean:
                full_name = url_clean.split("github.com/")[1]
            else:
                path = urlparse(url_clean).path.strip("/")
                if "/" in path:
                    full_name = path

            if not full_name:
                return {}

            repo = self.github.get_repo(full_name)
            meta: dict[str, Any] = {
                "stars": repo.stargazers_count,
                "description": repo.description,
                "topics": repo.get_topics(),
                "language": repo.language,
            }

            for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
                if pr.merged:
                    meta["last_pr_title"] = pr.title
                    meta["last_pr_user"] = pr.user.login
                    meta["last_pr_merged_at"] = pr.merged_at.isoformat() if pr.merged_at else None
                    meta["last_pr_url"] = pr.html_url
                    break

            return meta
        except Exception as e:
            print(f"[GITHUB] Warning: metadata fetch failed: {e}")
            return {}

    def close(self):
        self.github.close()


# ===========================================================================
# GitIntegration — multi-platform search & branch listing
# ===========================================================================

def _sort_by_priority(repos: list) -> list:
    repos.sort(key=lambda r: (not r.get("private", False), r.get("updated_at", "")), reverse=True)
    return repos


class GitIntegration:
    """Multi-platform Git repository search and branch listing.

    Supports GitHub.com, GitHub Enterprise, GitLab, Bitbucket.
    """

    _TIMEOUTS = {"github.com": 120, "gitlab.com": 120, "bitbucket.org": 120}

    # --- Public API ---

    def search_repositories(
        self, keywords: list[str], git_endpoint: str = "github.com",
        limit: int = 20, github_token: Optional[str] = None,
    ) -> list[dict]:
        """Search repos across supported Git platforms."""
        token = github_token or self._resolve(git_endpoint)

        if git_endpoint == "github.com":
            return self._search_github(keywords, limit, token)
        elif "github" in git_endpoint:
            return self._search_github_enterprise(keywords, limit, git_endpoint, token)
        elif git_endpoint == "gitlab.com":
            return self._search_gitlab(keywords, limit)
        elif git_endpoint == "bitbucket.org":
            return self._search_bitbucket(keywords, limit)
        raise ValueError(f"Unsupported git endpoint: {git_endpoint}")

    def list_branches(
        self, owner: str, name: str, git_endpoint: str = "github.com",
        github_token: Optional[str] = None,
    ) -> list[dict]:
        """List branches for a repository."""
        repo_url = f"https://{git_endpoint}/{owner}/{name}"
        token = github_token or self._resolve(git_endpoint, repo_url)

        if git_endpoint == "github.com":
            return self._list_github_branches(owner, name, token)
        elif "github" in git_endpoint:
            return self._list_github_enterprise_branches(owner, name, git_endpoint, token)
        elif git_endpoint == "gitlab.com":
            return self._list_gitlab_branches(owner, name)
        elif git_endpoint == "bitbucket.org":
            return self._list_bitbucket_branches(owner, name)
        raise ValueError(f"Unsupported git endpoint: {git_endpoint}")

    # --- GitHub search ---

    def _search_github(self, keywords, limit, token):
        repos: list[dict] = []
        if token:
            repos.extend(self._search_user_repos(keywords, limit, token, "github.com"))

        remaining = limit - len(repos)
        if remaining > 0:
            query = " ".join(keywords) + " in:name,description,readme"
            if not token:
                query += " is:public"
            headers = self._gh_headers(token)
            try:
                resp = requests.get(
                    "https://api.github.com/search/repositories",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": min(remaining, 100)},
                    headers=headers, timeout=self._timeout("github.com"),
                )
                resp.raise_for_status()
                existing = {f"{r['owner']}/{r['name']}" for r in repos}
                for item in resp.json().get("items", []):
                    key = f"{item['owner']['login']}/{item['name']}"
                    if key not in existing:
                        repos.append(self._map_gh(item, "github.com"))
                        if len(repos) >= limit:
                            break
            except Exception as e:
                print(f"[GIT-SEARCH] GitHub error: {e}")

        return _sort_by_priority(repos)[:limit]

    def _search_github_enterprise(self, keywords, limit, endpoint, token):
        repos: list[dict] = []
        if token:
            repos.extend(self._search_user_repos(keywords, limit, token, endpoint))

        remaining = limit - len(repos)
        if remaining > 0:
            query = " ".join(keywords) + " in:name,description,readme"
            headers = self._gh_headers(token)
            verify = self._ssl_verify(endpoint)
            try:
                resp = requests.get(
                    f"https://{endpoint}/api/v3/search/repositories",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": min(remaining, 100)},
                    headers=headers, timeout=self._timeout(endpoint), verify=verify,
                )
                resp.raise_for_status()
                existing = {f"{r['owner']}/{r['name']}" for r in repos}
                for item in resp.json().get("items", []):
                    key = f"{item['owner']['login']}/{item['name']}"
                    if key not in existing:
                        repos.append(self._map_gh(item, endpoint))
                        if len(repos) >= limit:
                            break
            except Exception as e:
                print(f"[GIT-SEARCH] Enterprise error ({endpoint}): {e}")

        return _sort_by_priority(repos)[:limit]

    def _search_user_repos(self, keywords, limit, token, endpoint):
        repos: list[dict] = []
        is_ent = endpoint != "github.com"
        base = f"https://{endpoint}/api/v3" if is_ent else "https://api.github.com"
        headers = self._gh_headers(token)
        verify = self._ssl_verify(endpoint) if is_ent else True

        for affiliation in ("owner", "collaborator", "organization_member"):
            if len(repos) >= limit:
                break
            for page in range(1, 4):
                if len(repos) >= limit:
                    break
                try:
                    resp = requests.get(
                        f"{base}/user/repos",
                        params={"affiliation": affiliation, "type": "all", "sort": "updated",
                                "direction": "desc", "per_page": 100, "page": page},
                        headers=headers, timeout=self._timeout(endpoint), verify=verify,
                    )
                    if resp.status_code != 200:
                        break
                    items = resp.json()
                    if not items:
                        break
                    existing = {f"{r['owner']}/{r['name']}" for r in repos}
                    for item in items:
                        if self._matches(item, keywords):
                            key = f"{item['owner']['login']}/{item['name']}"
                            if key not in existing:
                                repos.append(self._map_gh(item, endpoint))
                                existing.add(key)
                                if len(repos) >= limit:
                                    break
                except Exception as e:
                    print(f"[GIT-SEARCH] User repos error ({affiliation}): {e}")
                    break
        return repos

    # --- GitLab / Bitbucket ---

    def _search_gitlab(self, keywords, limit):
        headers = {"Accept": "application/json"}
        t = os.getenv("GITLAB_TOKEN")
        if t:
            headers["Authorization"] = f"Bearer {t}"
        try:
            resp = requests.get(
                "https://gitlab.com/api/v4/projects",
                params={"search": " ".join(keywords), "order_by": "star_count",
                        "sort": "desc", "per_page": min(limit, 100), "visibility": "public"},
                headers=headers, timeout=self._timeout("gitlab.com"),
            )
            resp.raise_for_status()
            return [{
                "name": r["name"], "owner": r["namespace"]["name"],
                "description": r.get("description", ""), "stars": r.get("star_count", 0),
                "forks": r.get("forks_count", 0), "language": "",
                "html_url": r["web_url"], "clone_url": r["http_url_to_repo"],
                "git_endpoint": "gitlab.com",
                "private": r.get("visibility", "public") != "public",
                "updated_at": r.get("last_activity_at", ""),
            } for r in resp.json()][:limit]
        except Exception as e:
            print(f"[GIT-SEARCH] GitLab error: {e}")
            return []

    def _search_bitbucket(self, keywords, limit):
        try:
            resp = requests.get(
                "https://api.bitbucket.org/2.0/repositories",
                params={"q": f'name ~ "{" ".join(keywords)}"', "sort": "-updated_on",
                        "pagelen": min(limit, 100)},
                headers={"Accept": "application/json"}, timeout=self._timeout("bitbucket.org"),
            )
            resp.raise_for_status()
            return [{
                "name": r["name"], "owner": r["owner"]["display_name"],
                "description": r.get("description", ""), "stars": 0, "forks": 0,
                "language": r.get("language", ""),
                "html_url": r["links"]["html"]["href"],
                "clone_url": r["links"]["clone"][0]["href"],
                "git_endpoint": "bitbucket.org", "private": r.get("is_private", False),
                "updated_at": r.get("updated_on", ""),
            } for r in resp.json().get("values", [])][:limit]
        except Exception as e:
            print(f"[GIT-SEARCH] Bitbucket error: {e}")
            return []

    # --- Branch listing ---

    def _list_github_branches(self, owner, name, token):
        headers = self._gh_headers(token)
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{owner}/{name}/branches",
                headers=headers, timeout=self._timeout("github.com"),
            )
            resp.raise_for_status()
            default = self._get_default(f"https://api.github.com/repos/{owner}/{name}", headers, True)
            return [{"name": b["name"], "commit_sha": b["commit"]["sha"],
                     "is_default": b["name"] == default, "protected": b.get("protected", False)}
                    for b in resp.json()]
        except Exception as e:
            print(f"[GIT-BRANCHES] GitHub error: {e}")
            return []

    def _list_github_enterprise_branches(self, owner, name, endpoint, token):
        headers = self._gh_headers(token)
        verify = self._ssl_verify(endpoint)
        try:
            resp = requests.get(
                f"https://{endpoint}/api/v3/repos/{owner}/{name}/branches",
                headers=headers, timeout=self._timeout(endpoint), verify=verify,
            )
            resp.raise_for_status()
            default = self._get_default(f"https://{endpoint}/api/v3/repos/{owner}/{name}", headers, verify)
            return [{"name": b["name"], "commit_sha": b["commit"]["sha"],
                     "is_default": b["name"] == default, "protected": b.get("protected", False)}
                    for b in resp.json()]
        except Exception as e:
            print(f"[GIT-BRANCHES] Enterprise error ({endpoint}): {e}")
            return []

    def _list_gitlab_branches(self, owner, name):
        project_id = f"{owner}%2F{name}"
        headers = {"Accept": "application/json"}
        t = os.getenv("GITLAB_TOKEN")
        if t:
            headers["Authorization"] = f"Bearer {t}"
        try:
            resp = requests.get(
                f"https://gitlab.com/api/v4/projects/{project_id}/repository/branches",
                headers=headers, timeout=self._timeout("gitlab.com"),
            )
            resp.raise_for_status()
            return [{"name": b["name"], "commit_sha": b["commit"]["id"],
                     "is_default": b.get("default", False), "protected": b.get("protected", False)}
                    for b in resp.json()]
        except Exception as e:
            print(f"[GIT-BRANCHES] GitLab error: {e}")
            return []

    def _list_bitbucket_branches(self, owner, name):
        try:
            resp = requests.get(
                f"https://api.bitbucket.org/2.0/repositories/{owner}/{name}/refs/branches",
                headers={"Accept": "application/json"}, timeout=self._timeout("bitbucket.org"),
            )
            resp.raise_for_status()
            return [{"name": b["name"], "commit_sha": b["target"]["hash"],
                     "is_default": b["name"] in ("main", "master"), "protected": False}
                    for b in resp.json().get("values", [])]
        except Exception as e:
            print(f"[GIT-BRANCHES] Bitbucket error: {e}")
            return []

    # --- Helpers ---

    def _resolve(self, endpoint="github.com", repo_url=""):
        if repo_url:
            t = resolve_token(repo_url)
            if t:
                return t
        return resolve_token(f"https://{endpoint}/org/repo")

    def _gh_headers(self, token):
        h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Discovery Agent/1.0"}
        if token:
            h["Authorization"] = f"token {token}"
        return h

    def _ssl_verify(self, endpoint):
        if os.getenv("GITHUB_ENTERPRISE_DISABLE_SSL", "false").lower() == "true":
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            return False
        ca = os.getenv("GITHUB_ENTERPRISE_CA_BUNDLE")
        if ca and os.path.exists(ca):
            return ca
        return True

    def _timeout(self, endpoint):
        return self._TIMEOUTS.get(endpoint, 180)

    def _get_default(self, url, headers, verify):
        try:
            resp = requests.get(url, headers=headers, timeout=30, verify=verify)
            if resp.status_code == 200:
                return resp.json().get("default_branch")
        except Exception:
            pass
        return None

    def _matches(self, repo, keywords):
        text = " ".join(filter(None, [
            repo.get("name", ""), repo.get("description", ""),
            repo.get("language", ""), " ".join(repo.get("topics", []) or []),
        ])).lower()
        return any(kw.lower() in text for kw in keywords)

    def _map_gh(self, item, endpoint):
        return {
            "name": item["name"], "owner": item["owner"]["login"],
            "description": item.get("description", ""),
            "stars": item.get("stargazers_count", 0), "forks": item.get("forks_count", 0),
            "language": item.get("language", ""), "html_url": item["html_url"],
            "clone_url": item["clone_url"], "git_endpoint": endpoint,
            "private": item.get("private", False), "updated_at": item.get("updated_at", ""),
        }
