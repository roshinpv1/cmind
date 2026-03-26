"""
Job management for async indexing tasks.

Tracks job status, progress, and errors.
"""

import os
import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from codemind.storage.database import Base, Database
from codemind.storage.db_factory import get_database


class JobStatus(str, Enum):
    """Job status enum."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobModel(Base):
    """Job persistence model."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_path: Mapped[str] = mapped_column(String, index=True)
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)  # Git URL (if remote)
    branch: Mapped[str] = mapped_column(String, default="main")
    repo_id: Mapped[str | None] = mapped_column(String, nullable=True)  # Computed repo ID
    org: Mapped[str | None] = mapped_column(String, nullable=True)  # Organization owning this component
    status: Mapped[JobStatus] = mapped_column(
        SQLEnum(JobStatus), default=JobStatus.PENDING, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True) # Initiator
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class JobManager:
    """Manages indexing jobs."""

    def __init__(self, db_path: str | None = None):
        """Initialize job manager connected to SQLite."""
        if db_path is None:
            import os
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_DB_PATH", os.path.join(base_default, "codemind.db"))
        self.db = get_database(db_path)
        self.db.init_db()


    def create_job(
        self,
        repo_path: str,
        repo_url: str | None = None,
        branch: str = "main",
        repo_id: str | None = None,
        org: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Create new indexing job."""
        job_id = str(uuid.uuid4())

        with self.db.get_session() as session:
            job = JobModel(
                id=job_id,
                repo_path=repo_path,
                repo_url=repo_url,
                branch=branch,
                repo_id=repo_id,
                org=org,
                user_id=user_id,
                status=JobStatus.PENDING,
            )
            session.add(job)
            session.commit()

        return job_id

    def get_job(self, job_id: str) -> JobModel | None:
        """Get job by ID."""
        with self.db.get_session() as session:
            return session.query(JobModel).filter_by(id=job_id).first()

    def update_job(
        self,
        job_id: str,
        status: JobStatus | None = None,
        stage: str | None = None,
        progress: int | None = None,
        error: str | None = None,
        repo_path: str | None = None,
    ):
        """Update job status."""
        with self.db.get_session() as session:
            job = session.query(JobModel).filter_by(id=job_id).first()
            if not job:
                return

            if status:
                job.status = status
                if status == JobStatus.COMPLETED:
                    job.completed_at = datetime.now(UTC)
            if stage is not None:
                job.stage = stage
            if progress is not None:
                job.progress = progress
            if error is not None:
                job.error = error
            if repo_path is not None:
                job.repo_path = repo_path

            job.updated_at = datetime.now(UTC)
            session.commit()

    def list_jobs(
        self, 
        repo_path: str | None = None, 
        status: JobStatus | None = None,
        user_id: str | None = None
    ) -> list[JobModel]:
        """List jobs with optional filters."""
        with self.db.get_session() as session:
            query = session.query(JobModel)

            if repo_path:
                query = query.filter_by(repo_path=repo_path)
            if status:
                query = query.filter_by(status=status)
            if user_id:
                query = query.filter_by(user_id=user_id)

            return query.order_by(JobModel.created_at.desc()).all()
