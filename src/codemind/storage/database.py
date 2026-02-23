"""
Database setup and session management.

Provides SQLAlchemy engine and session configuration.
"""

from pathlib import Path


from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, text
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship, sessionmaker

Base = declarative_base()


class GraphNode(Base):
    """Node in the code knowledge graph."""

    __tablename__ = "graph_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "file:/path/to/main.py"
    type: Mapped[str] = mapped_column(String, index=True)  # file, class, function
    repo_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)  # Human readable name
    file_path: Mapped[str] = mapped_column(String, index=True)
    start_line: Mapped[int] = mapped_column(Integer, default=0)
    end_line: Mapped[int] = mapped_column(Integer, default=0)
    properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationships
    outgoing_edges = relationship(
        "GraphEdge",
        foreign_keys="GraphEdge.source_id",
        back_populates="source_node",
        cascade="all, delete-orphan",
    )
    incoming_edges = relationship(
        "GraphEdge",
        foreign_keys="GraphEdge.target_id",
        back_populates="target_node",
        cascade="all, delete-orphan",
    )


class GraphEdge(Base):
    """Edge in the code knowledge graph."""

    __tablename__ = "graph_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String, index=True)  # calls, imports, defines
    properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Unique constraint to prevent duplicate edges
    __table_args__ = (UniqueConstraint("source_id", "target_id", "type", name="uq_edge_source_target_type"),)

    # Relationships
    source_node = relationship("GraphNode", foreign_keys=[source_id], back_populates="outgoing_edges")
    target_node = relationship("GraphNode", foreign_keys=[target_id], back_populates="incoming_edges")
    target_node = relationship("GraphNode", foreign_keys=[target_id], back_populates="incoming_edges")


class CatalogStore(Base):
    """
    Store full catalog entries for retrieval.
    Decoupled from vector store to avoid context window limits.
    """
    __tablename__ = "catalog_store"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    repo_name: Mapped[str | None] = mapped_column(String, nullable=True)
    org: Mapped[str | None] = mapped_column(String, nullable=True)  # Organization owning this component
    content: Mapped[str] = mapped_column(Text)  # Full JSON/Markdown content
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Structured metadata
    
    created_at: Mapped[int] = mapped_column(Integer, default=0) # timestamp
    updated_at: Mapped[int] = mapped_column(Integer, default=0)

class Database:
    """Database connection manager."""

    def __init__(self, db_path: str | Path = "data/codemind.db"):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
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
        """Create all tables if they don't exist."""
        # Import models here to ensure they are registered with Base metadata if defined elsewhere
        # (Though currently defined above)
        Base.metadata.create_all(bind=self.engine)

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
