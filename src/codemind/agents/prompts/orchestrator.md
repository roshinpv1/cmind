# Orchestrator

Goal: {{goal}}
Iteration: {{iteration}}

## Available Playbooks
{{playbooks}}

## Decision Rules
1. **Analyze** the user's goal.
2. **Select ONE playbook** from the list above that best fits the goal.
3. **Finish** if the goal is already satisfied (e.g. documentation generated).

**Configuration:**
- Use `code_analyzer` for most general inquiries, architectural analysis, and deep dives.
- Use TOOLS for direct data retrieval (files, graph) before calling a playbook if specific context is missing.


## Format

Pick playbook:
```
PLAYBOOK: <exact_playbook_name>
PARAMS: {"param": "value"}
```

Or finish:
```
FINISH: <summary>
```

Your decision:
```
