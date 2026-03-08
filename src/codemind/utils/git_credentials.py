"""
Unified Git credential resolution for pygit2.

Supports enterprise (PAT/SSO/MFA), Git SaaS (SSH/JWT), and public repos.
Resolves credentials via a priority chain and returns pygit2 RemoteCallbacks.
"""

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pygit2

logger = logging.getLogger(__name__)

# Env vars checked in priority order for token-based auth
_TOKEN_ENV_VARS = (
    "CODEMIND_GIT_TOKEN",
    "GIT_ACCESS_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "BITBUCKET_TOKEN",
    "AZURE_DEVOPS_PAT",
)

# JWT/GitHub App cache
_jwt_cache: dict[str, dict] = {}
_JWT_REFRESH_BUFFER = 600  # Refresh 10 min before expiry


@dataclass
class AuthResult:
    """Result of credential resolution."""

    method: str  # "token" | "ssh" | "jwt" | "public"
    callbacks: pygit2.RemoteCallbacks | None
    token: str | None = None  # For HTTP fallback


class GitCredentialProvider:
    """Resolves Git credentials for clone/fetch operations.

    Priority chain:
      1. Explicit token (passed directly or via env)
      2. SSH key (CODEMIND_SSH_PRIVATE_KEY or ~/.ssh/id_rsa)
      3. GitHub App JWT (GITSAAS_APP_ID + GITSAAS_PRIVATE_KEY)
      4. Static env-var PAT scan
      5. Public (no credentials)
    """

    def resolve(
        self,
        repo_url: str,
        token: str | None = None,
        ssh_key_path: str | None = None,
    ) -> AuthResult:
        """Resolve the best available credential for a repo URL.

        Args:
            repo_url: Repository URL (HTTPS or SSH)
            token: Explicit token override
            ssh_key_path: Explicit SSH key path override

        Returns:
            AuthResult with pygit2 RemoteCallbacks configured
        """
        # 1. Explicit token
        if token:
            return self._token_auth(token)

        # 2. SSH URL → use SSH key
        if self._is_ssh_url(repo_url):
            return self._ssh_auth(repo_url, ssh_key_path)

        # 3. GitHub App JWT (GitSaaS)
        jwt_result = self._try_gitsaas_jwt(repo_url)
        if jwt_result:
            return jwt_result

        # 4. Static env-var token scan
        env_token = self._scan_env_tokens()
        if env_token:
            return self._token_auth(env_token)

        # 5. SSH key if available (for HTTPS repos that support SSH fallback)
        ssh_result = self._try_ssh_key(ssh_key_path)
        if ssh_result:
            return ssh_result

        # 6. Public — no credentials
        logger.debug("No credentials found, using public access")
        return AuthResult(method="public", callbacks=None)

    # ── Token Auth ────────────────────────────────────────────────────────

    def _token_auth(self, token: str) -> AuthResult:
        """Create token-based auth (works for GitHub, GitLab, Bitbucket, Azure)."""
        credentials = pygit2.UserPass("x-access-token", token)
        callbacks = pygit2.RemoteCallbacks(credentials=credentials)
        logger.debug("Using token-based authentication")
        return AuthResult(method="token", callbacks=callbacks, token=token)

    # ── SSH Auth ──────────────────────────────────────────────────────────

    def _ssh_auth(self, repo_url: str, key_path: str | None = None) -> AuthResult:
        """Create SSH key auth for git@ URLs."""
        username = self._extract_ssh_user(repo_url)
        pubkey, privkey, passphrase = self._resolve_ssh_paths(key_path)

        if not privkey or not Path(privkey).exists():
            logger.warning("SSH URL but no private key found, trying agent")
            # Try SSH agent
            credentials = pygit2.KeypairFromAgent(username)
        else:
            credentials = pygit2.Keypair(
                username,
                pubkey if pubkey and Path(pubkey).exists() else "",
                privkey,
                passphrase or "",
            )

        callbacks = pygit2.RemoteCallbacks(credentials=credentials)
        logger.debug(f"Using SSH authentication as {username}")
        return AuthResult(method="ssh", callbacks=callbacks)

    def _try_ssh_key(self, key_path: str | None = None) -> AuthResult | None:
        """Try SSH key auth if a key file exists."""
        _, privkey, passphrase = self._resolve_ssh_paths(key_path)
        if privkey and Path(privkey).exists():
            credentials = pygit2.Keypair(
                "git",
                str(Path(privkey).with_suffix(".pub")) if Path(privkey).with_suffix(".pub").exists() else "",
                privkey,
                passphrase or "",
            )
            callbacks = pygit2.RemoteCallbacks(credentials=credentials)
            return AuthResult(method="ssh", callbacks=callbacks)
        return None

    def _resolve_ssh_paths(self, key_path: str | None) -> tuple[str | None, str | None, str | None]:
        """Resolve SSH key paths from args or env."""
        passphrase = os.getenv("CODEMIND_SSH_PASSPHRASE", "")

        if key_path:
            privkey = key_path
        else:
            privkey = os.getenv("CODEMIND_SSH_PRIVATE_KEY")

        if not privkey:
            # Check default locations
            default_keys = [
                Path.home() / ".ssh" / "id_ed25519",
                Path.home() / ".ssh" / "id_rsa",
            ]
            for k in default_keys:
                if k.exists():
                    privkey = str(k)
                    break

        if privkey:
            pubkey = str(Path(privkey).with_suffix(".pub"))
            return pubkey, privkey, passphrase

        return None, None, passphrase

    # ── GitHub App / GitSaaS JWT ──────────────────────────────────────────

    def _try_gitsaas_jwt(self, repo_url: str) -> AuthResult | None:
        """Try GitHub App installation token."""
        app_id = os.getenv("GITSAAS_APP_ID")
        private_key = os.getenv("GITSAAS_PRIVATE_KEY")
        installation_id = os.getenv("GITSAAS_INSTALLATION_ID")

        if not (app_id and private_key):
            return None

        try:
            token = self._get_installation_token(app_id, private_key, installation_id, repo_url)
            if token:
                logger.debug("Using GitHub App JWT authentication")
                return self._token_auth(token)
        except Exception as e:
            logger.warning(f"GitSaaS JWT auth failed: {e}")

        return None

    def _get_installation_token(
        self, app_id: str, private_key_pem: str,
        installation_id: str | None, repo_url: str,
    ) -> str | None:
        """Fetch a short-lived installation access token."""
        cache_key = f"{app_id}:{installation_id or 'default'}"

        # Check cache
        cached = _jwt_cache.get(cache_key)
        if cached and cached.get("expires_at", 0) > time.time() + _JWT_REFRESH_BUFFER:
            return cached["token"]

        try:
            import jwt as pyjwt
            import requests

            # Generate JWT
            now = int(time.time())
            payload = {
                "iat": now - 60,
                "exp": now + 600,
                "iss": app_id,
            }

            formatted_key = self._format_pem_key(private_key_pem)
            encoded_jwt = pyjwt.encode(payload, formatted_key, algorithm="RS256")

            # Get installation token
            headers = {
                "Authorization": f"Bearer {encoded_jwt}",
                "Accept": "application/vnd.github+json",
            }

            if installation_id:
                url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
            else:
                # Find installation for the org
                org = self._extract_org(repo_url)
                install_url = "https://api.github.com/app/installations"
                resp = requests.get(install_url, headers=headers, timeout=10)
                resp.raise_for_status()
                installations = resp.json()

                inst_id = None
                for inst in installations:
                    if inst.get("account", {}).get("login", "").lower() == org.lower():
                        inst_id = inst["id"]
                        break

                if not inst_id:
                    logger.warning(f"No installation found for org: {org}")
                    return None

                url = f"https://api.github.com/app/installations/{inst_id}/access_tokens"

            resp = requests.post(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            token = data["token"]
            expires_at = time.time() + 3600  # 1 hour

            _jwt_cache[cache_key] = {"token": token, "expires_at": expires_at}
            return token

        except ImportError:
            logger.warning("PyJWT not installed, cannot use GitHub App auth")
            return None
        except Exception as e:
            logger.warning(f"Installation token fetch failed: {e}")
            return None

    # ── Env Token Scan ────────────────────────────────────────────────────

    def _scan_env_tokens(self) -> str | None:
        """Scan environment variables for a Git token."""
        for var in _TOKEN_ENV_VARS:
            val = os.getenv(var)
            if val:
                logger.debug(f"Using token from {var}")
                return val
        return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_ssh_url(url: str) -> bool:
        """Check if URL uses SSH protocol."""
        return url.startswith("git@") or url.startswith("ssh://")

    @staticmethod
    def _extract_ssh_user(url: str) -> str:
        """Extract SSH username from URL (default: git)."""
        if url.startswith("git@"):
            return "git"
        parsed = urlparse(url)
        return parsed.username or "git"

    @staticmethod
    def _extract_org(repo_url: str) -> str:
        """Extract org/owner name from a Git URL."""
        url = repo_url.rstrip("/").removesuffix(".git")
        parts = url.split("/")
        return parts[-2] if len(parts) >= 2 else ""

    @staticmethod
    def _format_pem_key(raw_key: str) -> str:
        """Wrap a raw private key string in PEM markers if missing."""
        if "BEGIN" not in raw_key:
            raw_key = f"-----BEGIN RSA PRIVATE KEY-----\n{raw_key}\n-----END RSA PRIVATE KEY-----"
        return raw_key
