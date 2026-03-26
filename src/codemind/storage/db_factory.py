import os
from contextlib import contextmanager

from .database import Database
from .mongo_backend import MongoDatabase

def get_database(db_path: str = None):
    """
    Factory function to get the appropriate database backend.
    
    Reads DB_BACKEND from environment:
    - sqlite (default): Returns standard SQLAlchemy Database
    - mongodb: Returns MongoDatabase
    """
    backend = os.getenv("DB_BACKEND", "sqlite").lower()
    
    if backend == "mongodb":
        # MongoDatabase handles its own connection string via env vars
        return MongoDatabase()
    else:
        # Default SQLite backend
        if db_path is None:
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_DB_PATH", os.path.join(base_default, "codemind.db"))
        return Database(db_path)
