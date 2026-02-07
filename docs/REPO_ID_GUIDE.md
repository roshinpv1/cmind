# Getting Repository ID After Indexing

## Overview

The `repo_id` is now returned in both the index response and job status response, making it easy to track and use for subsequent operations like search.

---

## API Responses with repo_id

### 1. Immediate Response (POST /api/v1/index)

When you start indexing, you immediately get the `repo_id`:

```json
POST /api/v1/index
{
  "repo_path": "/path/to/repo"
  // OR
  "repo_url": "https://github.com/user/repo.git",
  "branch": "main"
}

Response:
{
  "job_id": "abc123...",
  "status": "pending",
  "repo_id": "a1b2c3d4e5f6..."  ← Repository ID
}
```

### 2. Job Status Response (GET /api/v1/jobs/{job_id})

You also get it when checking job status:

```json
GET /api/v1/jobs/abc123...

Response:
{
  "job_id": "abc123...",
  "repo_path": "/path/to/repo",
  "status": "completed",
  "stage": "completed",
  "progress": 100,
  "error": null,
  "repo_id": "a1b2c3d4e5f6..."  ← Repository ID
}
```

---

## Using repo_id for Search

Once you have the `repo_id`, use it to search within that specific repository:

```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "authentication functions",
    "repo_id": "a1b2c3d4e5f6...",
    "limit": 10
  }'
```

**Without repo_id**: Searches across ALL indexed repositories  
**With repo_id**: Searches only within the specified repository

---

## How repo_id is Computed

### For Local Paths
```python
# SHA-256 hash of the absolute path (first 16 chars)
repo_id = sha256("/absolute/path/to/repo").hexdigest()[:16]
```

### For Git URLs
```python
# SHA-256 hash of the normalized URL (first 16 chars)
repo_id = sha256("github.com/user/repo").hexdigest()[:16]
```

**Properties:**
- ✅ Deterministic (same repo = same ID)
- ✅ Unique per repository
- ✅ Stable across re-indexing

---

## Complete Workflow Example

```bash
# 1. Start indexing
RESPONSE=$(curl -X POST http://localhost:8000/api/v1/index \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/fastapi/fastapi.git"}')

JOB_ID=$(echo $RESPONSE | jq -r '.job_id')
REPO_ID=$(echo $RESPONSE | jq -r '.repo_id')

echo "Job ID: $JOB_ID"
echo "Repo ID: $REPO_ID"

# 2. Poll job status
curl http://localhost:8000/api/v1/jobs/$JOB_ID

# 3. Once complete, search using repo_id
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d "{
    \"query\": \"router implementation\",
    \"repo_id\": \"$REPO_ID\",
    \"limit\": 5
  }"
```

---

## Benefits

1. **Immediate Access**: Get `repo_id` right when you start indexing
2. **Consistent**: Same ID returned in index response and job status
3. **Searchable**: Use it immediately for targeted searches
4. **Trackable**: Link jobs to repositories easily

---

## Updated Postman Collection

The Postman collection has been updated to show `repo_id` in all relevant responses. Import the latest version from `CodeMind_API.postman_collection.json`.
