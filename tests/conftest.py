"""Pytest configuration and shared fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def sample_repo_path(tmp_path: Path) -> Path:
    """Provides a temporary directory for test repositories."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    return repo_dir


@pytest.fixture
def sample_python_file(tmp_path: Path) -> Path:
    """Provides a sample Python file for testing."""
    file_path = tmp_path / "sample.py"
    file_path.write_text('''
def hello_world():
    """A simple hello world function."""
    return "Hello, World!"

class SampleClass:
    """A sample class for testing."""

    def __init__(self, name: str):
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}!"
''')
    return file_path
