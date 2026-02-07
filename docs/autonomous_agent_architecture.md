# Autonomous Agent Architecture & Implementation Flow

## 1. High-Level Overview

The CodeMind Autonomous Agent is designed to intelligently analyze codebases, plan tasks, and execute complex goals using a **Planner-Executor** architecture. It leverages a **Prompt-Based Skill System** where capabilities are defined in Markdown, not code.

```mermaid
graph TD
    User([User Request]) --> API[API Layer]
    API --> Agent[Autonomous Agent]
    
    subgraph "Autonomous Agent Core"
        Planner[Planner (Orchestrator)] <--> Executor[Skill Executor]
        Planner <--> Registry[Skill Registry]
    end
    
    Executor --> Tools[Unified Tool Interface]
    Tools --> DB[(LanceDB / Graph)]
    
    Executor --> LLM[LLM Engine]
    Planner --> LLM
```

---

## 2. Request Lifecycle

1.  **Incoming Request**: User sends a goal (e.g., "Document this repo") via `POST /api/v1/agents/autonomous`.
2.  **Initialization**: The `AutonomousAgent` initializes the `Planner` with the user's goal and repository ID.
3.  **Planning Loop**: The Planner enters a `Think-Act-Observe` loop.
4.  **Execution**: The Planner delegates specific tasks to the `SkillExecutor`.
5.  **Completion**: Once the Planner decides the goal is met, it synthesizes a final response.

---

## 3. The Planner (The Brain)

**File**: `src/codemind/agents/planner.py`

The Planner uses **LangGraph** to manage the state of the conversation and decision-making process.

### State Management (`PlannerState`)
- **Goal**: User's original request.
- **Plan**: Queue of next actions.
- **History**: List of executed actions and observations.
- **Iteration**: Current step count.

### The Loop (Think -> Act -> Observe)

1.  **THINK Node**:
    -   **Orchestrator Prompt**: Loads a concise system prompt (`src/codemind/agents/prompts/orchestrator.md`).
    -   **Context**: Injects the Goal, list of Available Skills (from Registry), and a *concise* History (last 3 iterations).
    -   **LLM Decision**: The LLM outputs either:
        -   `SKILL: <name>` (to do work)
        -   `FINISH: <summary>` (job done)
    -   **Optimization**: Uses a low token limit (200 tokens) to force concise reasoning and prevent context overflow.

2.  **ACT Node**:
    -   Parses the LLM's decision.
    -   Calls `SkillExecutor.execute(skill_name, params)`.

3.  **OBSERVE Node**:
    -   Receives the output from the Executor.
    -   Records success/failure and the result summary into `PlannerState["observations"]`.
    -   Increments iteration count.

---

## 4. Skill Architecture (The Capabilities)

**Location**: `skills/*.md`

Skills are defined purely in **Markdown**, making them easy to extend without changing Python code.

### Structure of a Skill (`doc_generator.md`)
1.  **Metadata**: Name, Description, When to Use (for Planner intent matching).
2.  **System Prompt**: Instructions for the LLM on *how* to process the code (e.g., "Generate a README based on these files...").
3.  **Search Strategy**: YAML configuration defining how to retrieve context.
    -   **Phases**: Supports multi-step search (e.g., "Discovery" -> "Details").
    -   **Queries**: Specific keywords or semantic queries.
    -   **Limits**: Number of chunks to retrieve.

### Loading
-   **Parser** (`src/codemind/skills/parsers.py`): Reads Markdown, extracts the YAML block and System Prompt.
-   **Registry** (`src/codemind/skills/registry.py`): Loads all valid skills into memory at startup.

---

## 5. Skill Executor (The Engine)

**File**: `src/codemind/skills/executors.py`

The Executor runs a specific skill. It follows a strictly defined **Linear Workflow**: `Search -> Generate -> Format`.

### Step 1: Search Node (`search_code`)
-   **Strategy Execution**: Reads variables from the skill's `SearchStrategy`.
-   **Phased Search**: If the skill defines properties like `phases`, it iterates through them to collect queries.
-   **Tool Call**: Invokes `tools.search_codebase` with the compiled queries.
-   **Error Handling**: Checks for `success: True`. If `success: False` (even with empty results), marks the state as failed.

### Step 2: Generation Node (`llm_generate`)
This node contains the **Token-Aware Map-Reduce Logic** to handle large contexts.

1.  **Estimation**: Calculates total tokens (System Prompt + Retrieved Code + User Goal).
2.  **Decision**:
    -   **Small Context** (< 25k tokens): Runs a single LLM call.
    -   **Large Context**: triggers **Map-Reduce**.
        -   **Split**: Uses `token_utils.split_into_chunks` to create batches (e.g., 2000 tokens/batch).
        -   **Map**: process each batch independently with the System Prompt.
        -   **Reduce**: Synthesizes the results of all batches into one final answer.

### Step 3: Format Node
-   Standardizes the output into a dictionary: `{"success": True, "result": "..."}`.

---

## 6. The Tools (The Hands)

**File**: `src/codemind/skills/tools.py`

CodeMind now uses a **Single Unified Tool**: `search_codebase`.

-   **Capabilities**:
    -   Semantic Search (Vector embeddings).
    -   Hybrid Search (Keyword + Vector).
    -   Graph Filtering (File types, dependencies).
-   **Normalization**: Ensures consistent return format (`success`, `results`, `count`) so the Executor can reliably detect status.

---

## 7. Key Implementation Details for Reliability

### Local LLM Optimizations
-   **Prompt Succinctness**: Orchestrator prompts are stripped of fluff.
-   **History Truncation**: The Planner only sees the last 3 steps to keep the context window stable.
-   **Token Budgeting**: The Executor strictly manages context size to avoid `400 Bad Request` errors.

### Failure Recovery
-   **Planner Loop**: If a skill fails (e.g., "Search failed"), the Planner sees the error in the `Observe` step. It can then try a different skill or retry with different parameters in the next `Think` cycle (up to `max_iterations`).
