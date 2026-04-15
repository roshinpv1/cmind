"""
Database setup and session management.

Provides SQLAlchemy engine and session configuration.
Code-structure graphs use Graphify (see codemind.graph); this file handles relational metadata only.
"""

from pathlib import Path


from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint, create_engine, event, text
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, sessionmaker

Base = declarative_base()


class IndexRun(Base):
    """Tracks each indexing job's execution and stats."""

    __tablename__ = "index_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)  # Same as job_id
    repo_id: Mapped[str] = mapped_column(String, index=True)
    commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    branch: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[str] = mapped_column(String)  # ISO datetime
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")  # running|completed|failed
    files_indexed: Mapped[int] = mapped_column(Integer, default=0)
    symbols_extracted: Mapped[int] = mapped_column(Integer, default=0)
    chunks_created: Mapped[int] = mapped_column(Integer, default=0)
    embeddings_generated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CommitSnapshot(Base):
    """Tracks commit history for each repository."""

    __tablename__ = "commit_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[str] = mapped_column(String, index=True)
    commit_sha: Mapped[str] = mapped_column(String)
    parent_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    indexed_at: Mapped[str] = mapped_column(String)  # ISO datetime
    files_changed: Mapped[int] = mapped_column(Integer, default=0)

class UserRecord(Base):
    """Enterprise user profile (synced from SSO)."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)  # SSO sub/id
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, default="user")  # admin|user
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    last_login: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=0)

class CatalogStore(Base):
    """
    Store full catalog entries for retrieval.
    Decoupled from vector store to avoid context window limits.
    Supports lifecycle: proposed → qualified → active
    """
    __tablename__ = "catalog_store"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    repo_name: Mapped[str | None] = mapped_column(String, nullable=True)
    org: Mapped[str | None] = mapped_column(String, nullable=True)  # Organization owning this component
    content: Mapped[str] = mapped_column(Text)  # Full JSON/Markdown content
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Structured metadata
    
    # Proposal lifecycle
    status: Mapped[str] = mapped_column(String, default="active", index=True)  # proposed | qualified | active
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)  # Legacy string name
    created_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True) # Linked to UserRecord.user_id
    source_gap: Mapped[str | None] = mapped_column(String, nullable=True)  # Original gap name from analysis
    source_analysis_id: Mapped[str | None] = mapped_column(String, nullable=True)  # Links to analysis job
    requirements: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Auto-generated requirements
    git_url: Mapped[str | None] = mapped_column(String, nullable=True)  # Git repo URL (for promotion)
    git_branch: Mapped[str | None] = mapped_column(String, nullable=True)  # Git branch
    quality_score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100, computed during qualification
    
    # Popularity tracking
    search_count: Mapped[int] = mapped_column(Integer, default=0)  # Times appeared in search results
    view_count: Mapped[int] = mapped_column(Integer, default=0)  # Times explicitly viewed/clicked
    popularity_points: Mapped[int] = mapped_column(Integer, default=0)  # Weighted score: search=+1, view=+5
    likes_count: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[int] = mapped_column(Integer, default=0) # timestamp
    updated_at: Mapped[int] = mapped_column(Integer, default=0)


class PlaybookStoreModel(Base):
    """
    Persisted playbooks — user-created, imported, or installed from the store.
    Built-in playbooks live in playbooks/*.md but can be overridden here.
    """
    __tablename__ = "playbook_store"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # uuid
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    version: Mapped[str] = mapped_column(String, default="1.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    when_to_use: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String, default="analysis")  # analysis|generation|evaluation|exploration
    complexity: Mapped[str] = mapped_column(String, default="medium")
    author: Mapped[str] = mapped_column(String, default="user") # Legacy string name
    author_user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True) # Linked to UserRecord.user_id
    is_builtin: Mapped[int] = mapped_column(Integer, default=0)
    is_published: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    icon: Mapped[str] = mapped_column(String, default="Brain")
    color: Mapped[str] = mapped_column(String, default="violet")

    # Playbook content
    system_prompt: Mapped[str] = mapped_column(Text, default="You are a helpful coding assistant.")
    search_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    output_schema: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON  
    behavior: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    examples: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    anti_patterns: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    quality_rubric: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    evaluation_rules: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    templates: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON [{label, prompt}]
    requires_repo: Mapped[int] = mapped_column(Integer, default=1)

    # Marketplace metadata
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[float | None] = mapped_column(nullable=True)
    likes_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[int] = mapped_column(Integer, default=0)


class AgentRun(Base):
    """Persistent autonomous agent run record."""

    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    rerun_from_checkpoint: Mapped[str | None] = mapped_column(String, nullable=True)
    goal: Mapped[str] = mapped_column(Text)
    repo_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)  # pending|running|completed|failed
    mirror_root: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)  # ISO datetime
    updated_at: Mapped[str] = mapped_column(String)  # ISO datetime
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    steps_taken: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AgentStep(Base):
    """Per-step status for a run (planner iteration/tool phase)."""

    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    step_index: Mapped[int] = mapped_column(Integer, index=True)
    step_name: Mapped[str] = mapped_column(String, default="iteration")
    status: Mapped[str] = mapped_column(String, default="running", index=True)  # running|completed|failed
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentCheckpoint(Base):
    """Serialized point-in-time checkpoint for reruns/resume."""

    __tablename__ = "agent_checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "checkpoint_key", name="uq_agent_checkpoints_run_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    checkpoint_key: Mapped[str] = mapped_column(String, index=True)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class Database:
    """Database connection manager."""

    def __init__(self, db_path: str | Path | None = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        if db_path is None:
            import os
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_DB_PATH", os.path.join(base_default, "codemind.db"))

        # Ensure data directory exists
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create engine with proper settings
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},  # Allow multi-threading
            echo=False,  # Set to True for SQL logging
        )

        # Enable WAL mode for concurrent access from multiple processes
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s on lock
            cursor.execute("PRAGMA foreign_keys=ON")  # Enforce foreign keys
            cursor.close()

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def init_db(self):
        """Create all tables if they don't exist, then migrate any missing columns."""
        Base.metadata.create_all(bind=self.engine)
        self._migrate_schema()

    def _migrate_schema(self):
        """
        Add any columns that exist in the ORM models but are missing from the
        on-disk SQLite tables (i.e. databases created before a column was added).

        SQLite supports ALTER TABLE … ADD COLUMN but not DROP/RENAME, so this
        is safe to run on every startup — it is a no-op when the schema is current.
        """
        with self.engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                # Fetch current columns from the live table
                result = conn.execute(text(f"PRAGMA table_info({table.name})"))
                existing_columns = {row[1] for row in result}  # row[1] = column name

                for column in table.columns:
                    if column.name not in existing_columns:
                        # Build a minimal column definition for ALTER TABLE
                        col_type = column.type.compile(dialect=self.engine.dialect)
                        nullable = "" if not column.nullable else ""
                        default_clause = ""
                        if column.default is not None and column.default.is_scalar:
                            val = column.default.arg
                            if isinstance(val, str):
                                default_clause = f" DEFAULT '{val}'"
                            elif val is None:
                                default_clause = " DEFAULT NULL"
                            else:
                                default_clause = f" DEFAULT {val}"
                        elif column.nullable:
                            default_clause = " DEFAULT NULL"

                        ddl = (
                            f"ALTER TABLE {table.name} "
                            f"ADD COLUMN {column.name} {col_type}{default_clause}"
                        )
                        print(f"[DB MIGRATE] Adding missing column: {table.name}.{column.name}")
                        conn.execute(text(ddl))
            conn.commit()

    def get_session(self):
        """
        Get database session.

        Returns:
            SQLAlchemy session
        """
        return self.SessionLocal()

    def drop_all(self):
        """Drop all tables (for testing)."""
        Base.metadata.drop_all(bind=self.engine)
