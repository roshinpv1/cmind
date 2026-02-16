# CodeMind Sequence Diagrams

## 1. Indexing Workflow

The indexing workflow is orchestrated by `IndexingWorkflow`, handling file processing, AST extraction, embedding, and graph building.

```mermaid
sequenceDiagram
    participant API as API (Server)
    participant WF as IndexingWorkflow
    participant CD as ChangeDetector
    participant AST as ASTExtractor
    participant Chunk as ASTChunker
    participant Embed as EmbeddingGenerator
    participant Lance as LanceDBStorage
    participant Graph as GraphBuilder
    participant Rel as RelationshipExtractor
    participant Manifest as ManifestManager

    API->>WF: run(state)
    activate WF
    
    WF->>CD: detect_changes(state)
    CD-->>WF: FileChange[] (modified/new)
    
    WF->>AST: extract_ast(state)
    AST-->>WF: (AST extraction deferred to graph phase)
    
    WF->>Chunk: chunk_files(state)
    loop For each changed file
        Chunk->>Chunk: chunk_file(path)
    end
    Chunk-->>WF: CodeChunk[]
    
    WF->>Embed: generate_embeddings(state)
    Embed->>Lance: get_all_chunks(repo_id) (check existing)
    Embed->>Embed: encode_chunks(new_chunks)
    Embed-->>WF: chunks_with_embeddings
    WF->>Lance: append_chunks(repo_id, embeddings)
    
    WF->>Graph: build_graph(state)
    Graph->>Graph: build_repository_node()
    loop For each changed file
        Graph->>Graph: build_file_node()
        Graph->>AST: extract(file, language)
        AST-->>Graph: symbols (classes, functions)
        Graph->>Graph: build_class_node()
        Graph->>Graph: build_function_node()
    end
    
    WF->>Rel: extract_relationships(state)
    loop For each changed file
        Rel->>AST: extract(file)
        Rel->>Rel: Resolve imports
        Rel->>Graph: build_import_edges()
        Rel->>Graph: build_inheritance_edges()
        Rel->>Rel: Extract calls
        Rel->>Graph: build_call_edges()
    end
    
    WF->>Manifest: update_manifest(state)
    Manifest-->>WF: updated repository/file manifest
    
    WF-->>API: final_state (completed)
    deactivate WF
```

## 2. Search Functionality

CodeMind supports Semantic, Structural, and Hybrid search modes.

```mermaid
sequenceDiagram
    participant Client
    participant API as API (Server)
    participant Graph as GraphQueryService
    participant Lance as LanceDBStorage
    participant Embed as EmbeddingGenerator

    Client->>API: POST /search (query, mode, filters)
    activate API
    
    API->>Embed: encode_query(query)
    Embed-->>API: query_vector
    
    alt Structural Search
        API->>Graph: filter_by_structure(repo_id, filters)
        Graph-->>API: file_paths[]
        API-->>Client: chunks (mocked from files)
    
    else Hybrid Search
        opt Has Filters
            API->>Graph: filter_by_structure(repo_id, filters)
            Graph-->>API: candidate_files[] (graph filter)
        end
        
        API->>Lance: search(query_vector, repo_id, limit)
        Lance-->>API: semantic_results[]
        
        opt Candidate Files Exist
            API->>API: Filter semantic_results by candidate_files
            Note right of API: Intersection of semantic match AND structural filter
        end
        
        opt Expand Context
            loop For each result
                API->>Graph: get_file_context(repo_id, file_path)
                Graph-->>API: context (classes, functions)
            end
        end
        
        API-->>Client: SearchResult[]
    end
    deactivate API
```

## 3. Autonomous Agent Flow

The autonomous agent uses a Planner (LLM) to orchestrate Playbooks and Tools in a Think-Act-Observe loop.

```mermaid
sequenceDiagram
    participant Client
    participant API as API (Server)
    participant Planner as PlannerAgent
    participant LLM as LLM Client
    participant Executor as PlaybookExecutor
    participant Tools as PlaybookTools
    participant Registry as PlaybookRegistry

    Client->>API: POST /agents/autonomous (goal)
    API->>Planner: execute(goal)
    activate Planner
    
    loop Until Finish or Max Iterations
        Note over Planner: Think Phase
        Planner->>LLM: generate(prompt="Choose PLAYBOOK or TOOL")
        LLM-->>Planner: Action (e.g., TOOL: search)
        
        Note over Planner: Act Phase
        alt Tool Execution
            Planner->>Tools: execute_tool(name, params)
            Tools-->>Planner: Result (JSON)
        else Playbook Execution
            Planner->>Registry: get_playbook(name)
            Planner->>Executor: execute(playbook, input)
            Executor-->>Planner: Result (Markdown)
        end
        
        Note over Planner: Observe Phase
        Planner->>Planner: Log observation (success/error, output)
    end
    
    Note over Planner: Finish Phase
    opt Synthesis Needed
        Planner->>LLM: generate(prompt="Synthesize answer from data...")
        LLM-->>Planner: Final Answer
    end
    
    Planner-->>API: Final Result
    API-->>Client: Job Result
    deactivate Planner
```

## 4. Graph Queries

Direct queries to the Kùzu knowledge graph for structural analysis.

```mermaid
sequenceDiagram
    participant Client
    participant API as API (Server)
    participant Service as GraphQueryService
    participant DB as KuzuGraphDB

    Client->>API: POST /graph/query (type, pattern)
    activate API
    
    API->>Service: query(repo_id, type)
    
    alt Files Query
        Service->>DB: execute("MATCH (f:File) WHERE ... RETURN f.path")
    else Classes Query
        Service->>DB: execute("MATCH (c:Class) WHERE ... RETURN c.name")
    else Functions Query
        Service->>DB: execute("MATCH (fn:Function) WHERE ... RETURN fn.name")
    else Dependencies
        Service->>DB: execute("MATCH (f)-[:IMPORTS]->(d) RETURN ...")
    end
    
    DB-->>Service: Result Rows
    Service-->>API: JSON List
    API-->>Client: Response
    deactivate API
```

## 5. Catalogs

Creating and retrieving high-level summaries (catalogs) stored in LanceDB.

```mermaid
sequenceDiagram
    participant Client
    participant API as API (Server)
    participant Ex as PlaybookExecutor
    participant Lance as LanceDBStorage
    participant Embed as EmbeddingGenerator

    Client->>API: POST /catalogs (playbook_name="code_analyzer", prompt)
    activate API
    
    API->>API: Fetch Repo Metadata
    
    API->>Ex: execute(playbook_name, prompt)
    Ex-->>API: Execution Result (Text)
    
    API->>Embed: encode_document(result)
    Embed-->>API: vector
    
    API->>Lance: store_catalog_item(item + vector)
    Lance-->>API: Success
    
    API-->>Client: Catalog Item Created
    deactivate API
    
    Client->>API: POST /catalogs/search (query)
    API->>Embed: encode_query(query)
    API->>Lance: search_catalogs(vector)
    Lance-->>API: Matching Items
    API-->>Client: Results
```
