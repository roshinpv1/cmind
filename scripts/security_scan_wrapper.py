"""Simple one-call wrapper: index repo, run security scan, return result."""

from __future__ import annotations

import time
from typing import Any

import httpx


class SecurityScanWrapperError(RuntimeError):
    pass


def _raise_for_api_error(resp: httpx.Response, context: str) -> None:
    if 200 <= resp.status_code < 300:
        return
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text
    raise SecurityScanWrapperError(f"{context} failed ({resp.status_code}): {payload}")


def _poll_status(
    client: httpx.Client,
    status_path: str,
    *,
    poll_interval_sec: float,
    timeout_sec: float,
    phase: str,
) -> dict[str, Any]:
    start = time.time()
    last: dict[str, Any] = {}
    while True:
        resp = client.get(status_path)
        _raise_for_api_error(resp, f"{phase} status check")
        last = resp.json()
        status = str(last.get("status") or "").lower()
        if status in {"completed", "failed"}:
            return last
        if (time.time() - start) > timeout_sec:
            raise SecurityScanWrapperError(
                f"{phase} timed out after {timeout_sec}s (last status={status or 'unknown'})"
            )
        time.sleep(poll_interval_sec)


def run_security_scan_once(
    *,
    base_url: str,
    repo_url: str,
    branch: str = "main",
    bearer_token: str | None = None,
    org: str | None = None,
    max_iterations: int = 50,
    poll_interval_sec: float = 3.0,
    indexing_timeout_sec: float = 3600.0,
    autonomous_timeout_sec: float = 3600.0,
    mirror_mode: bool = True,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """
    One method for consumers:
      1) index repository
      2) wait for index completion
      3) run autonomous security_vulnerability_scan
      4) wait for completion
      5) return {repo_url, branch, repo_id, result}
    """
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout_seconds,
        headers=headers,
    ) as client:
        # 1) Start indexing
        idx_resp = client.post(
            "/api/v1/index",
            json={"repo_url": repo_url, "branch": branch, "org": org},
        )
        _raise_for_api_error(idx_resp, "Index submission")
        idx_payload = idx_resp.json()
        index_job_id = str(idx_payload["job_id"])
        repo_id = idx_payload.get("repo_id")

        # 2) Poll index status
        index_final = _poll_status(
            client,
            f"/api/v1/jobs/{index_job_id}",
            poll_interval_sec=poll_interval_sec,
            timeout_sec=indexing_timeout_sec,
            phase="Indexing",
        )
        index_status = str(index_final.get("status") or "").lower()
        repo_id = index_final.get("repo_id") or repo_id
        if index_status != "completed":
            raise SecurityScanWrapperError(
                f"Indexing failed: {index_final.get('error') or 'unknown error'}"
            )
        if not repo_id:
            raise SecurityScanWrapperError("Indexing completed but repo_id missing.")

        # 3) Start autonomous security scan
        auto_resp = client.post(
            "/api/v1/agents/autonomous",
            json={
                "goal": (
                    "Perform a comprehensive application security vulnerability audit. "
                    "Return the structured JSON result with evidence-backed findings."
                ),
                "repo_id": repo_id,
                "max_iterations": max_iterations,
                "allowed_playbooks": ["security_vulnerability_scan"],
                "mirror_mode": mirror_mode,
            },
        )
        _raise_for_api_error(auto_resp, "Autonomous execution submission")
        auto_payload = auto_resp.json()
        autonomous_job_id = str(auto_payload["job_id"])

        # 4) Poll autonomous status
        auto_final = _poll_status(
            client,
            f"/api/v1/agents/autonomous/{autonomous_job_id}/status",
            poll_interval_sec=poll_interval_sec,
            timeout_sec=autonomous_timeout_sec,
            phase="Autonomous scan",
        )
        auto_status = str(auto_final.get("status") or "").lower()
        if auto_status != "completed":
            raise SecurityScanWrapperError(
                f"Autonomous scan failed: {auto_final.get('error') or 'unknown error'}"
            )

        # 5) Fetch final result
        result_resp = client.get(f"/api/v1/agents/autonomous/{autonomous_job_id}/result")
        _raise_for_api_error(result_resp, "Autonomous result fetch")
        result_payload = result_resp.json()

        return {
            "repo_url": repo_url,
            "branch": branch,
            "repo_id": str(repo_id),
            "result": result_payload,
        }
