
import asyncio
import json
import httpx
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

class BatchIndexRequest(BaseModel):
    """Configuration for a single repo to index."""
    url: str
    branch: str = "main"

class BatchIndexResult(BaseModel):
    """Result of a batch index operation."""
    url: str
    branch: str
    job_id: Optional[str] = None
    repo_id: Optional[str] = None
    status: str
    error: Optional[str] = None
    catalog_status: Optional[str] = None

class BatchIndexer:
    """
    Handles batch indexing of repositories via the CodeMind API.
    """
    
    def __init__(self, api_base_url: str = "http://localhost:8000"):
        self.api_base_url = api_base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def submit_job(self, repo_url: str, branch: str) -> BatchIndexResult:
        """Submit a single indexing job."""
        try:
            payload = {
                "repo_url": repo_url,
                "branch": branch,
                "repo_path": None 
            }
            
            response = await self.client.post(
                f"{self.api_base_url}/api/v1/index",
                json=payload
            )
            
            if response.status_code == 200:
                data = response.json()
                return BatchIndexResult(
                    url=repo_url,
                    branch=branch,
                    job_id=data.get("job_id"),
                    repo_id=data.get("repo_id"),
                    status="submitted"
                )
            else:
                return BatchIndexResult(
                    url=repo_url,
                    branch=branch,
                    status="failed",
                    error=f"API Error {response.status_code}: {response.text}"
                )
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"DEBUG: Exception type: {type(e)}, args: {e.args}")
            return BatchIndexResult(
                url=repo_url,
                branch=branch,
                status="failed",
                error=f"{type(e).__name__}: {str(e)}"
            )

    async def process_batch(self, repos: List[Dict[str, str]]) -> List[BatchIndexResult]:
        """
        Process a list of repositories.
        
        Args:
            repos: List of dicts with 'url' and optionally 'branch'
        """
        results = []
        
        print(f"🚀 Starting batch indexing for {len(repos)} repositories...")
        
        for i, repo in enumerate(repos):
            url = repo.get("url")
            branch = repo.get("branch", "main")
            
            if not url:
                print(f"⚠️ Skipping entry {i}: Missing 'url'")
                continue
                
            print(f"[{i+1}/{len(repos)}] Submitting: {url} ({branch})...")
            
            result = await self.submit_job(url, branch)
            results.append(result)
            
            if result.status == "submitted":
                print(f"   ✅ Job ID: {result.job_id}")
            else:
                print(f"   ❌ Failed: {result.error}")
                
        return results

    async def wait_for_jobs(self, results: List[BatchIndexResult], repos_config: Optional[List[Dict[str, Any]]] = None, poll_interval: int = 2):
        """
        Wait for all submitted jobs to complete.
        
        Args:
            results: List of batch results
            repos_config: Original configuration (to check for playbook requests)
            poll_interval: Seconds to wait between checks
        """
        active_jobs = [r for r in results if r.job_id]
        if not active_jobs:
            return

        # Map job_id to config if provided
        job_config_map = {}
        if repos_config:
            # Assume 1:1 mapping if lengths match
            if len(results) == len(repos_config):
                for res, conf in zip(results, repos_config):
                    if res.job_id:
                        job_config_map[res.job_id] = conf

        print(f"\n⏳ Waiting for {len(active_jobs)} jobs to complete...")
        
        # Track job completion
        completed_ids = set()
        
        while len(completed_ids) < len(active_jobs):
            pending_count = 0
            
            for result in active_jobs:
                if result.job_id in completed_ids:
                    continue
                
                try:
                    response = await self.client.get(f"{self.api_base_url}/api/v1/jobs/{result.job_id}")
                    if response.status_code == 200:
                        data = response.json()
                        status = data.get("status")
                        
                        if status in ["completed", "failed"]:
                            completed_ids.add(result.job_id)
                            # Update result object in place
                            result.status = status
                            if data.get("error"):
                                result.error = data.get("error")
                                
                            icon = "✅" if status == "completed" else "❌"
                            print(f"   {icon} Job {result.job_id} finished ({status})")
                            
                            # Trigger Catalog Generation if successful and configured
                            if status == "completed" and result.job_id in job_config_map:
                                config = job_config_map[result.job_id]
                                playbook = config.get("playbook")
                                if playbook:
                                    print(f"      📖 Triggering playbook '{playbook}'...")
                                    repo_id = data.get("repo_id") or result.repo_id
                                    
                                    if repo_id:
                                        prompt = config.get("prompt")
                                        success = await self.create_catalog_entry(repo_id, playbook, prompt)
                                        result.catalog_status = "created" if success else "failed"
                                    else:
                                        print("      ⚠️ Cannot create catalog: Repo ID missing")
                                        result.catalog_status = "failed_missing_id"

                        else:
                            pending_count += 1
                            
                except Exception as e:
                    print(f"   ⚠️ Error checking job {result.job_id}: {e}")
            
            if pending_count > 0:
                await asyncio.sleep(poll_interval)
                
        print("\n✨ All jobs finished.")

    async def create_catalog_entry(self, repo_id: str, playbook: str, prompt: Optional[str] = None) -> bool:
        """Create a catalog entry for a repository."""
        try:
            payload = {
                "repo_id": repo_id,
                "playbook_name": playbook,
                "prompt": prompt
            }
            # Remove prompt if None to let backend use default
            if prompt is None:
                del payload["prompt"]
                
            response = await self.client.post(
                f"{self.api_base_url}/api/v1/catalogs",
                json=payload,
                timeout=60.0 # Playbooks might take time
            )
            
            if response.status_code == 200:
                print(f"   📘 Catalog entry created for {repo_id}")
                return True
            else:
                print(f"   ❌ Catalog creation failed: {response.text}")
                return False
        except Exception as e:
            print(f"   ❌ Catalog creation error: {e}")
            return False
