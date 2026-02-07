# Quick Filter Reference

## All Available Filters

### File Filters
```json
{
  "file_type": ".py",                    // Single extension
  "file_types": [".py", ".js"],          // Multiple (OR)
  "file_pattern": "test_",               // Contains pattern
  "file_patterns": ["api", "service"],   // Multiple patterns (OR)
  "file_pattern_regex": "^src/.*\\.py$"  // Regex match
}
```

### Code Filters
```json
{
  "class_name": "User",                    // Single class
  "class_names": ["User", "Admin"],        // Multiple (OR)
  "function_name": "login",                // Single function
  "function_names": ["login", "logout"],   // Multiple (OR)
  "has_decorator": "@app.get",             // Has decorator
  "inherits_from": "BaseModel"             // Inherits from class
}
```

### Exclusion Filters
```json
{
  "exclude_patterns": ["test_", "cache"],      // Exclude containing
  "exclude_pattern_regex": ".*(test|spec).*"   // Exclude regex
}
```

## Quick Examples

### Find Python or JavaScript API files (exclude tests)
```json
{
  "filters": {
    "file_types": [".py", ".js"],
    "file_pattern": "api",
    "exclude_patterns": ["test_"]
  }
}
```

### Find test files for authentication
```json
{
  "filters": {
    "file_pattern_regex": ".*test.*\\.py$",
    "function_names": ["test_auth", "test_login"]
  }
}
```

### Find models (exclude migrations)
```json
{
  "filters": {
    "file_pattern": "model",
    "inherits_from": "Base",
    "exclude_pattern_regex": ".*migration.*"
  }
}
```

## Logic Rules

- **Arrays = OR**: `file_types: [".py", ".js"]` → `.py` OR `.js`
- **Different types = AND**: Multiple filter types combined with AND
- **Exclusions last**: Applied after all other filters

## Full API Call

```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "your search query",
    "repo_id": "YOUR_REPO_ID",
    "search_mode": "hybrid",
    "filters": {
      "file_types": [".py", ".js"],
      "exclude_patterns": ["test_"]
    },
    "expand_context": true,
    "limit": 10
  }'
```

## Search Modes

- `"semantic"` - Pure vector search (fast)
- `"structural"` - Graph-only (no embeddings)
- `"hybrid"` - Both combined (default, most powerful)
