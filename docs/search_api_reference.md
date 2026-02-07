# CodeMind Search API Reference

This document details the usage of the `/api/v1/search` endpoint, including all search modes, filter options, and combination strategies.

## Endpoint Overview

- **URL**: `POST /api/v1/search`
- **Content-Type**: `application/json`
- **Response Model**: List of `SearchResult` objects

### Request Body

```json
{
  "query": "string (required)",
  "repo_id": "string (optional, but recommended)",
  "limit": 10,
  "search_mode": "hybrid",
  "filters": {
    "file_type": ".py",
    "class_name": "FastAPI"
  },
  "expand_context": true
}
```

---

## 1. Search Modes

The API supports three distinct search modes tailored for different use cases.

### A. Semantic Search (`mode: "semantic"`)
Uses vector embeddings to find code that is *conceptually* similar to your query. Best for "How do I..." questions or finding logic without knowing exact keywords.

- **How it works**: Embeds query -> Searches LanceDB vector store.
- **Ignores**: Most structural filters (unless manually implemented in client).
- **Use Case**: "Find authentication logic", "Where is the user data validated?"

### B. Structural Search (`mode: "structural"`)
Uses the Kùzu Graph Database to find files that match specific structural criteria. **Does not use valid semantic similarity**; it returns files that match the filters.

- **How it works**: Queries Graph DB with provided filters -> Returns matching file paths.
- **Ignores**: The semantic meaning of `query` (only uses it if it matches a symbol name in some advanced filters).
- **Use Case**: "Find all Python files", "Find all files with class `User`", "Find all tests".

### C. Hybrid Search (`mode: "hybrid"`) - **Recommended**
The power of both. First filters the search space using the Graph DB (Structural), then performs Semantic Search within those candidate files.

- **How it works**:
    1.  **Filter**: Graph DB finds candidate files matching `filters`.
    2.  **Search**: LanceDB performs vector search.
    3.  **Intersect**: Results are filtered to only include those in the candidate list.
- **Use Case**: "Find authentication logic ONLY in `.py` files", "Find `process_data` function usage in `services/` directory".

---

## 2. Filter Options

The `filters` dictionary supports a wide range of structural constraints. These are primarily used in `structural` and `hybrid` modes.

### File Filters
Narrow down the search scope by file attributes.

| Filter Key | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `file_type` | `str` | Exact extension match | `".py"`, `".ts"` |
| `file_types` | `list[str]` | Match **ANY** of these extensions | `[".py", ".ipynb"]` |
| `file_pattern` | `str` | Simple path substring match | `"tests/"`, `"models"` |
| `file_patterns` | `list[str]` | Match **ANY** of these patterns | `["tests/", "spec/"]` |
| `file_pattern_regex` | `str` | logical Regex match on file path | `"^src/.*_api\\.py$"` |

### Symbol Filters
Find files containing specific code definitions.

| Filter Key | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `class_name` | `str` | File must declare this class | `"User"`, `"BaseModel"` |
| `class_names` | `list[str]` | Match **ANY** of these classes | `["User", "Account"]` |
| `function_name` | `str` | File must declare this function | `"process_request"` |
| `function_names` | `list[str]` | Match **ANY** of these functions | `["login", "logout"]` |

### Exclusion Filters
Exclude noisy files or directories.

| Filter Key | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `exclude_patterns` | `list[str]` | Exclude path if it contains string | `["node_modules", "dist"]` |
| `exclude_pattern_regex` | `str` | Exclude path if regex matches | `".*/(test|mock)/.*"` |

### Advanced Structure
*Note: These currently rely on symbol name matching in the graph.*

| Filter Key | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `has_decorator` | `str` | File contains decorator usage | `"@app.get"`, `"@dataclass"` |
| `inherits_from` | `str` | File contains parent class usage | `"BaseModel"`, `"SessionMixin"` |

---

## 3. Combination Examples

### Scenario 1: Find API Endpoints (Hybrid)
Find semantic matches for "login" but only in Python files that likely define API routes.

```json
{
  "query": "login authentication endpoint",
  "search_mode": "hybrid",
  "filters": {
    "file_type": ".py",
    "has_decorator": "@app",
    "exclude_patterns": ["tests"]
  }
}
```

### Scenario 2: Find React Components (Structural)
Find all TypeScript files in the `components/` folder.

```json
{
  "query": "ignored",
  "search_mode": "structural",
  "filters": {
    "file_type": ".tsx",
    "file_pattern": "components/"
  }
}
```

### Scenario 3: Complex Multi-Language Search (Hybrid)
Find "data processing" logic in either Python or Rust files, excluding generated code.

```json
{
  "query": "data processing pipeline",
  "search_mode": "hybrid",
  "filters": {
    "file_types": [".py", ".rs"],
    "exclude_patterns": ["generated", "protobuf", "build"]
  }
}
```

### Scenario 4: Specific Class Logic (Hybrid)
Find where the `User` class is used or defined, focusing on "password validation".

```json
{
  "query": "password validation logic",
  "search_mode": "hybrid",
  "filters": {
    "class_name": "User"
  }
}
```

---

## 4. Response Format

```json
[
  {
    "chunk_text": "def validate_password(pwd): ...",
    "file_path": "src/auth/validators.py",
    "start_line": 45,
    "score": 0.89,
    "context": {
      "file_path": "src/auth/validators.py",
      "classes": ["PasswordValidator"],
      "functions": ["validate_password", "hash_secret"]
    }
  }
]
```
- **score**: For semantic/hybrid, this is the vector similarity (lower is usually better distance). For structural, it is always `1.0`.
- **context**: populated if `expand_context: true`. Contains graph metadata about the file.
