"""
Manifest manager for repository state persistence.

Provides high-level interface for storing and retrieving indexing state.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from codemind.indexer.models import FileChange

from .database import Database
from .models import FileManifest, RepositoryManifest


class ManifestManager:
    """Manager for repository manifest operations."""

    def __init__(self, db_path: str | Path = "data/codemind.db"):
        """
        Initialize manifest manager.

        Args:
            db_path: Path to SQLite database
        """
        self.db = Database(db_path)
        self.db.init_db()

    def _compute_repo_id(self, repo_path: str) -> str:
        """
        Compute unique repository ID from path.

        Args:
            repo_path: Path to repository

        Returns:
            Unique repository ID (hash of absolute path)
        """
        abs_path = Path(repo_path).resolve()
        return hashlib.sha256(str(abs_path).encode()).hexdigest()[:16]

    def get_repository(self, repo_path: str) -> RepositoryManifest | None:
        """
        Get repository manifest by path.

        Args:
            repo_path: Path to repository

        Returns:
            Repository manifest or None if not found
        """
        with self.db.get_session() as session:
            return (
                session.query(RepositoryManifest)
                .filter_by(repo_path=str(Path(repo_path).resolve()))
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

    def create_repository(
        self,
        repo_path: str,
        embedding_model: str = "all-MiniLM-L6-v2",
        embedding_version: int = 1,
    ) -> RepositoryManifest:
        """
        Create new repository manifest.

        Args:
            repo_path: Path to repository
            embedding_model: Name of embedding model
            embedding_version: Version of embeddings

        Returns:
            Created repository manifest
        """
        repo_id = self._compute_repo_id(repo_path)
        abs_path = str(Path(repo_path).resolve())

        with self.db.get_session() as session:
            repo = RepositoryManifest(
                repo_path=abs_path,
                repo_id=repo_id,
                last_indexed_at=datetime.now(UTC),
                embedding_model=embedding_model,
                embedding_version=embedding_version,
            )
            session.add(repo)
            session.commit()
            session.refresh(repo)
            return repo

            session.refresh(repo)
            return repo

    def list_repositories(self) -> list[RepositoryManifest]:
        """
        List all repository manifests.

        Returns:
            List of repository manifests
        """
        with self.db.get_session() as session:
            return session.query(RepositoryManifest).all()

    def update_repository(
        self,
        repo_id: str,
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

            repo.last_indexed_at = datetime.now(UTC)
            repo.updated_at = datetime.now(UTC)

            session.commit()
            session.refresh(repo)
            return repo

    def get_file_hashes(self, repo_id: str) -> dict[str, str]:
        """
        Get all active file hashes for repository.

        Args:
            repo_id: Repository ID

        Returns:
            Dictionary mapping file paths to content hashes
        """
        with self.db.get_session() as session:
            files = session.query(FileManifest).filter_by(repo_id=repo_id, is_active=True).all()
            return {f.file_path: f.content_hash for f in files}

    def update_files(
        self,
        repo_id: str,
        changed_files: list[FileChange],
        deleted_files: list[str],
    ) -> None:
        """
        Update file manifests for repository.

        Args:
            repo_id: Repository ID
            changed_files: List of changed files
            deleted_files: List of deleted file paths
        """
        with self.db.get_session() as session:
            now = datetime.now(UTC)

            # Mark deleted files as inactive
            if deleted_files:
                session.query(FileManifest).filter(
                    FileManifest.repo_id == repo_id,
                    FileManifest.file_path.in_(deleted_files),
                ).update({"is_active": False}, synchronize_session=False)

            # Update or create changed files
            for file_change in changed_files:
                existing = (
                    session.query(FileManifest)
                    .filter_by(repo_id=repo_id, file_path=file_change.path)
                    .first()
                )

                if existing:
                    # Update existing
                    existing.content_hash = file_change.content_hash
                    existing.size_bytes = file_change.size_bytes
                    existing.last_indexed_at = now
                    existing.is_active = True
                else:
                    # Create new
                    new_file = FileManifest(
                        repo_id=repo_id,
                        file_path=file_change.path,
                        content_hash=file_change.content_hash,
                        size_bytes=file_change.size_bytes,
                        last_indexed_at=now,
                        is_active=True,
                    )
                    session.add(new_file)

            session.commit()

            # Update repository total files count
            total_active = (
                session.query(FileManifest).filter_by(repo_id=repo_id, is_active=True).count()
            )
            repo = session.query(RepositoryManifest).filter_by(repo_id=repo_id).first()
            if repo:
                repo.total_files_indexed = total_active
                repo.updated_at = now
                session.commit()

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
