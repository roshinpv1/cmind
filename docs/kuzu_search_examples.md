# Kùzu Graph Search Testing Examples

## Prerequisites
- Server running: `uvicorn codemind.api.server:app`
- Repository indexed with embeddings and graph
- Have a `repo_id` ready

## Example 1: Hybrid Search - Find FastAPI Code in Python Files

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "routing and endpoints",
    "repo_id": "YOUR_REPO_ID",
    "search_mode": "hybrid",
    "filters": {"file_type": ".py"},
    "expand_context": true,
    "limit": 5
  }'
```

**What it does:**
1. Filters to only `.py` files using Kùzu graph
2. Performs semantic search within those files  
3. Returns results with class/function context

---

## Example 2: Structural Search - Find All Test Files

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "tests",
    "repo_id": "YOUR_REPO_ID",
    "search_mode": "structural",
    "filters": {"file_pattern": "test_"},
    "expand_context": true,
    "limit": 10
  }'
```

**What it does:**
- Pure structural query - finds files with "test_" in name
- No semantic similarity, just graph traversal
- Shows structural context for each file

---

## Example 3: Hybrid Search - Find Methods in Specific Class

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "handle request",
    "repo_id": "YOUR_REPO_ID",
    "search_mode": "hybrid",
    "filters": {"class_name": "APIRouter"},
    "expand_context": true,
    "limit": 5
  }'
```

**What it does:**
1. Uses graph to find files with "APIRouter" class
2. Semantic search for "handle request" within those files
3. Returns context showing which class/methods contain the code

---

## Example 4: Pure Semantic Search (Backward Compatible)

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "authentication middleware",
    "repo_id": "YOUR_REPO_ID",
    "search_mode": "semantic",
    "expand_context": false,
    "limit": 10
  }'
```

**What it does:**
- Traditional vector similarity search
- No graph filtering, no context expansion
- Fastest search mode

---

## Example 5: Graph Query - Find All Python Files

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/graph/query \
  -H "Content-Type: application/json" \
  -d '{
    "repo_id": "YOUR_REPO_ID",
    "query_type": "files",
    "file_type": ".py"
  }'
```

**Response:**
```json
[
  {"file_path": "src/main.py"},
  {"file_path": "src/api/routes.py"},
  {"file_path": "tests/test_api.py"}
]
```

---

## Example 6: Graph Query - Find Classes by Name

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/graph/query \
  -H "Content-Type: application/json" \
  -d '{
    "repo_id": "YOUR_REPO_ID",
    "query_type": "classes",
    "pattern": "Router"
  }'
```

**Response:**
```json
[
  {
    "type": "Class",
    "name": "APIRouter",
    "file_path": "src/routing.py"
  },
  {
    "type": "Class",
    "name": "WebSocketRouter",
    "file_path": "src/websocket.py"
  }
]
```

---

## Example 7: Graph Query - Get Methods in a Class

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/graph/query \
  -H "Content-Type: application/json" \
  -d '{
    "repo_id": "YOUR_REPO_ID",
    "query_type": "functions",
    "class_name": "FastAPI"
  }'
```

**Response:**
```json
[
  {
    "function_name": "__init__",
    "file_path": "fastapi/applications.py"
  },
  {
    "function_name": "get",
    "file_path": "fastapi/applications.py"
  },
  {
    "function_name": "post",
    "file_path": "fastapi/applications.py"
  }
]
```

---

## Example 8: Search with Context Expansion

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "database connection",
    "repo_id": "YOUR_REPO_ID",
    "search_mode": "semantic",
    "expand_context": true,
    "limit": 3
  }'
```

**Response (with context):**
```json
[
  {
    "chunk_text": "async def get_db_connection():\\n    conn = await asyncpg.connect()...",
    "file_path": "src/database.py",
    "start_line": 15,
    "score": 0.82,
    "context": {
      "file_path": "src/database.py",
      "classes": ["DatabaseManager"],
      "functions": ["get_db_connection", "close_connection"]
    }
  }
]
```

---

## Testing Checklist

- [ ] Test semantic-only search (backward compatibility)
- [ ] Test hybrid search with file type filter
- [ ] Test hybrid search with class filter
- [ ] Test structural-only search
- [ ] Test context expansion
- [ ] Test graph queries for files
- [ ] Test graph queries for classes
- [ ] Test graph queries for functions
- [ ] Verify performance (hybrid should be < 2x semantic)
- [ ] Test with empty results / no matches
