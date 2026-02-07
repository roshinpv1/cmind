# CodeMind

**Incremental, append-only code intelligence platform with semantic search and knowledge graphs**

## 🎯 System Intent

CodeMind is designed to provide deep code understanding through a combination of:
- **Incremental indexing** - Process only what changed, never reprocess the entire codebase
- **Append-only storage** - Immutable data guarantees, no updates or deletes
- **Deterministic processing** - Same input always produces same output, content-addressable IDs
- **Hybrid intelligence** - Combines vector embeddings with AST-based knowledge graphs
- **Failure resilience** - Graceful degradation, isolated failures don't corrupt state

## 🏗️ Architecture Principles

### 1. **Incremental by Default**
- Git-based change detection with content-hash fallback
- Only changed files/chunks trigger reprocessing
- Manifest tracks repository state across restarts

### 2. **Append-Only Everything**
- LanceDB stores all embeddings without updates
- Graph nodes use content-based IDs for idempotency
- Job history preserved for debugging

### 3. **Deterministic Identity**
- Content hashing for chunks ensures stable IDs
- Embedding versioning tracks model changes
- Reproducible outputs enable testing and validation

### 4. **Workflow Orchestration**
- LangGraph models indexing as stateful workflow
- Node-level error isolation prevents cascade failures
- Observable state transitions for debugging

## 📋 Milestone Roadmap

The project is built incrementally across 15 milestones:

| Milestone | Focus Area | Status |
|-----------|-----------|---------|
| **M0** | Project Skeleton & Guardrails | ✅ In Progress |
| **M1** | Incremental Change Detection | 🔜 Planned |
| **M2** | Manifest Persistence | 🔜 Planned |
| **M3** | AST Extraction Engine | 🔜 Planned |
| **M4** | Deterministic Chunking | 🔜 Planned |
| **M5** | Incremental Embedding Engine | 🔜 Planned |
| **M6** | Append-Only LanceDB Storage | 🔜 Planned |
| **M7** | Graph Construction Engine | 🔜 Planned |
| **M8** | LangGraph Workflow Orchestration | 🔜 Planned |
| **M9** | Async Job Manager | 🔜 Planned |
| **M10** | FastAPI Control Plane | 🔜 Planned |
| **M11** | Semantic Search (LanceDB) | 🔜 Planned |
| **M12** | Hybrid RAG Engine | 🔜 Planned |
| **M13** | Observability & Metrics | 🔜 Planned |
| **M14** | Hardening & Validation | 🔜 Planned |

## 🚀 Quick Start

### Prerequisites
- Python 3.10 or higher
- Git

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd cmind

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in development mode
pip install -e ".[dev]"
```

### Development

```bash
# Run code formatting
black .

# Run linting
ruff check .

# Run type checking
mypy src/codemind

# Run tests
pytest tests/ -v
```

## 🛠️ Technology Stack

### Core Dependencies
- **FastAPI** - Modern web framework for APIs
- **LangGraph** - Workflow orchestration with state management
- **LanceDB** - Append-only vector storage
- **SentenceTransformers** - Embedding generation
- **Tree-Sitter** - Multi-language AST parsing
- **Neo4j** - Knowledge graph storage
- **SQLAlchemy** - Database ORM for manifest and jobs

### Development Tools
- **Black** - Code formatting
- **Ruff** - Fast linting
- **Pytest** - Testing framework
- **MyPy** - Static type checking

## 📁 Project Structure

```
cmind/
├── src/codemind/          # Main package
│   ├── api/               # FastAPI endpoints (M10)
│   ├── indexer/           # Change detection & file loading (M1)
│   ├── graph/             # Knowledge graph engine (M7)
│   ├── storage/           # LanceDB & manifest persistence (M2, M6)
│   ├── jobs/              # Async job manager (M9)
│   ├── workflows/         # LangGraph orchestration (M8)
│   └── utils/             # Shared utilities
├── tests/                 # Test suite
├── pyproject.toml         # Project configuration
└── README.md              # This file
```

## 🔍 Key Features

### Incremental Indexing (M1)
Detects changed files using Git history and content hashing, ensuring only deltas are processed.

### Semantic Search (M11)
Vector similarity search across code chunks with metadata filtering and relevance ranking.

### Hybrid RAG (M12)
Combines vector recall with graph-based reranking to assemble contextually accurate code snippets.

### Observability (M13)
Structured logging, metrics collection, and health endpoints for operational visibility.

## 📝 License

[To be determined]

## 🤝 Contributing

[To be determined]
