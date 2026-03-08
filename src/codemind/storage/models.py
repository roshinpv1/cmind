"""
SQLAlchemy models for manifest persistence.

Stores repository and file indexing state.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class RepositoryManifest(Base):
    """Repository indexing manifest."""

    __tablename__ = "repository_manifests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_path: Mapped[str] = mapped_column(String, unique=True, index=True)
    repo_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    branch: Mapped[str | None] = mapped_column(String, nullable=True)  # New field for uniqueness
    org: Mapped[str | None] = mapped_column(String, nullable=True)  # Organization owning this component
    language: Mapped[str | None] = mapped_column(String, nullable=True)  # Primary language
    framework: Mapped[str | None] = mapped_column(String, nullable=True)  # Primary framework
    size: Mapped[int] = mapped_column(Integer, default=0)  # Total repo size in bytes
    last_indexed_at: Mapped[datetime] = mapped_column(DateTime)
    last_commit_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String, default="all-MiniLM-L6-v2")
    embedding_version: Mapped[int] = mapped_column(Integer, default=1)
    total_files_indexed: Mapped[int] = mapped_column(Integer, default=0)
    
    # New metadata fields
    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_author: Mapped[str | None] = mapped_column(String, nullable=True)
    last_authors: Mapped[str | None] = mapped_column(String, nullable=True, comment="JSON list of last 4 authors")
    total_commits: Mapped[int] = mapped_column(Integer, default=0)
    
    # GitHub PR Metadata
    last_pr_title: Mapped[str | None] = mapped_column(String, nullable=True)
    last_pr_user: Mapped[str | None] = mapped_column(String, nullable=True)
    last_pr_merged_at: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # CD companion repo
    cd_repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Contributors JSON: [{"name": "...", "commits": N}, ...]
    contributors: Mapped[str | None] = mapped_column(String, nullable=True, comment="JSON list of contributors")
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<RepositoryManifest(repo_id={self.repo_id}, path={self.repo_path})>"

