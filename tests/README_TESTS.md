# Search API Integration Tests

## Overview

Comprehensive integration tests for the search API covering:
- All search modes (semantic, structural, hybrid)
- All filter types and combinations
- Edge cases and error handling
- Context expansion

## Prerequisites

1. **Start the server:**
   ```bash
   uvicorn codemind.api.server:app --reload
   ```

2. **Index a repository** to get a valid `repo_id`

3. **Update the `REPO_ID` constant** in `test_search_integration.py`

## Installation

```bash
pip install -r tests/requirements-test.txt
```

## Running Tests

### Run all tests
```bash
pytest tests/test_search_integration.py -v
```

### Run specific test class
```bash
pytest tests/test_search_integration.py::TestHybridSearch -v
```

### Run specific test
```bash
pytest tests/test_search_integration.py::TestHybridSearch::test_hybrid_combined_filters -v
```

### Run with detailed output
```bash
pytest tests/test_search_integration.py -v -s
```

### Run only failed tests
```bash
pytest tests/test_search_integration.py --lf
```

## Test Coverage

### Search Modes
- ✅ Semantic search (pure vector similarity)
- ✅ Structural search (graph-only)
- ✅ Hybrid search (vector + graph filtering)

### Filter Types

#### Single Value Filters
- ✅ `file_type` - Single file extension
- ✅ `file_pattern` - Path contains pattern
- ✅ `file_pattern_regex` - Regex match on path

#### OR Logic Filters (Arrays)
- ✅ `file_types` - Multiple extensions (OR)
- ✅ `file_patterns` - Multiple patterns (OR)
- ✅ `class_names` - Multiple classes (OR)
- ✅ `function_names` - Multiple functions (OR)

#### Exclusion Filters
- ✅ `exclude_patterns` - Exclude paths containing
- ✅ `exclude_pattern_regex` - Exclude regex matches

#### Combined Filters
- ✅ Multiple filters with AND logic
- ✅ OR within filter type + AND between types

### Edge Cases
- ✅ Empty filters (fallback to semantic)
- ✅ Non-existent repo_id
- ✅ Invalid regex patterns
- ✅ Large limits
- ✅ Context expansion enabled/disabled

## Test Structure

```
tests/test_search_integration.py
├── TestSemanticSearch
│   ├── test_basic_semantic_search
│   └── test_semantic_with_context_expansion
├── TestStructuralSearch
│   └── test_structural_file_type_filter
├── TestHybridSearch
│   ├── test_hybrid_single_file_type
│   ├── test_hybrid_multiple_file_types_or_logic
│   ├── test_hybrid_file_pattern
│   ├── test_hybrid_multiple_patterns_or_logic
│   ├── test_hybrid_regex_pattern
│   ├── test_hybrid_exclusion_patterns
│   ├── test_hybrid_exclusion_regex
│   └── test_hybrid_combined_filters
├── TestEdgeCases
│   ├── test_empty_filters
│   ├── test_nonexistent_repo_id
│   ├── test_invalid_regex_pattern
│   └── test_large_limit
└── TestContextExpansion
    ├── test_context_expansion_enabled
    └── test_context_expansion_disabled
```

## Expected Output

```bash
$ pytest tests/test_search_integration.py -v

tests/test_search_integration.py::TestSemanticSearch::test_basic_semantic_search PASSED
tests/test_search_integration.py::TestSemanticSearch::test_semantic_with_context_expansion PASSED
tests/test_search_integration.py::TestStructuralSearch::test_structural_file_type_filter PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_single_file_type PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_multiple_file_types_or_logic PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_file_pattern PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_multiple_patterns_or_logic PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_regex_pattern PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_exclusion_patterns PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_exclusion_regex PASSED
tests/test_search_integration.py::TestHybridSearch::test_hybrid_combined_filters PASSED
tests/test_search_integration.py::TestEdgeCases::test_empty_filters PASSED
tests/test_search_integration.py::TestEdgeCases::test_nonexistent_repo_id PASSED
tests/test_search_integration.py::TestEdgeCases::test_invalid_regex_pattern PASSED
tests/test_search_integration.py::TestEdgeCases::test_large_limit PASSED
tests/test_search_integration.py::TestContextExpansion::test_context_expansion_enabled PASSED
tests/test_search_integration.py::TestContextExpansion::test_context_expansion_disabled PASSED

======================= 17 passed in 3.45s =======================
```

## Troubleshooting

### No results in tests
- Verify server is running on localhost:8000
- Check `REPO_ID` is correct
- Ensure repository is indexed with embeddings

### Tests timing out
- Increase request timeout in test code
- Check server logs for errors
- Verify LanceDB and Kùzu databases exist

### Assertion failures
- Check server logs for `[SEARCH]` debug messages
- Verify filters are being applied correctly
- May need to adjust assertions based on your data
