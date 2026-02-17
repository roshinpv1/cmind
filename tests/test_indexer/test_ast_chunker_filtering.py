import pytest
from pathlib import Path
from codemind.indexer.ast_chunker import ASTChunker

from unittest.mock import MagicMock
from codemind.indexer.ast_extractor import ASTExtractionResult, Symbol

def test_ast_chunker_minimum_size_filter(monkeypatch):
    # Mock ASTExtractor
    mock_extractor = MagicMock()
    mock_extractor.detect_language.return_value = "python"
    
    # Create mock symbols
    # 1. A small symbol (filtered)
    s1 = Symbol(name="tiny", type="function", start_line=4, end_line=5, start_byte=0, end_byte=0)
    # 2. A large symbol (kept)
    s2 = Symbol(name="LargeComponent", type="class", start_line=7, end_line=11, start_byte=0, end_byte=0)
    # 3. Another small symbol (filtered)
    s3 = Symbol(name="another_tiny", type="function", start_line=13, end_line=14, start_byte=0, end_byte=0)
    
    mock_extractor.extract.return_value = ASTExtractionResult(
        symbols=[s1, s2, s3],
        imports=[],
        language="python",
        success=True
    )
    
    # Inject mock
    monkeypatch.setattr("codemind.indexer.ast_chunker.ASTExtractor", lambda: mock_extractor)
    
    # Setup chunker with min_chunk_chars=50
    chunker = ASTChunker(min_chunk_chars=50)
    
    # Test content (lines correspond to mock symbols)
    content = """
import os

def tiny():
    pass

class LargeComponent:
    def __init__(self):
        self.name = "A large component that should definitely be indexed because it has enough content to meet the threshold."
        self.version = "1.0.0"
        self.description = "This is a detailed description to ensure we exceed the 50 character limit for this chunk."

def another_tiny():
    return 1
    
)  # Trailing bracket
"""
    
    # Run chunking
    chunks = chunker.chunk_file(Path("test_file.py"), content=content)
    
    # Assertions
    # s1: "def tiny():\n    pass" (~20 chars) -> filtered
    # s2: "class LargeComponent: ..." (large) -> kept
    # s3: "def another_tiny():\n    return 1" (~30 chars) -> filtered
    # uncovered range (imports): "import os\n" (~10 chars) -> filtered
    # uncovered range (trailing): ")  # Trailing bracket\n" (~20 chars) -> filtered
    
    assert len(chunks) == 1
    assert chunks[0].symbol_name == "LargeComponent"
    assert len(chunks[0].text.strip()) >= 50

def test_ast_chunker_unfiltered_small_chunks(monkeypatch):
    # Mock ASTExtractor to return no symbols (falls back to char chunking)
    mock_extractor = MagicMock()
    mock_extractor.detect_language.return_value = "python"
    mock_extractor.extract.return_value = ASTExtractionResult(symbols=[], imports=[], language="python", success=True)
    monkeypatch.setattr("codemind.indexer.ast_chunker.ASTExtractor", lambda: mock_extractor)

    # Setup chunker with min_chunk_chars=0 (backwards compatibility/manual override)
    chunker = ASTChunker(min_chunk_chars=0)
    
    content = "def tiny(): pass"
    chunks = chunker.chunk_file(Path("tiny.py"), content=content)
    
    assert len(chunks) > 0
    assert "tiny" in chunks[0].text
