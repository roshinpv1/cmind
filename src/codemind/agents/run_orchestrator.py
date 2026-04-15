"""
Persistent run orchestration for autonomous agent executions.

Provides:
- durable run status tracking
- per-iteration step tracking
- point-in-time checkpoints for reruns
- mirror workspace allocation for safe code generation
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from codemind.storage.database import AgentCheckpoint, AgentRun, AgentStep
from codemind.storage.models import RepositoryManifest


class RunOrchestrator:
    """Manage durable autonomous runs and checkpoints."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _parse_iso(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw)
        except Exception:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    def _mirror_base(self) -> Path:
        base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
        mirror_base = os.getenv("CODEMIND_AGENT_MIRROR_PATH", os.path.join(base_default, "agent-mirrors"))
        p = Path(mirror_base).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _resolve_repo_root(self, repo_id: str | None) -> Path | None:
        if not repo_id:
            return None
        with self.db.get_session() as session:
            repo = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()
            if repo and repo.repo_path:
                return Path(repo.repo_path).resolve()
        return None

    def create_run(
        self,
        goal: str,
        repo_id: str | None,
        *,
        parent_run_id: str | None = None,
        rerun_from_checkpoint: str | None = None,
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = self._now_iso()
        mirror_root = self._mirror_base() / run_id
        mirror_root.mkdir(parents=True, exist_ok=True)

        repo_root = self._resolve_repo_root(repo_id)
        if repo_root:
            # Keep source metadata and allocate a repo mirror root. We intentionally
            # do not mutate/copy source eagerly; generated files are projected here.
            repo_mirror = mirror_root / "repo"
            repo_mirror.mkdir(parents=True, exist_ok=True)
            (mirror_root / ".source_repo_path").write_text(str(repo_root), encoding="utf-8")
        else:
            repo_mirror = mirror_root / "repo"
            repo_mirror.mkdir(parents=True, exist_ok=True)

        with self.db.get_session() as session:
            run = AgentRun(
                run_id=run_id,
                parent_run_id=parent_run_id,
                rerun_from_checkpoint=rerun_from_checkpoint,
                goal=goal,
                repo_id=repo_id,
                status="pending",
                mirror_root=str(repo_mirror),
                created_at=now,
                updated_at=now,
            )
            session.add(run)
            session.commit()

        return {
            "run_id": run_id,
            "status": "pending",
            "created_at": now,
            "mirror_root": str(repo_mirror),
        }

    def mark_running(self, run_id: str) -> None:
        now = self._now_iso()
        with self.db.get_session() as session:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if not run:
                return
            run.status = "running"
            run.updated_at = now
            run.completed_at = None
            session.commit()

    def upsert_step(self, run_id: str, step_index: int, status: str, payload: dict | None = None, error: str | None = None) -> None:
        now = self._now_iso()
        with self.db.get_session() as session:
            step = session.query(AgentStep).filter_by(run_id=run_id, step_index=step_index).first()
            if not step:
                step = AgentStep(
                    run_id=run_id,
                    step_index=step_index,
                    step_name="iteration",
                    status=status,
                    created_at=now,
                    updated_at=now,
                    output_json=payload,
                    error=error,
                )
                session.add(step)
            else:
                step.status = status
                step.updated_at = now
                if payload is not None:
                    step.output_json = payload
                if error is not None:
                    step.error = error
            session.commit()

    def save_checkpoint(self, run_id: str, checkpoint_key: str, step_index: int, state_json: dict | None) -> None:
        now = self._now_iso()
        with self.db.get_session() as session:
            ckpt = session.query(AgentCheckpoint).filter_by(run_id=run_id, checkpoint_key=checkpoint_key).first()
            if not ckpt:
                ckpt = AgentCheckpoint(
                    run_id=run_id,
                    checkpoint_key=checkpoint_key,
                    step_index=step_index,
                    created_at=now,
                    state_json=state_json or {},
                )
                session.add(ckpt)
            else:
                ckpt.step_index = step_index
                ckpt.state_json = state_json or {}
            session.commit()

    def mark_completed(self, run_id: str, result: dict | None, iterations: int, steps_taken: int) -> None:
        now = self._now_iso()
        with self.db.get_session() as session:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if not run:
                return
            run.status = "completed"
            run.updated_at = now
            run.completed_at = now
            run.result_json = result or {}
            run.iterations = iterations
            run.steps_taken = steps_taken
            session.commit()

    def update_runtime_state(
        self,
        run_id: str,
        *,
        iterations: int | None = None,
        steps_taken: int | None = None,
        generated_files: list[str] | None = None,
    ) -> None:
        """Persist in-flight run state so continuation can resume from DB context."""
        now = self._now_iso()
        with self.db.get_session() as session:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if not run:
                return
            if iterations is not None:
                run.iterations = int(iterations)
            if steps_taken is not None:
                run.steps_taken = int(steps_taken)
            if generated_files is not None:
                base = dict(run.result_json or {})
                deduped = list(dict.fromkeys(str(p) for p in generated_files if p))
                base["generated_files"] = deduped
                run.result_json = base
            run.updated_at = now
            session.commit()

    def mark_failed(self, run_id: str, error: str) -> None:
        now = self._now_iso()
        with self.db.get_session() as session:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if not run:
                return
            run.status = "failed"
            run.updated_at = now
            run.completed_at = now
            run.error = error
            session.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.get_session() as session:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if not run:
                return None
            return {
                "run_id": run.run_id,
                "parent_run_id": run.parent_run_id,
                "rerun_from_checkpoint": run.rerun_from_checkpoint,
                "goal": run.goal,
                "repo_id": run.repo_id,
                "status": run.status,
                "mirror_root": run.mirror_root,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
                "completed_at": run.completed_at,
                "error": run.error,
                "iterations": run.iterations,
                "steps_taken": run.steps_taken,
                "result": run.result_json,
            }

    def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.get_session() as session:
            rows = (
                session.query(AgentCheckpoint)
                .filter_by(run_id=run_id)
                .order_by(AgentCheckpoint.step_index.asc())
                .all()
            )
            return [
                {
                    "checkpoint_key": r.checkpoint_key,
                    "step_index": r.step_index,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    def get_latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        """Return most recent checkpoint for a run."""
        with self.db.get_session() as session:
            row = (
                session.query(AgentCheckpoint)
                .filter_by(run_id=run_id)
                .order_by(AgentCheckpoint.step_index.desc())
                .first()
            )
            if not row:
                return None
            return {
                "checkpoint_key": row.checkpoint_key,
                "step_index": row.step_index,
                "created_at": row.created_at,
                "state": row.state_json or {},
            }

    def get_checkpoint(self, run_id: str, checkpoint_key: str) -> dict[str, Any] | None:
        with self.db.get_session() as session:
            row = session.query(AgentCheckpoint).filter_by(run_id=run_id, checkpoint_key=checkpoint_key).first()
            if not row:
                return None
            return {
                "checkpoint_key": row.checkpoint_key,
                "step_index": row.step_index,
                "created_at": row.created_at,
                "state": row.state_json or {},
            }

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent autonomous runs for continuation UX."""
        cap = max(1, min(int(limit), 200))
        with self.db.get_session() as session:
            rows = (
                session.query(AgentRun)
                .order_by(AgentRun.updated_at.desc())
                .limit(cap)
                .all()
            )
            out: list[dict[str, Any]] = []
            for r in rows:
                result = r.result_json if isinstance(r.result_json, dict) else {}
                out.append(
                    {
                        "run_id": r.run_id,
                        "goal": r.goal,
                        "repo_id": r.repo_id,
                        "status": r.status,
                        "created_at": r.created_at,
                        "updated_at": r.updated_at,
                        "iterations": r.iterations,
                        "steps_taken": r.steps_taken,
                        "mirror_root": r.mirror_root,
                        "generated_files_count": len(result.get("generated_files", []) or []),
                    }
                )
            return out

    def cleanup_expired_runs(self, retention_days: int = 14) -> dict[str, int]:
        """
        Delete old terminal runs/checkpoints/steps and mirror directories.

        Only terminal runs (completed/failed) older than retention are removed.
        """
        days = max(1, int(retention_days))
        cutoff = datetime.now(UTC) - timedelta(days=days)
        removed_runs = 0
        removed_steps = 0
        removed_checkpoints = 0
        removed_mirror_dirs = 0

        with self.db.get_session() as session:
            rows = session.query(AgentRun).filter(AgentRun.status.in_(["completed", "failed"])).all()
            stale_run_ids: list[str] = []
            stale_mirrors: list[str] = []
            for run in rows:
                ts = self._parse_iso(run.updated_at) or self._parse_iso(run.created_at)
                if ts and ts < cutoff:
                    stale_run_ids.append(run.run_id)
                    if run.mirror_root:
                        stale_mirrors.append(run.mirror_root)

            if stale_run_ids:
                removed_steps = (
                    session.query(AgentStep)
                    .filter(AgentStep.run_id.in_(stale_run_ids))
                    .delete(synchronize_session=False)
                )
                removed_checkpoints = (
                    session.query(AgentCheckpoint)
                    .filter(AgentCheckpoint.run_id.in_(stale_run_ids))
                    .delete(synchronize_session=False)
                )
                removed_runs = (
                    session.query(AgentRun)
                    .filter(AgentRun.run_id.in_(stale_run_ids))
                    .delete(synchronize_session=False)
                )
                session.commit()

        for root in stale_mirrors:
            try:
                p = Path(root).resolve()
                if p.exists():
                    # mirror_root points to ".../<run_id>/repo", clean whole run bucket.
                    run_bucket = p.parent
                    if run_bucket.exists():
                        shutil.rmtree(run_bucket, ignore_errors=True)
                        removed_mirror_dirs += 1
            except Exception:
                continue

        return {
            "removed_runs": int(removed_runs or 0),
            "removed_steps": int(removed_steps or 0),
            "removed_checkpoints": int(removed_checkpoints or 0),
            "removed_mirror_dirs": removed_mirror_dirs,
        }
