# CodeMind

**🧠 AI-Powered Code Intelligence Platform with Autonomous Agents**

CodeMind is a production-ready code analysis platform that combines semantic search, graph-based code understanding, and autonomous LangGraph agents to provide deep insights into codebases.

---

## 🎯 What is CodeMind?

CodeMind helps you understand, document, and analyze codebases using:

- **🔍 Hybrid Search** - Semantic vector search + graph-based structural queries
- **🤖 Autonomous Agents** - LangGraph agents that generate docs, answer questions, and more
- **📊 Knowledge Graphs** - Kùzu graph database for code relationships
- **💾 Append-Only Storage** - Immutable, incremental indexing with LanceDB
- **🚀 Fast API** - RESTful API for all operations

**Perfect for:**
- Onboarding new developers
- Generating documentation
- Understanding legacy code
- Code search and exploration
- Automated code analysis

---

## ✨ Key Features

### 1. Intelligent Code Search

**Semantic Search**
```bash
POST /api/v1/search
{
  "query": "authentication middleware",
  "repo_id": "abc123",
  "search_mode": "semantic",
  "limit": 10
}
```

**Hybrid Search** (Semantic + Graph Filters)
```bash
POST /api/v1/search
{
  "query": "database models",
  "repo_id": "abc123",
  "search_mode": "hybrid",
  "filters": {
    "file_type": ".py",
    "file_patterns": ["models", "db"]
  }
}
```

### 2. LangGraph Autonomous Agents

**Documentation Generator Agent**
- ✅ Analyzes repository structure via graph queries
- ✅ Identifies main components with semantic search
- ✅ Extracts features and patterns
- ✅ Generates comprehensive documentation with LLM
- ✅ Self-tracks progress through workflow

```bash
POST /api/v1/agents/execute
{
  "agent_type": "doc_generator",
  "repo_id": "abc123",
  "task": "generate_readme",
  "config": {
    "doc_type": "readme",
    "include_examples": true
  }
}
```

**Agent Workflow:**
```
analyze_structure → identify_components → extract_features → generate_documentation
         ↓                    ↓                                       
    handle_error         handle_error                              
```

### 3. Graph-Based Code Understanding

**Powered by Kùzu Graph Database:**
- File and directory relationships
- Code dependencies
- AST-level structure
- Fast graph queries for filtering

**Example Queries:**
```python
# Find all Python files
files = graph.find_files_by_pattern(repo_id, file_type=".py")

# Get files matching pattern
files = graph.find_files_by_pattern(repo_id, file_patterns=["api", "routes"])

# Filter by file type and pattern
files = graph.filter_by_structure(repo_id, {
    "file_type": ".js",
    "file_patterns": ["component", "view"]
})
```

### 4. Incremental Indexing

**Append-Only, Deterministic:**
- Only processes changed files
- Content-based hashing for stable IDs
- Git-aware change detection
- Immutable storage in LanceDB

```bash
# Index local repository
POST /api/v1/index
{
  "repo_path": "/path/to/repo"
}

# Index from Git URL
POST /api/v1/index
{
  "repo_url": "https://github.com/user/repo.git",
  "branch": "main"
}
```

---

## 🏗️ Architecture

### System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     FastAPI Server                       │
│  • REST API endpoints                                   │
│  • Job management                                       │
│  • WebSocket support                                    │
└────────────┬───────────────────────────────────┬────────┘
             │                                   │
    ┌────────▼────────┐                 ┌───────▼────────┐
    │  LangGraph      │                 │  Search &      │
    │  Agents         │                 │  Indexing      │
    │                 │                 │                │
    │ • Doc Generator │                 │ • Indexing     │
    │ • Q&A (planned) │                 │   Workflow     │
    │ • Diagram       │                 │ • Hybrid       │
    │   (planned)     │                 │   Search       │
    └────────┬────────┘                 └───────┬────────┘
             │                                   │
    ┌────────▼───────────────────────────────────▼────────┐
    │              Storage & Knowledge Layer              │
    │                                                      │
    │  ┌──────────┐  ┌──────────┐  ┌─────────────────┐  │
    │  │ LanceDB  │  │   Kùzu   │  │  Embeddings     │  │
    │  │ (Vector) │  │  (Graph) │  │  (MiniLM-L6-v2) │  │
    │  └──────────┘  └──────────┘  └─────────────────┘  │
    └─────────────────────────────────────────────────────┘
```

### Technology Stack

**Core:**
- **[FastAPI](https://fastapi.tiangolo.com/)** - Modern web framework
- **[LangGraph](https://github.com/langchain-ai/langgraph)** - Agent workflow orchestration
- **[LanceDB](https://lancedb.com/)** - Vector database for embeddings
- **[Kùzu](https://kuzudb.com/)** - Embedded graph database
- **[SentenceTransformers](https://www.sbert.net/)** - all-MiniLM-L6-v2 embeddings

**LLM:**
- **[LM Studio](https://lmstudio.ai/)** - Local LLM server (default: localhost:1234)
- Compatible with any OpenAI-compatible API

**Development:**
- Python 3.12+
- Pydantic for validation
- Tree-sitter for AST parsing

---

## 🚀 Quick Start

### Prerequisites

1. **Python 3.12+**
2. **LM Studio** (or any OpenAI-compatible LLM server)
   - Download from [lmstudio.ai](https://lmstudio.ai/)
   - Start local server on `localhost:1234`

### Installation

```bash
# Clone repository
git clone <your-repo-url>
cd cmind

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

### Start the Server

```bash
# Start FastAPI server
uvicorn codemind.api.server:app --reload

# Server runs on http://localhost:8000
# API docs: http://localhost:8000/docs
```

### Index Your First Repository

```bash
# Index a local repository
curl -X POST http://localhost:8000/api/v1/index \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/your/repo"}'

# Response includes job_id and repo_id
{
  "job_id": "abc-123",
  "status": "pending",
  "repo_id": "def456"
}

# Check indexing status
curl http://localhost:8000/api/v1/jobs/abc-123
```

### Search Code

```bash
# Semantic search
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "authentication functions",
    "repo_id": "def456",
    "search_mode": "semantic",
    "limit": 10
  }'
```

### Generate Documentation

```bash
# Start agent
curl -X POST http://localhost:8000/api/v1/agents/execute \
  -H "Content-Type: application/json" \
  -d '{
    "agent_type": "doc_generator",
    "repo_id": "def456",
    "task": "generate_readme"
  }'

# Get result
curl http://localhost:8000/api/v1/agents/{job_id}/result
```

---

## � API Reference

### Indexing

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/index` | POST | Index a repository |
| `/api/v1/jobs/{job_id}` | GET | Get job status |

### Search

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/search` | POST | Search code (semantic/hybrid) |

### Agents

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/agents/execute` | POST | Execute an agent task |
| `/api/v1/agents/{job_id}/status` | GET | Get agent job status |
| `/api/v1/agents/{job_id}/result` | GET | Get agent result |

### System

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/stats` | GET | System statistics |

**Full API Documentation:** Visit `/docs` when server is running

---

## 🤖 LangGraph Agents

### Current: Documentation Generator

**Workflow:**
1. **Analyze Structure** - Query graph for file types and counts
2. **Identify Components** - Search for entry points, APIs, configs
3. **Extract Features** - Semantic search for authentication, database, etc.
4. **Generate Documentation** - LLM generates comprehensive docs

**Features:**
- ✅ Typed state management with `DocGenState`
- ✅ Conditional routing (skip steps if no data)
- ✅ Progress tracking (append-only list)
- ✅ Error handling with dedicated error node
- ✅ LLM integration (LM Studio)

**State:**
```python
class DocGenState(TypedDict):
    repo_id: str
    doc_type: Literal["readme", "api", "module"]
    structure: dict          # File analysis
    components: list[dict]   # Main components
    features: list[dict]     # Extracted features
    documentation: str       # Generated output
    progress: list[str]      # Progress tracking
    error: Optional[str]     # Error state
```

### Planned Agents

- **Q&A Agent** - Answer questions about code
- **Diagram Generator** - Create architecture diagrams (Mermaid)
- **Code Refactor Agent** - Suggest/apply refactorings
- **Test Generator** - Create unit tests

**Future: Fully Autonomous**
- ReAct pattern (Reason → Act → Observe)
- Self-reflection and iteration
- Multi-agent collaboration
- Tool ecosystem (20+ tools)
- Memory and learning

---

## 📁 Project Structure

```
cmind/
├── src/codemind/
│   ├── api/
│   │   ├── server.py           # FastAPI application
│   │   ├── agents.py           # Agent endpoints
│   │   └── routes.py           # Search/index endpoints
│   ├── llm/
│   │   ├── agents/
│   │   │   ├── base_agent.py   # Base agent class
│   │   │   └── doc_generator.py # LangGraph doc agent
│   │   └── factory.py          # LLM client factory
│   ├── storage/
│   │   └── lancedb_storage.py  # Vector storage
│   ├── graph/
│   │   ├── kuzu_graph.py       # Graph database
│   │   └── graph_query.py      # Graph query service
│   ├── indexer/
│   │   ├── file_loader.py      # File loading
│   │   └── chunker.py          # Code chunking
│   ├── workflows/
│   │   └── indexing_workflow.py # Indexing workflow
│   └── jobs/
│       └── job_manager.py      # Background jobs
├── tests/                      # Test suite
├── data/
│   ├── lancedb/               # Vector storage
│   ├── kuzu_graph/            # Graph storage
│   └── repos/                 # Cloned repositories
├── docs/                      # Documentation
├── pyproject.toml            # Project config
└── README.md                 # This file
```

---

## � Configuration

### Environment Variables

```bash
# LLM Configuration
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=local-model

# Storage Paths
LANCEDB_PATH=data/lancedb
KUZU_PATH=data/kuzu_graph
REPOS_PATH=data/repos

# Server
HOST=0.0.0.0
PORT=8000
```

### Data Directories

All data is stored locally in the `data/` directory:
- `data/lancedb/` - Vector embeddings
- `data/kuzu_graph/` - Code graph
- `data/repos/` - Cloned repositories

---

## 🧪 Development

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_search_integration.py -v

# Run with coverage
pytest --cov=codemind tests/
```

### Code Quality

```bash
# Format code
black src/ tests/

# Lint
ruff check src/ tests/

# Type checking
mypy src/codemind
```

### Testing with Postman

Import `CodeMind_API.postman_collection.json` for pre-configured requests:
- Indexing endpoints
- Search (semantic & hybrid)
- Agent execution
- System health

---

## 📊 How It Works

### 1. Indexing Pipeline

```
Repository → File Loader → Chunker → Embedder → LanceDB
                                                    ↓
                          Tree-sitter → AST → Kùzu Graph
```

**Steps:**
1. Clone/load repository
2. Detect changed files (Git or content hash)
3. Chunk code into semantic units
4. Generate embeddings (all-MiniLM-L6-v2)
5. Store in LanceDB (append-only)
6. Parse AST and build graph in Kùzu
7. Track in manifest

### 2. Hybrid Search

```
Query → Embedder → LanceDB (vector search)
                        ↓
         Graph Filters → Kùzu (structure filter)
                        ↓
                  Merge & Rank → Results
```

**Example:**
```python
# User query: "API endpoints in auth module"

# Step 1: Semantic search
vector_results = lancedb.search(query_embedding, limit=50)

# Step 2: Graph filter
graph_files = kuzu.find_files(file_patterns=["auth", "api"])

# Step 3: Merge (files in both results)
final_results = intersect(vector_results, graph_files)
```

### 3. Agent Execution

```
User Request → LangGraph Workflow → Tools → LLM → Result
                     ↓
                 State Updates → Progress Tracking
```

**Example: README Generation**
1. Agent analyzes structure via graph query
2. Searches for main components
3. Extracts features with semantic search
4. Builds context from all data
5. LLM generates documentation
6. Returns result with progress log

---

## 🎯 Use Cases

### 1. Code Exploration
"Find all database connection handlers"
```bash
curl -X POST /api/v1/search -d '{
  "query": "database connection pool",
  "search_mode": "semantic"
}'
```

### 2. Documentation Generation
"Generate README for my FastAPI project"
```bash
curl -X POST /api/v1/agents/execute -d '{
  "agent_type": "doc_generator",
  "task": "generate_readme"
}'
```

### 3. Onboarding
"What are the main entry points of this codebase?"
- Agent analyzes structure
- Identifies `main.py`, `app.py`, `server.py`
- Explains architecture

### 4. Code Understanding
"How does authentication work here?"
- Semantic search for auth patterns
- Graph traversal for dependencies
- Agent synthesizes explanation

---

## 🚧 Current Limitations

- **Single repository at a time** - Multi-repo support planned
- **Local LLM only** - Cloud LLM support coming
- **No incremental updates** - Full re-index on changes (incremental planned)
- **Limited agent types** - Only doc generator (more planned)
- **No UI** - API-only (web UI planned)

---

## 🗺️ Roadmap

### ✅ Completed (v0.1)
- [x] LanceDB vector storage
- [x] Kùzu graph database
- [x] Semantic search
- [x] Hybrid search (semantic + graph)
- [x] LangGraph documentation agent
- [x] FastAPI server
- [x] Background job processing

### 🚀 Next (v0.2) - Autonomous Agents
- [ ] ReAct pattern (Reason-Act-Observe)
- [ ] Tool registry with 10+ tools
- [ ] Self-reflection and iteration
- [ ] Q&A agent
- [ ] Diagram generator agent

### 🔮 Future (v0.3+)
- [ ] Multi-agent collaboration (Supervisor pattern)
- [ ] Memory and learning
- [ ] Web-based UI
- [ ] Multi-repository support
- [ ] Incremental indexing
- [ ] Cloud LLM support
- [ ] LangSmith observability

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing`)
5. Open a Pull Request

**Development guidelines:**
- Run tests before submitting
- Add tests for new features
- Follow existing code style
- Update documentation

---

## 📝 License

[To be determined]

---

## 🙏 Acknowledgments

**Built with:**
- [LangGraph](https://github.com/langchain-ai/langgraph) - Agent orchestration
- [LanceDB](https://lancedb.com/) - Vector storage
- [Kùzu](https://kuzudb.com/) - Graph database
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [Sentence Transformers](https://www.sbert.net/) - Embeddings

---

## 📧 Contact

For questions or feedback, please open an issue on GitHub.

---

**CodeMind** - *AI-Powered Code Intelligence*
