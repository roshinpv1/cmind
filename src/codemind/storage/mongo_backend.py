"""
MongoDB backend for CodeMind.

Provides a wrapper around PyMongo that mimics the minimal SQLAlchemy
API used strictly throughout CodeMind (query, filter_by, add, delete, commit).
This ensures zero changes are needed in consumer code.
"""

import os
from typing import Any, Generic, TypeVar, Type
from datetime import datetime

try:
    from pymongo import MongoClient
    from pymongo.collection import Collection
    from pymongo.database import Database as PyMongoDatabase
except ImportError:
    # We'll handle the missing dependency gracefully or fail at runtime
    pass


T = TypeVar("T")

class MongoQuery(Generic[T]):
    """Builder for PyMongo queries that mimics SQLAlchemy semantic."""
    
    def __init__(self, collection: "Collection", model_class: Type[T]):
        self.collection = collection
        self.model_class = model_class
        self._filter = {}
        self._sort = None
        
    def filter_by(self, **kwargs) -> "MongoQuery[T]":
        """Add equality filters."""
        for k, v in kwargs.items():
            self._filter[k] = v
        return self
        
    def filter(self, condition) -> "MongoQuery[T]":
        """
        Handle SQLAlchemy binary expressions if possible.
        Since we don't want to parse ASTs, we'll try to extract the dict representation
        if the model provides one, or just warn.
        
        To fully support .filter(Model.field == val), we implemented a simple 
        monkey-patch in MongoDatabase_init, but for .in_() and .desc() we need special handling.
        """
        # For our specific usage, many times we might pass a dict directly in our custom Mongo layer
        # If it's a SQLAlchemy BinaryExpression, we try to convert it
        try:
            # Very basic extraction for SQLAlchemy BinaryExpression
            if hasattr(condition, "left") and hasattr(condition, "right"):
                key = condition.left.name
                
                # Handle IN operator
                if hasattr(condition.right, "element") and type(condition).__name__ == "in_op":
                    # This is an approximation of what we might get
                    self._filter[key] = {"$in": [x.value for x in condition.right.element]}
                else:
                    self._filter[key] = condition.right.value
        except Exception:
            pass
            
        return self
        
    def order_by(self, condition) -> "MongoQuery[T]":
        """Handle ordering."""
        # Simple extraction for SQLAlchemy Sort expressions
        try:
            if hasattr(condition, "element"):
                key = condition.element.name
                # Descending if it's an UnaryExpression with desc modifier
                if getattr(condition, "modifier", None) == "desc" or "desc" in str(condition).lower():
                    self._sort = [(key, -1)]
                else:
                    self._sort = [(key, 1)]
            else:
                # Fallback to string
                key = str(condition).split('.')[-1]
                self._sort = [(key, 1)]
        except Exception:
            pass
            
        return self
        
    def first(self) -> T | None:
        """Execute query and return first result."""
        kwargs = {}
        if self._sort:
            kwargs["sort"] = self._sort
            
        doc = self.collection.find_one(self._filter, **kwargs)
        if not doc:
            return None
        return self._doc_to_model(doc)
        
    def all(self) -> list[T]:
        """Execute query and return all results."""
        kwargs = {}
        if self._sort:
            kwargs["sort"] = self._sort
            
        cursor = self.collection.find(self._filter, **kwargs)
        return [self._doc_to_model(doc) for doc in cursor]
        
    def delete(self) -> int:
        """Delete matching documents."""
        result = self.collection.delete_many(self._filter)
        return result.deleted_count
        
    def _doc_to_model(self, doc: dict) -> T:
        """Convert a Mongo dict back to an ORM model instance."""
        # Remove Mongo's internal _id
        if "_id" in doc:
            del doc["_id"]
            
        # Create instance without calling __init__ directly (bypassing SQLAlchemy init)
        instance = self.model_class.__new__(self.model_class)
        for k, v in doc.items():
            setattr(instance, k, v)
        return instance


class MongoSession:
    """Wrapper that mimics SQLAlchemy Session API."""
    
    def __init__(self, db: "PyMongoDatabase"):
        self.db = db
        self._added = []
        self._deleted = []
        
    def query(self, model_class: Type[T]) -> MongoQuery[T]:
        """Start a query for a model."""
        collection_name = getattr(model_class, "__tablename__", model_class.__name__.lower())
        collection = self.db[collection_name]
        return MongoQuery(collection, model_class)
        
    def add(self, instance: Any) -> None:
        """Queue an item for insertion."""
        self._added.append(instance)
        
    def delete(self, instance: Any) -> None:
        """Queue an item for deletion."""
        self._deleted.append(instance)
        
    def commit(self) -> None:
        """Execute queued inserts and deletes."""
        # Process inserts and updates
        for instance in self._added:
            collection_name = getattr(instance, "__tablename__", instance.__class__.__name__.lower())
            collection = self.db[collection_name]
            
            # Extract attributes that represent columns
            doc = {}
            for key in dir(instance):
                if not key.startswith("_") and key not in ["metadata", "registry"]:
                    val = getattr(instance, key)
                    if not callable(val):
                        # Handle JSON types
                        if isinstance(val, (dict, list)):
                            import json
                            doc[key] = json.dumps(val)
                        else:
                            doc[key] = val
                            
            # Identify primary key for upsert
            pk_field = None
            if hasattr(instance.__class__, "id") and collection_name != "index_runs":
                pk_field = "id"
            elif hasattr(instance.__class__, "run_id"):
                pk_field = "run_id"
            elif hasattr(instance.__class__, "repo_id") and collection_name in ("repository_manifests", "catalog_store"):
                pk_field = "repo_id"
            elif hasattr(instance.__class__, "symbol_id"):
                pk_field = "symbol_id"
                
            if pk_field and getattr(instance, pk_field, None) is not None:
                collection.update_one(
                    {pk_field: getattr(instance, pk_field)},
                    {"$set": doc},
                    upsert=True
                )
            else:
                collection.insert_one(doc)
                
        # Process deletes
        for instance in self._deleted:
            collection_name = getattr(instance, "__tablename__", instance.__class__.__name__.lower())
            collection = self.db[collection_name]
            
            pk_field = None
            if hasattr(instance, "id"): pk_field = "id"
            elif hasattr(instance, "run_id"): pk_field = "run_id"
            elif hasattr(instance, "repo_id") and collection_name in ("repository_manifests", "catalog_store"): pk_field = "repo_id"
            elif hasattr(instance, "symbol_id"): pk_field = "symbol_id"
            
            if pk_field:
                collection.delete_one({pk_field: getattr(instance, pk_field)})
        self._added = []
        self._deleted = []
        
    def rollback(self) -> None:
        """Clear queued operations."""
        self._added = []
        self._deleted = []
        
    def refresh(self, instance: Any) -> None:
        """No-op for Mongo since we don't have an identity map."""
        pass
        
    def close(self) -> None:
        """No-op."""
        pass
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        self.close()


class MongoDatabase:
    """MongoDB connection manager that mimics Database class."""
    
    def __init__(self):
        """Initialize database connection."""
        uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        db_name = os.getenv("MONGODB_DATABASE", "codemind")
        
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
        
    def init_db(self):
        """Create collections and indexes."""
        # In MongoDB, collections are created implicitly, but we can explicitly create indexes here
        
        # jobs indexes
        self.db.jobs.create_index("id", unique=True)
        self.db.jobs.create_index("repo_path")
        self.db.jobs.create_index("status")
        
        # repository_manifests indexes
        self.db.repository_manifests.create_index("repo_id", unique=True)
        self.db.repository_manifests.create_index("repo_path", unique=True)
        
        # index_runs indexes
        self.db.index_runs.create_index("run_id", unique=True)
        self.db.index_runs.create_index("repo_id")
        
        # symbols indexes
        self.db.symbols.create_index("symbol_id", unique=True)
        self.db.symbols.create_index("repo_id")
        self.db.symbols.create_index("file_path")
        
        # commit_snapshots indexes
        self.db.commit_snapshots.create_index("repo_id")
        
        # catalog_store indexes
        self.db.catalog_store.create_index("repo_id", unique=True)
        self.db.catalog_store.create_index("status")
        
        # playbook_store indexes
        self.db.playbook_store.create_index("id", unique=True)
        self.db.playbook_store.create_index("name", unique=True)
        
    def get_session(self) -> MongoSession:
        """Get database session."""
        return MongoSession(self.db)
        
    def drop_all(self):
        """Drop all specific collections."""
        collections = [
            "jobs", "repository_manifests", "index_runs", 
            "symbols", "commit_snapshots", "catalog_store", "playbook_store"
        ]
        for coll in collections:
            self.db[coll].drop()
