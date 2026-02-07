# Kùzu Graph Database Integration

## ✅ Graph Persistence Now Active!

**Yes!** The graph is now **fully persisted** using **Kùzu** embedded graph database.

## 🔧 What Changed

### Before (M7 Initial)
- ❌ In-memory graph (lost on restart)
- ❌ No query capabilities
- ❌ Simple Python dictionaries

### After (M7 Enhanced)
- ✅ **Kùzu embedded database**
- ✅ **Persistent storage** in `data/kuzu_graph/`
- ✅ **Cypher queries** supported
- ✅ **Survives restarts**

---

## 📊 Schema

### Node Types

**Repository**
- `repo_id` (PRIMARY KEY)
- `path`

**File**
- `file_id` (PRIMARY KEY)
- `repo_id`
- `path`

**Class**
- `class_id` (PRIMARY KEY)
- `repo_id`
- `file_path`
- `name`

**Function**
- `func_id` (PRIMARY KEY)
- `repo_id`
- `file_path`
- `name`
- `parent_class`

### Relationship Types

- `CONTAINS` - Repository → File
- `DECLARES_CLASS` - File → Class
- `DECLARES_FUNCTION` - File → Function
- `HAS_METHOD` - Class → Function

---

## 🎯 Features

### Persistent Storage
```python
# Graph survives server restarts
graph = KuzuGraphDB("data/kuzu_graph")
```

### Idempotent Operations
```python
# Safe to call multiple times
graph.add_repository(repo_id, path)
graph.add_file(repo_id, file_path)
graph.add_class(repo_id, file_path, "MyClass")
```

### Cypher Queries
```python
# Get all classes in a file
classes = graph.get_file_classes(repo_id, "src/main.py")

# Get all methods of a class
methods = graph.get_class_methods(repo_id, "src/main.py", "MyClass")

# Custom queries
result = graph.query("""
    MATCH (r:Repository)-[:CONTAINS]->(f:File)
    WHERE r.repo_id = $repo_id
    RETURN f.path
""", {"repo_id": repo_id})
```

---

## 📂 Storage Location

```
data/
  ├── kuzu_graph/        # Kùzu database files
  │   ├── catalog/
  │   ├── storage/
  │   └── wal/
  ├── codemind.db        # SQLite (manifest, jobs)
  └── lancedb/           # Vector embeddings
```

---

## 🚀 Integration

The API server automatically uses Kùzu now:

```python
# In server.py lifespan
app.state.graph_db = KuzuGraphDB()  # Persistent Kùzu graph
```

Graph data is built during indexing workflow and persists across restarts!

---

## 🎉 Benefits

1. **Persistence**: Graph survives server restarts
2. **Performance**: Optimized graph queries
3. **Scalability**: Can handle large codebases
4. **Standards**: Cypher query language support
5. **Embedded**: No separate database server needed
6. **Transactions**: ACID guarantees

---

## 📝 Example Usage

```python
from codemind.graph import KuzuGraphDB

# Initialize
graph = KuzuGraphDB()

# Add nodes
graph.add_repository("repo123", "/path/to/repo")
graph.add_file("repo123", "src/main.py")
graph.add_class("repo123", "src/main.py", "Application")
graph.add_function("repo123", "src/main.py", "run", parent_class="Application")

# Query
classes = graph.get_file_classes("repo123", "src/main.py")
# [{"name": "Application", "id": "repo123:src/main.py:Application"}]

methods = graph.get_class_methods("repo123", "src/main.py", "Application")
# [{"name": "run", "id": "repo123:src/main.py:run"}]
```

---

**The graph is now production-ready with full persistence!** 🎉
