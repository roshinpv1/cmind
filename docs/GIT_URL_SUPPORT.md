# Git URL & Branch Support - Feature Documentation

## ✅ Enhancement Complete

Added support for indexing repositories via Git URLs with branch specification.

## 🔧 What Changed

### New Capabilities

**1. Index Git Repositories Directly**
```bash
# No need to clone manually - just provide the URL
POST /api/v1/index
{
  "repo_url": "https://github.com/username/repository.git",
  "branch": "main"
}
```

**2. Branch Specification**
- Index any branch: `main`, `develop`, `feature/xyz`
- Repositories are cached locally for incremental updates
- Each branch is cached separately

**3. Backward Compatible**
- Local paths still work: `{"repo_path": "/path/to/repo"}`
- Must provide either `repo_path` OR `repo_url` (not both)

---

## 📝 API Changes

### IndexRequest Model
```python
class IndexRequest(BaseModel):
    repo_path: str | None = None    # Local filesystem path
    repo_url: str | None = None     # Git repository URL (https/ssh)
    branch: str = "main"            # Branch to index (for git URLs)
```

**Validation:**
- Either `repo_path` or `repo_url` must be provided
- Cannot provide both simultaneously
- `branch` is optional (defaults to "main")

---

## 🛠️ Implementation Details

### GitRepoManager (`utils/git_utils.py`)

**Features:**
- Clones remote repositories to `data/repos/{repo_name}/{branch}/`
- Reuses existing clones (pulls latest changes)
- Shallow clones for efficiency (`depth=1`)
- Supports HTTPS and SSH URLs

**Methods:**
```python
ensure_repo(repo_url: str, branch: str) -> tuple[Path, str, str]
# Returns: (local_path, repo_id, current_commit)
```

### Cache Structure
```
data/repos/
  ├── my-repo/
  │   ├── main/        # main branch cache
  │   └── develop/     # develop branch cache
  └── another-repo/
      └── main/
```

---

## 📬 Updated Postman Collection

The Postman collection now includes:

1. **Index Local Repository** - Original functionality
2. **Index Git Repository (HTTPS)** - New: Clone & index from URL
3. **Index Specific Branch** - New: Specify branch to index

### Example Requests

**Local Repository:**
```json
{
  "repo_path": "/Users/username/projects/my-repo"
}
```

**GitHub Repository (main branch):**
```json
{
  "repo_url": "https://github.com/username/repository.git",
  "branch": "main"
}
```

**Specific Branch:**
```json
{
  "repo_url": "https://github.com/username/repository.git",
  "branch": "develop"
}
```

**SSH URLs:**
```json
{
  "repo_url": "git@github.com:username/repository.git",
  "branch": "main"
}
```

---

## 🔄 Workflow

**For Git URLs:**
1. API receives `repo_url` and `branch`
2. `GitRepoManager` checks cache at `data/repos/{repo}/{branch}/`
3. If exists → pull latest changes
4. If not → clone repository (shallow, depth=1)
5. Indexing proceeds on local cached copy
6. Subsequent runs reuse cache (incremental updates)

**For Local Paths:**
- Works exactly as before
- No cloning or caching

---

## ✅ Examples

### Index a Public Repo
```bash
curl -X POST http://localhost:8000/api/v1/index \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/fastapi/fastapi.git",
    "branch": "master"
  }'
```

### Index Multiple Branches
```bash
# Index main branch
curl -X POST http://localhost:8000/api/v1/index \
  -d '{"repo_url": "https://github.com/user/repo.git", "branch": "main"}'

# Index develop branch (cached separately)
curl -X POST http://localhost:8000/api/v1/index \
  -d '{"repo_url": "https://github.com/user/repo.git", "branch": "develop"}'
```

---

## 📌 Notes

- Private repositories require authentication (SSH keys or credentials)
- Repositories are cached in `data/repos/` directory
- Each branch gets its own cache directory
- Incremental updates pull latest changes before indexing
- Cache can be cleared using `GitRepoManager.cleanup_cache()`

---

## 🚀 Ready to Use!

The server automatically reloaded with these changes. Try it now with the updated Postman collection! 🎉
