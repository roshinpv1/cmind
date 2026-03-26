"""
Indexing Worker — standalone process that polls for pending indexing jobs.

Run as: python -m codemind.worker.index_worker
"""

import os
import sys
import time
import signal
import logging
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from codemind.jobs import JobManager, JobStatus
from codemind.storage import ManifestManager
from codemind.storage.lancedb_storage import LanceDBStorage
from codemind.graph.graph_db import KuzuGraphAdapter
from codemind.workflows import IndexingState, IndexingWorkflow

logger = logging.getLogger("codemind.worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WORKER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Graceful shutdown
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received shutdown signal, finishing current job…")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


class IndexWorker:
    """
    Polls the jobs table for PENDING indexing jobs and executes them.

    Designed to run as a separate process from the FastAPI API server.
    Both processes share SQLite (WAL mode) and LanceDB on disk.
    """

    def __init__(
        self,
        poll_interval: int = 5,
        db_path: str | None = None,
        graph_db: "KuzuGraphAdapter | None" = None,
    ):
        self.poll_interval = poll_interval
        
        if db_path is None:
            import os
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_DB_PATH", os.path.join(base_default, "codemind.db"))
            
        # Shared infrastructure — same paths as the API server
        self.job_manager = JobManager(db_path=db_path)
        self.manifest = ManifestManager(db_path=db_path)
        self.lance_storage = LanceDBStorage()

        # Graph DB (Kuzu) - Use provided instance or create own
        # When running inside the server process, the server passes its instance
        # to avoid Kuzu single-process lock conflicts.
        self.graph_db = graph_db if graph_db is not None else KuzuGraphAdapter()

        logger.info("Worker initialized (poll_interval=%ds)", poll_interval)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        """Start the polling loop."""
        logger.info("▶  Worker started — polling for jobs…")

        while not _shutdown:
            job = self._claim_next_job()

            if job:
                self._execute_job(job)
            else:
                time.sleep(self.poll_interval)

        logger.info("⏹  Worker stopped gracefully.")

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def _claim_next_job(self):
        """
        Atomically find and claim the oldest PENDING job.

        Returns the claimed JobModel or None.
        """
        with self.job_manager.db.get_session() as session:
            from codemind.jobs.job_manager import JobModel

            job = (
                session.query(JobModel)
                .filter_by(status=JobStatus.PENDING)
                .order_by(JobModel.created_at.asc())
                .first()
            )

            if not job:
                return None

            # Claim it
            job.status = JobStatus.RUNNING
            job.stage = "starting"
            job.updated_at = datetime.now(UTC)
            session.commit()

            # Detach from session so we can use it after session closes
            job_id = job.id
            repo_path = job.repo_path
            repo_url = job.repo_url
            branch = job.branch
            repo_id = job.repo_id
            org = getattr(job, 'org', None)
            user_id = getattr(job, 'user_id', None)

        # Return a plain dict to avoid detached-instance issues
        return {
            "id": job_id,
            "repo_path": repo_path,
            "repo_url": repo_url,
            "branch": branch,
            "repo_id": repo_id,
            "org": org,
            "user_id": user_id,
        }

    def _execute_job(self, job: dict):
        """Execute a single indexing job."""
        job_id = job["id"]
        logger.info("━" * 60)
        logger.info("Processing job %s", job_id)

        try:
            # Resolve repo path (clone if git URL)
            resolved = self._resolve_repo(job)
            actual_repo_path = resolved["repo_path"]
            actual_repo_id = resolved["repo_id"]

            # Update job with resolved path
            self.job_manager.update_job(
                job_id,
                repo_path=actual_repo_path,
                stage="indexing",
            )

            # Progress callback — writes stage + progress to the DB in real time
            def _on_progress(stage: str, progress: int):
                self.job_manager.update_job(job_id, stage=stage, progress=progress)

            # Create workflow with graph adapter and progress reporting
            workflow = IndexingWorkflow(
                self.manifest, self.lance_storage, self.graph_db,
                progress_callback=_on_progress,
            )

            # Create workflow state
            state = IndexingState(
                repo_path=actual_repo_path,
                repo_id=actual_repo_id,
                job_id=job_id,
                org=job.get("org"),
                repo_url=resolved.get("repo_url"),
                cd_repo_url=resolved.get("cd_repo_url"),
                user_id=job.get("user_id"),
            )

            # Run the full indexing workflow
            final_state = workflow.run(state)

            # Update job based on result
            if final_state.error:
                logger.error("Job %s FAILED at stage '%s': %s", job_id, final_state.stage, final_state.error)
                self.job_manager.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    stage=final_state.stage,
                    error=final_state.error,
                )
            else:
                logger.info("Job %s COMPLETED ✅", job_id)
                self.job_manager.update_job(
                    job_id,
                    status=JobStatus.COMPLETED,
                    stage="completed",
                    progress=100,
                )

        except Exception as e:
            logger.exception("Job %s raised an exception", job_id)
            self.job_manager.update_job(
                job_id,
                status=JobStatus.FAILED,
                error=str(e),
                progress=0,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_repo(self, job: dict) -> dict:
        """
        Resolve the repository to a local path and repo_id.

        If repo_url is set, clones/updates the repo first.
        Also checks for a companion '-CD' repository and merges its files.

        Returns dict with repo_path, repo_id, repo_url, cd_repo_url.
        """
        repo_url = job.get("repo_url")
        repo_path = job.get("repo_path")
        branch = job.get("branch", "main")
        repo_id = job.get("repo_id")

        if repo_url:
            from codemind.utils.git_utils import GitRepoManager

            git_token = os.environ.get("GIT_ACCESS_TOKEN")
            git_manager = GitRepoManager()
            local_path, computed_repo_id, _ = git_manager.ensure_repo(
                repo_url, branch, token=git_token
            )

            # Check for companion -CD repo and merge if found
            cd_repo_url = self._try_clone_cd_companion(
                repo_url, branch, local_path, git_manager, git_token
            )

            return {
                "repo_path": str(local_path),
                "repo_id": computed_repo_id,
                "repo_url": repo_url,
                "cd_repo_url": cd_repo_url,
            }

        # Local path
        return {
            "repo_path": repo_path,
            "repo_id": repo_id or self.manifest._compute_repo_id(repo_path),
            "repo_url": None,
            "cd_repo_url": None,
        }

    def _try_clone_cd_companion(self, repo_url, branch, main_local_path, git_manager, token):
        """
        Detect if a companion '-CD' repository exists and merge its files.

        Convention: for repo 'my-service.git', the CD repo is 'my-service-CD.git'.
        CD files are placed under main_local_path/_cd/ to avoid collisions.
        If the CD repo doesn't exist or fails to clone, indexing proceeds normally.

        Returns the CD repo URL if found, else None.
        """
        import shutil

        # Build CD repo URL:
        #   https://host/org/repo.git  →  https://host/org/repo-CD.git
        #   https://host/org/repo      →  https://host/org/repo-CD
        base_url = repo_url.rstrip("/")
        if base_url.endswith(".git"):
            cd_url = base_url[:-4] + "-CD.git"
        else:
            cd_url = base_url + "-CD"

        logger.info("Checking for companion CD repo: %s", cd_url)

        try:
            # Try same branch first, fall back to 'main' if that fails
            try:
                cd_local_path, _, _ = git_manager.ensure_repo(cd_url, branch, token=token)
            except Exception:
                if branch != "main":
                    logger.info("CD repo branch '%s' not found, trying 'main'", branch)
                    cd_local_path, _, _ = git_manager.ensure_repo(cd_url, "main", token=token)
                else:
                    raise

            # Merge CD files into main repo under _cd/ subfolder
            target_dir = Path(main_local_path) / "_cd"
            if target_dir.exists():
                shutil.rmtree(target_dir)  # Clean previous merge

            shutil.copytree(
                str(cd_local_path), str(target_dir),
                ignore=shutil.ignore_patterns('.git'),
                dirs_exist_ok=True,
            )

            cd_file_count = sum(1 for f in target_dir.rglob("*") if f.is_file())
            logger.info("✅ Merged %d CD companion files into %s", cd_file_count, target_dir)
            return cd_url

        except Exception as e:
            logger.info("No CD companion found or clone failed: %s (this is OK)", e)
            return None


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main():
    """Entry point: ``python -m codemind.worker.index_worker``."""
    import os
    poll = int(os.getenv("WORKER_POLL_INTERVAL", "5"))
    base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
    db = os.getenv("CODEMIND_DB_PATH", os.path.join(base_default, "codemind.db"))
    print(f"[CLI] Starting local worker targeting {db} ...")
    worker = IndexWorker(poll_interval=poll, db_path=db)
    worker.run()


if __name__ == "__main__":
    main()
