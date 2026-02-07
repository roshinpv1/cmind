"""
Kùzu graph database integration for code relationships.

Persistent graph storage using Kùzu embedded graph database.
"""

from pathlib import Path

import kuzu


class KuzuGraphDB:
    """Kùzu-based graph database for code relationships."""

    def __init__(self, db_path: str | Path = "data/kuzu_graph"):
        """
        Initialize Kùzu database.

        Args:
            db_path: Path to Kùzu database directory
        """
        self.db_path = Path(db_path)
        # Ensure parent directory exists (data/), but let Kùzu create kuzu_graph/
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to database (Kùzu creates the directory itself)
        self.db = kuzu.Database(str(self.db_path))
        self.conn = kuzu.Connection(self.db)

        # Initialize schema
        self._init_schema()

    def _init_schema(self):
        """Create node and relationship tables if they don't exist."""
        # Create node tables
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Repository(
                repo_id STRING,
                path STRING,
                PRIMARY KEY (repo_id)
            )
            """)

        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS File(
                file_id STRING,
                repo_id STRING,
                path STRING,
                PRIMARY KEY (file_id)
            )
            """)

        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Class(
                class_id STRING,
                repo_id STRING,
                file_path STRING,
                name STRING,
                PRIMARY KEY (class_id)
            )
            """)

        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Function(
                func_id STRING,
                repo_id STRING,
                file_path STRING,
                name STRING,
                parent_class STRING,
                PRIMARY KEY (func_id)
            )
            """)

        # Create relationship tables
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS CONTAINS(
                FROM Repository TO File
            )
            """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS DECLARES_CLASS(
                FROM File TO Class
            )
            """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS DECLARES_FUNCTION(
                FROM File TO Function
            )
            """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS HAS_METHOD(
                FROM Class TO Function
            )
            """)

    def add_repository(self, repo_id: str, path: str):
        """Add repository node (idempotent)."""
        self.conn.execute(
            """
            MERGE (r:Repository {repo_id: $repo_id})
            ON CREATE SET r.path = $path
            ON MATCH SET r.path = $path
            """,
            {"repo_id": repo_id, "path": path},
        )

    def add_file(self, repo_id: str, file_path: str):
        """Add file node and link to repository."""
        file_id = f"{repo_id}:{file_path}"

        # Add file node
        self.conn.execute(
            """
            MERGE (f:File {file_id: $file_id})
            ON CREATE SET f.repo_id = $repo_id, f.path = $path
            ON MATCH SET f.repo_id = $repo_id, f.path = $path
            """,
            {"file_id": file_id, "repo_id": repo_id, "path": file_path},
        )

        # Link to repository
        self.conn.execute(
            """
            MATCH (r:Repository {repo_id: $repo_id})
            MATCH (f:File {file_id: $file_id})
            MERGE (r)-[c:CONTAINS]->(f)
            """,
            {"repo_id": repo_id, "file_id": file_id},
        )

    def add_class(self, repo_id: str, file_path: str, class_name: str):
        """Add class node and link to file."""
        class_id = f"{repo_id}:{file_path}:{class_name}"
        file_id = f"{repo_id}:{file_path}"

        # Add class node
        self.conn.execute(
            """
            MERGE (c:Class {class_id: $class_id})
            ON CREATE SET c.repo_id = $repo_id, c.file_path = $file_path, c.name = $name
            ON MATCH SET c.repo_id = $repo_id, c.file_path = $file_path, c.name = $name
            """,
            {
                "class_id": class_id,
                "repo_id": repo_id,
                "file_path": file_path,
                "name": class_name,
            },
        )

        # Link to file
        self.conn.execute(
            """
            MATCH (f:File {file_id: $file_id})
            MATCH (c:Class {class_id: $class_id})
            MERGE (f)-[d:DECLARES_CLASS]->(c)
            """,
            {"file_id": file_id, "class_id": class_id},
        )

    def add_function(
        self, repo_id: str, file_path: str, func_name: str, parent_class: str | None = None
    ):
        """Add function node and link to file or class."""
        func_id = f"{repo_id}:{file_path}:{func_name}"
        file_id = f"{repo_id}:{file_path}"

        # Add function node
        self.conn.execute(
            """
            MERGE (f:Function {func_id: $func_id})
            ON CREATE SET f.repo_id = $repo_id, f.file_path = $file_path,
                         f.name = $name, f.parent_class = $parent_class
            ON MATCH SET f.repo_id = $repo_id, f.file_path = $file_path,
                        f.name = $name, f.parent_class = $parent_class
            """,
            {
                "func_id": func_id,
                "repo_id": repo_id,
                "file_path": file_path,
                "name": func_name,
                "parent_class": parent_class or "",
            },
        )

        if parent_class:
            # Link to class
            class_id = f"{repo_id}:{file_path}:{parent_class}"
            self.conn.execute(
                """
                MATCH (c:Class {class_id: $class_id})
                MATCH (f:Function {func_id: $func_id})
                MERGE (c)-[m:HAS_METHOD]->(f)
                """,
                {"class_id": class_id, "func_id": func_id},
            )
        else:
            # Link to file
            self.conn.execute(
                """
                MATCH (file:File {file_id: $file_id})
                MATCH (func:Function {func_id: $func_id})
                MERGE (file)-[d:DECLARES_FUNCTION]->(func)
                """,
                {"file_id": file_id, "func_id": func_id},
            )

    def query(self, cypher: str, params: dict | None = None):
        """Execute Cypher query."""
        return self.conn.execute(cypher, params or {})

    def get_file_classes(self, repo_id: str, file_path: str) -> list[dict]:
        """Get all classes declared in a file."""
        file_id = f"{repo_id}:{file_path}"
        result = self.conn.execute(
            """
            MATCH (f:File {file_id: $file_id})-[:DECLARES_CLASS]->(c:Class)
            RETURN c.name AS name, c.class_id AS id
            """,
            {"file_id": file_id},
        )
        # Convert Kùzu result to list of dicts
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append({"name": row[0], "id": row[1]})
        return rows

    def get_class_methods(self, repo_id: str, file_path: str, class_name: str) -> list[dict]:
        """Get all methods of a class."""
        class_id = f"{repo_id}:{file_path}:{class_name}"
        result = self.conn.execute(
            """
            MATCH (c:Class {class_id: $class_id})-[:HAS_METHOD]->(f:Function)
            RETURN f.name AS name, f.func_id AS id
            """,
            {"class_id": class_id},
        )
        # Convert Kùzu result to list of dicts
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append({"name": row[0], "id": row[1]})
        return rows

    def close(self):
        """Close database connection."""
        # Kùzu connections auto-close, but we can explicitly do it
        pass
