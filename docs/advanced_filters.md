# Advanced Filter Examples

## All Available Filters

### Basic Filters (AND Logic)
```json
{
  "filters": {
    "file_type": ".py",
    "file_pattern": "api",
    "class_name": "User", 
    "function_name": "login"
  }
}
```

### OR Logic - Multiple Values
```json
{
  "filters": {
    "file_types": [".py", ".js", ".ts"],
    "class_names": ["User", "Admin", "Guest"]
  }
}
```

### Regex Patterns
```json
{
  "filters": {
    "file_pattern_regex": "^tests/.*_test\\.py$"
  }
}
```

### Exclusion Filters
```json
{
  "filters": {
    "file_type": ".py",
    "exclude_patterns": ["__pycache__", "migrations", ".pyc"]
  }
}
```

### Combined Advanced Example
```json
{
  "query": "authentication logic",
  "repo_id": "YOUR_REPO_ID",
  "search_mode": "hybrid",
  "filters": {
    "file_types": [".py", ".js"],
    "file_patterns": ["auth", "security"],
    "exclude_patterns": ["test_", "__pycache__"],
    "class_names": ["AuthService", "SecurityManager"]
  },
  "expand_context": true,
  "limit": 10
}
```

## Filter Reference

| Filter | Type | Description | Example |
|--------|------|-------------|---------|
| `file_type` | string | Single file extension | `".py"` |
| `file_types` | array | Multiple extensions (OR) | `[".py", ".js"]` |
| `file_pattern` | string | Path contains pattern | `"test_"` |
| `file_patterns` | array | Multiple patterns (OR) | `["api", "service"]` |
| `file_pattern_regex` | string | Regex match on path | `"^src/.*\\.py$"` |
| `class_name` | string | Single class name | `"User"` |
| `class_names` | array | Multiple class names (OR) | `["User", "Admin"]` |
| `function_name` | string | Single function name | `"login"` |
| `function_names` | array | Multiple functions (OR) | `["login", "logout"]` |
| `exclude_patterns` | array | Exclude paths containing | `["test_", "cache"]` |
| `exclude_pattern_regex` | string | Exclude regex matches | `"__.*__"` |
| `has_decorator` | string | Files with decorator | `"@app.get"` |
| `inherits_from` | string | Classes inheriting from | `"BaseModel"` |

## Logic Rules

1. **Within same filter type**: OR logic
   - `file_types: [".py", ".js"]` → `.py` OR `.js`

2. **Between different filter types**: AND logic
   - `file_type: ".py"` AND `class_name: "User"` → Python files with User class

3. **Exclusions**: Applied last, removed from results
   - Gets all matches, then removes exclusions

## Real-World Examples

### Find all API route files
```json
{
  "filters": {
    "file_patterns": ["route", "api", "endpoint"],
    "file_types": [".py", ".js"],
    "exclude_patterns": ["test_"]
  }
}
```

### Find authentication code
```json
{
  "filters": {
    "file_pattern_regex": ".*(auth|security|login).*",
    "exclude_patterns": ["node_modules", "__pycache__"]
  }
}
```

### Find test files for specific feature
```json
{
  "filters": {
    "file_pattern": "test_",
    "file_type": ".py",
    "function_names": ["test_auth", "test_login", "test_security"]
  }
}
```

### Find models/schemas
```json
{
  "filters": {
    "file_patterns": ["model", "schema"],
    "inherits_from": "BaseModel",
    "exclude_pattern_regex": ".*migration.*"
  }
}
```
