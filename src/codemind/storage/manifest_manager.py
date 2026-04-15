"""
Manifest manager for repository state persistence.

Provides high-level interface for storing and retrieving indexing state.
"""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from codemind.indexer.models import FileChange

from .database import CommitSnapshot, Database, IndexRun
from .db_factory import get_database
from .models import RepositoryManifest


class ManifestManager:
    """Manager for repository manifest operations."""

    def __init__(self, db_path: str | Path | None = None):
        """
        Initialize manifest manager.

        Args:
            db_path: Path to SQLite database
        """
        if db_path is None:
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_DB_PATH", os.path.join(base_default, "codemind.db"))
            
        self.db = get_database(db_path)
        self.db.init_db()

    def _compute_repo_id(self, repo_path: str, branch: str = "main") -> str:
        """
        Compute unique repository ID from path and branch.

        Args:
            repo_path: Path to repository
            branch: Branch name to ensure isolation across branches

        Returns:
            Unique repository ID (hash of absolute path + branch)
        """
        abs_path = Path(repo_path).resolve()
        unique_string = f"{abs_path}::{branch}"
        return hashlib.sha256(unique_string.encode()).hexdigest()[:16]

    def get_repository(self, repo_path: str, branch: str = "main") -> RepositoryManifest | None:
        """
        Get repository manifest by path and branch.

        Args:
            repo_path: Path to repository
            branch: Repository branch

        Returns:
            Repository manifest or None if not found
        """
        with self.db.get_session() as session:
            return (
                session.query(RepositoryManifest)
                .filter_by(repo_path=str(Path(repo_path).resolve()), branch=branch)
                .first()
            )

    def get_repository_by_id(self, repo_id: str) -> RepositoryManifest | None:
        """
        Get repository manifest by ID.

        Args:
            repo_id: Repository ID

        Returns:
            Repository manifest or None if not found
        """
        with self.db.get_session() as session:
            return session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()

    def get_repository_by_url_and_branch(
        self, 
        repo_url: str, 
        branch: str = "main"
    ) -> RepositoryManifest | None:
        """
        Get repository manifest by URL and branch.

        Args:
            repo_url: Repository URL
            branch: Repository branch

        Returns:
            Repository manifest or None if not found
        """
        with self.db.get_session() as session:
            # Note: We filter by repo_url and branch
            return (
                session.query(RepositoryManifest)
                .filter(RepositoryManifest.repo_url == repo_url)
                .filter(RepositoryManifest.branch == branch)
                .first()
            )

    def create_repository(
        self,
        repo_path: str,
        repo_id: str | None = None,
        repo_url: str | None = None,
        branch: str = "main",
        org: str | None = None,
        embedding_model: str = "all-MiniLM-L6-v2",
        embedding_version: int = 1,
        user_id: str | None = None,
    ) -> RepositoryManifest:
        """
        Create new repository manifest.

        Args:
            repo_path: Path to repository
            repo_id: Optional explicit repository ID (if not provided, computed from path)
            repo_url: Optional repository URL
            branch: Optional branch name
            org: Optional organization name
            embedding_model: Name of embedding model
            embedding_version: Version of embeddings

        Returns:
            Created repository manifest
        """
        if not repo_id:
            repo_id = self._compute_repo_id(repo_path)
        
        abs_path = str(Path(repo_path).resolve())

        with self.db.get_session() as session:
            repo = RepositoryManifest(
                repo_path=abs_path,
                repo_id=repo_id,
                repo_url=repo_url,
                branch=branch,
                org=org,
                last_indexed_at=datetime.now(UTC),
                embedding_model=embedding_model,
                embedding_version=embedding_version,
                created_by_user_id=user_id,
            )
            session.add(repo)
            session.commit()
            session.refresh(repo)
            return repo

    def list_repositories(self, user_id: str | None = None) -> list[RepositoryManifest]:
        """
        List repository manifests.
        
        Args:
            user_id: Optional user ID to filter by. If None, returns all (for Admin).

        Returns:
            List of repository manifests
        """
        with self.db.get_session() as session:
            query = session.query(RepositoryManifest)
            if user_id:
                # Include repos created by this user OR old repos with no creator
                query = query.filter(
                    (RepositoryManifest.created_by_user_id == user_id) |
                    (RepositoryManifest.created_by_user_id.is_(None))
                )
            return query.all()

    def update_repository(
        self,
        repo_id: str,
        repo_url: str | None = None,
        branch: str | None = None,
        org: str | None = None,
        last_commit_hash: str | None = None,
        total_files: int | None = None,
        embedding_version: int | None = None,
        # New metadata fields
        metadata: dict | None = None,
    ) -> RepositoryManifest | None:
        """
        Update repository manifest.

        Args:
            repo_id: Repository ID
            repo_url: Repository URL
            branch: Repository branch
            last_commit_hash: Latest Git commit hash
            total_files: Total number of indexed files
            embedding_version: Embedding version
            metadata: Dictionary containing first_author, last_authors, etc.

        Returns:
            Updated repository manifest or None if not found
        """
        with self.db.get_session() as session:
            repo = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()

            if not repo:
                return None

            if repo_url is not None:
                repo.repo_url = repo_url
            if branch is not None:
                repo.branch = branch
            if org is not None:
                repo.org = org
            if last_commit_hash is not None:
                repo.last_commit_hash = last_commit_hash
            if total_files is not None:
                repo.total_files_indexed = total_files
            if embedding_version is not None:
                repo.embedding_version = embedding_version
            
            # Update metadata if provided
            if metadata:
                import json
                if "first_commit_at" in metadata:
                    repo.first_commit_at = metadata["first_commit_at"]
                if "first_author" in metadata:
                    repo.first_author = metadata["first_author"]
                if "last_authors" in metadata:
                    # Store list as JSON string
                    repo.last_authors = json.dumps(metadata["last_authors"])
                if "total_commits" in metadata:
                    repo.total_commits = metadata["total_commits"]
                
                # PR Metadata
                if "last_pr_title" in metadata:
                    repo.last_pr_title = metadata["last_pr_title"]
                if "last_pr_user" in metadata:
                    repo.last_pr_user = metadata["last_pr_user"]
                if "last_pr_merged_at" in metadata:
                    repo.last_pr_merged_at = metadata["last_pr_merged_at"]
                
                # CD companion repo
                if "cd_repo_url" in metadata:
                    repo.cd_repo_url = metadata["cd_repo_url"]
                
                # Contributors
                if "contributors" in metadata:
                    repo.contributors = json.dumps(metadata["contributors"])

            repo.last_indexed_at = datetime.now(UTC)
            repo.updated_at = datetime.now(UTC)

            session.commit()
            session.refresh(repo)
            return repo


    def delete_repository(self, repo_id: str) -> bool:
        """
        Delete repository manifest and all associated files.

        Args:
            repo_id: Repository ID

        Returns:
            True if deleted, False if not found
        """
        with self.db.get_session() as session:
            repo = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()

            if not repo:
                return False

            session.delete(repo)
            session.commit()
            return True

    # ── Index Run Operations ─────────────────────────────────────────────

    def create_index_run(
        self, run_id: str, repo_id: str, branch: str | None = None, commit_sha: str | None = None
    ) -> IndexRun:
        """Create a new index run record."""
        with self.db.get_session() as session:
            run = IndexRun(
                run_id=run_id,
                repo_id=repo_id,
                branch=branch,
                commit_sha=commit_sha,
                started_at=datetime.now(UTC).isoformat(),
                status="running",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def complete_index_run(
        self, run_id: str, status: str = "completed", 
        files_indexed: int = 0, symbols_extracted: int = 0,
        chunks_created: int = 0, embeddings_generated: int = 0,
        error: str | None = None,
    ) -> None:
        """Complete an index run with stats."""
        with self.db.get_session() as session:
            run = session.query(IndexRun).filter_by(run_id=run_id).first()
            if run:
                run.completed_at = datetime.now(UTC).isoformat()
                run.status = status
                run.files_indexed = files_indexed
                run.symbols_extracted = symbols_extracted
                run.chunks_created = chunks_created
                run.embeddings_generated = embeddings_generated
                run.error = error
                session.commit()

    def get_index_runs(self, repo_id: str) -> list[IndexRun]:
        """Get all index runs for a repository, newest first."""
        with self.db.get_session() as session:
            return (
                session.query(IndexRun)
                .filter_by(repo_id=repo_id)
                .order_by(IndexRun.started_at.desc())
                .all()
            )

    # ── Commit Snapshot Operations ───────────────────────────────────────

    def save_commit_snapshot(
        self, repo_id: str, commit_sha: str, parent_commit: str | None = None,
        files_changed: int = 0,
    ) -> CommitSnapshot:
        """Save a commit snapshot."""
        with self.db.get_session() as session:
            snapshot = CommitSnapshot(
                repo_id=repo_id,
                commit_sha=commit_sha,
                parent_commit=parent_commit,
                indexed_at=datetime.now(UTC).isoformat(),
                files_changed=files_changed,
            )
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)
            return snapshot

    def get_commit_snapshots(self, repo_id: str) -> list[CommitSnapshot]:
        """Get commit history for a repository."""
        with self.db.get_session() as session:
            return (
                session.query(CommitSnapshot)
                .filter_by(repo_id=repo_id)
                .order_by(CommitSnapshot.indexed_at.desc())
                .all()
            )

