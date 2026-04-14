# Core Code Analysis Runtime (Server-First)

This document defines the target runtime for CodeMind using OpenCode-style agent loops plus Graphify-first retrieval.

## Why Revisit Core

Current behavior works, but core responsibilities are spread across many places:
- code retrieval is split between graph/vector/filesystem tools,
- context control is split between prompt rules and sanitizers,
- playbook execution has multiple return shapes,
- result mapping is partially implicit.

For a long-running server agent, these concerns need one deterministic runtime contract.

## OpenCode Patterns to Reuse

From OpenCode core (`internal/llm/agent/agent.go`, `internal/llm/provider/provider.go`, `internal/llm/tools/*`):
- strict event-driven loop: `stream model -> collect tool calls -> execute -> append tool result -> continue`,
- persisted session/message model as source of truth,
- per-turn tool execution with explicit finish reasons,
- summary/compaction as first-class runtime operation, not ad-hoc fallback,
- bounded tool outputs and file reads with predictable metadata.

## CodeMind Target Runtime

### 1) Fetching Code Data (Graphify-first)

Order of operations for code analysis:
1. `get_map` (architecture hubs, entry points),
2. `trace_path` / callers / callees / dependencies (build ordered reading path),
3. `get_file_outline` (AST-level shape before large file reads),
4. `read_file` and `search_code` only for narrowed targets.

Fallback rules:
- if graph misses a path: `list_repo_directory`,
- if index misses content: filesystem read under manifest `repo_path`,
- semantic search stays optional and should not block graph-first playbooks.

### 2) Context Management

Runtime context should be managed in layers:
- **Tool output budget by tool type** (graph > read_file > search dumps),
- **ToolMessage sanitization before compaction**,
- **Conversation compaction** once threshold is crossed,
- **Session summary checkpoints** for long tasks.

### 3) Playbook Execution Loop

Server runtime loop:
1. create execution envelope (goal, repo scope, max iterations/tokens),
2. run model turn with bounded completion budget,
3. if tool calls exist, execute and append tool messages,
4. enforce stop reasons (`final_answer`, `iteration_cap`, `cancelled`, `permission_denied`, `error`),
5. map to final envelope.

### 4) Result Mapping

All playbooks must return one shape:

```json
{
  "success": true,
  "outputs": {
    "result": "text",
    "data": {},
    "tool_executed": false,
    "tool_result": null,
    "iterations": 0,
    "playbook": "name",
    "context": {
      "sources": [],
      "evidence_count": 0,
      "log_count": 0
    }
  },
  "error": null,
  "logs": []
}
```

## Immediate Refactor (Implemented)

- Added `PlaybookResultMapper` in `src/codemind/playbooks/runtime_core.py`.
- Wired `PlaybookExecutor.execute()` to map linear and ReAct returns into one stable envelope.
- This is the first building block for a full runtime split (fetch/context/execute/map).
- Added `CodeDataFetcher` in `src/codemind/playbooks/code_data_fetcher.py`.
- Wired ReAct executor preflight to use `CodeDataFetcher.build_graph_prefetch(...)` once per run and inject a deterministic roadmap block from code (not ad-hoc prompt-only map generation).

## Next Refactor Steps

1. Add a `CodeDataFetcher` runtime service and move Graphify-first sequencing out of prompts.
2. Add an execution event log model (`turn`, `tool_call`, `tool_result`, `finish_reason`) for replay/debug.
3. Add server session compaction checkpoints (persisted summaries).
4. Split `executors.py` into:
   - `runtime_loop.py`,
   - `context_runtime.py`,
   - `result_mapper.py`,
   - `playbook_runner.py`.
