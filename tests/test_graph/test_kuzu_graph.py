"""
Tests for Kùzu graph database integration.
"""

import tempfile
from pathlib import Path

import pytest

from codemind.graph import KuzuGraphDB


class TestKuzuGraphDB:
    """Test Kùzu graph database operations."""

    @pytest.fixture
    def temp_db_path(self):
        """Create temporary directory for database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test_kuzu_graph"

    @pytest.fixture
    def graph_db(self, temp_db_path):
        """Create test graph database."""
        db = KuzuGraphDB(temp_db_path)
        yield db
        db.close()

    def test_create_repository_node(self, graph_db):
        """Test creating repository node."""
        graph_db.add_repository("repo123", "/path/to/repo")
        # No exception means success - Kùzu doesn't have easy exists check

    def test_add_file_node(self, graph_db):
        """Test adding file node."""
        graph_db.add_repository("repo123", "/path/to/repo")
        graph_db.add_file("repo123", "src/main.py")

    def test_add_class_node(self, graph_db):
        """Test adding class node."""
        graph_db.add_repository("repo123", "/path/to/repo")
        graph_db.add_file("repo123", "src/main.py")
        graph_db.add_class("repo123", "src/main.py", "MyClass")

    def test_add_function_node(self, graph_db):
        """Test adding standalone function node."""
        graph_db.add_repository("repo123", "/path/to/repo")
        graph_db.add_file("repo123", "src/main.py")
        graph_db.add_function("repo123", "src/main.py", "my_function")

    def test_add_method_node(self, graph_db):
        """Test adding method node (function with parent class)."""
        graph_db.add_repository("repo123", "/path/to/repo")
        graph_db.add_file("repo123", "src/main.py")
        graph_db.add_class("repo123", "src/main.py", "MyClass")
        graph_db.add_function("repo123", "src/main.py", "method_name", "MyClass")

    def test_get_file_classes(self, graph_db):
        """Test querying classes in a file."""
        graph_db.add_repository("repo123", "/path/to/repo")
        graph_db.add_file("repo123", "src/main.py")
        graph_db.add_class("repo123", "src/main.py", "ClassA")
        graph_db.add_class("repo123", "src/main.py", "ClassB")

        classes = graph_db.get_file_classes("repo123", "src/main.py")
        assert len(classes) == 2
        class_names = {c["name"] for c in classes}
        assert "ClassA" in class_names
        assert "ClassB" in class_names

    def test_get_class_methods(self, graph_db):
        """Test querying methods of a class."""
        graph_db.add_repository("repo123", "/path/to/repo")
        graph_db.add_file("repo123", "src/main.py")
        graph_db.add_class("repo123", "src/main.py", "MyClass")
        graph_db.add_function("repo123", "src/main.py", "method1", "MyClass")
        graph_db.add_function("repo123", "src/main.py", "method2", "MyClass")

        methods = graph_db.get_class_methods("repo123", "src/main.py", "MyClass")
        assert len(methods) == 2
        method_names = {m["name"] for m in methods}
        assert "method1" in method_names
        assert "method2" in method_names

    def test_idempotent_operations(self, graph_db):
        """Test that operations are idempotent."""
        # Add same repository twice
        graph_db.add_repository("repo123", "/path/to/repo")
        graph_db.add_repository("repo123", "/path/to/repo")

        # Add same file twice
        graph_db.add_file("repo123", "src/main.py")
        graph_db.add_file("repo123", "src/main.py")

        # Add same class twice
        graph_db.add_class("repo123", "src/main.py", "MyClass")
        graph_db.add_class("repo123", "src/main.py", "MyClass")

        # Should not raise errors
        classes = graph_db.get_file_classes("repo123", "src/main.py")
        assert len(classes) == 1


class TestGraphPersistence:
    """Test graph persistence across sessions."""

    def test_data_persists_across_sessions(self):
        """Test that graph data survives database restart."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "persistent_graph"

            # First session - add data
            db1 = KuzuGraphDB(db_path)
            db1.add_repository("repo123", "/path/to/repo")
            db1.add_file("repo123", "src/main.py")
            db1.add_class("repo123", "src/main.py", "MyClass")
            db1.close()

            # Second session - verify data exists
            db2 = KuzuGraphDB(db_path)
            classes = db2.get_file_classes("repo123", "src/main.py")
            assert len(classes) == 1
            assert classes[0]["name"] == "MyClass"
            db2.close()
