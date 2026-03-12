# CodeMind Foundation Architecture

CodeMind is an enterprise-grade AI coding assistant and repository analytics platform. It is designed to navigate entirely self-hosted codebases, extract highly accurate structural architectures, and power specialized autonomous agents.

This document serves as the foundational overview of the platform, detailing how source code is ingested, modeled, and operated upon by autonomous LLM agents.

---

## 1. The Indexing Flow

The core capability of CodeMind lies in its rigorous, robust indexing pipeline designed to handle massive enterprise repositories without stalling. It converts unstructured source files into a highly queryable, unified **Semantic-Structural Graph**.

The indexing flow consists of the following deterministic stages orchestrated by `IndexingWorkflow`:

### 1. Change Detection
When an ingestion request is triggered, the `ManifestManager` uses either Git metadata or a high-performance hash-based differ to identify precisely which files have been added, modified, or deleted since the last indexed commit. 

### 2. AST Extraction & Parsing
Using `ASTExtractor`, CodeMind leverages native C-based `tree-sitter` parsers to perform lightning-fast syntax tree construction for multi-language repositories.
*   **Robustness**: To handle pathological cases like massive, obfuscated JavaScript bundles (e.g., `chunk-xyz.js`), the extractor strictly enforces C-level parser timeouts (`timeout_micros`) and Python-level recursive depth limits. This prevents infinite loops and guarantees the indexing job completes 100% of the time.

### 3. Symbol & Relationship Resolution
CodeMind breaks down the AST to understand the *meaning* of the code.
*   **AST Chunker**: Tokenizes and slices the code into manageable context windows for embedding vectors.
*   **Import Resolver**: Extracts `Import` dependencies between files and internal modules.
*   **Call Extractor**: Identifies function abstractions and method executions, linking `CallSites` to declarations.

### 4. Dual Database Storage
*   **Semantic Layer (LanceDB)**: Vectorizes the chunked AST code blocks using the `EmbeddingGenerator`. LanceDB allows fuzzy semantic search across natural language descriptions and comments.
*   **Structural Layer (Kùzu Graph DB)**: Builds a high-speed property graph modeling the exact architecture (Files → Classes → Functions → Calls → Imports). This graph enables deterministic, 100% accurate code analysis without hallucination.

---

## 2. Agentic Behaviour

CodeMind rejects simple conversational RAG in favor of **Autonomous Multi-Agent Architecture** powered by LangGraph. This yields a deterministic, self-evaluating AI reasoning loop.

### Supervisor Planner
When a user submits a complex request, they are first greeted by the **Planner Agent**. The Planner:
1. Translates the user's intent.
2. Selects the most appropriate **Playbook** from the PlaybookStore.
3. Formulates a concrete execution plan.
4. Spawns specialized executor agents.

### Tool Execution & Grounding Fences
The assigned executor utilizes the *Semantic-Structural Graph* via sophisticated Graph Query endpoints (e.g., `find_files_by_pattern`, `find_symbol_by_name`). 
*   **Grounding Fences**: Agents are strictly restrained by `grounding_fence` flags defined in the playbook. They cannot hallucinate APIs; they must prove the code exists in Kùzu or LanceDB before citing it in their analysis.
*   **Self-Correction**: Should an agent fetch irrelevant documents or fail to generate valid JSON schema, it self-corrects internally using the playbook's explicit `Anti-Patterns` constraints before returning an answer to the user.

---

## 3. Playbooks

Playbooks are the declarative "brains" of the agents. Instead of burying prompt engineering in python code, CodeMind uses a formalized Markdown schema (the **V2 Format**).

### Auto-Syncing Playbook Store
Playbooks are stored natively in the `playbooks/` directory and are automatically parsed and upserted into an SQLite `PlaybookStore` database upon server startup.

### Playbook Structure (YAML & Markdown)
A playbook strictly defines the agent's bounds:
*   **Search Strategy**: Dictates if the agent should use purely `semantic`, purely `structural`, or `hybrid` search, along with hard limits and score thresholds.
*   **Behavior**: YAML flags determining if test files should be excluded (`exclude_test_files`) or if repo metadata should be explicitly injected (`inject_repo_metadata`).
*   **Output Schema**: Forces the AI executor to respond in a strict, predefined JSON schema.
*   **Evaluation & Quality Rubric**: Criteria the Supervisor uses to grade the executor's draft response.
*   **Anti-Patterns**: Explicit constraints stopping the AI from falling into common architectural hallucinations.

---

## 4. Various Use Cases

The composability of the Graph DB and formalized Playbooks unlocks massively scalable use-cases.

### Discovery Agent
*   **Purpose**: Orienting developers within undocumented, legacy codebases.
*   **Execution**: A user asks, *"Where is the core authentication logic handled?"* The agent utilizes Hybrid search, queries LanceDB for "authentication" concepts, correlates it against the Kùzu graph to find the exact `AuthService` class, and reports back the structural dependencies (e.g., *"AuthService imports JWTUtils and queries the UserDB"*).

### NFR Agent (Non-Functional Requirements)
*   **Purpose**: Evaluating Technical Debt, Security, and SVP (Software Viability and Performance).
*   **Execution**: Using the `analyze_tech_debt` playbook, the agent is instructed to exclude business logic and rigidly look for structural flaws: circular dependencies, deeply inherited class hierarchies, duplicated utility functions, and outdated library imports discovered within Kùzu. It outputs an NFR grade matrix.

### Architecture As Code
*   **Purpose**: Generating and maintaining living, breathing architecture documents.
*   **Execution**: Using the Kùzu property graph, the architectural layout is queried deterministically. Agents can map API request flows (e.g., Controller → Service → Repository) and automatically generate raw Mermaid.js UML sequence diagrams or entity-relationship (ER) diagrams directly from actual code reality, never diverging from source truth.

### Application Catalog
*   **Purpose**: Enterprise visibility and macro-level searchability.
*   **Execution**: By running the `generate_catalog` playbook across hundreds of indexed repositories, CodeMind identifies the "Identity" of every repo (e.g., Framework=FastAPI, DB=Postgres, Purpose=Payment Gateway). It aggregates these summaries into a unified internal App Catalog, allowing Enterprise Architects to query, *"Show me all Python microservices that handle financial transactions."*
