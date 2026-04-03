---
name: "v2_code_generator_example"
version: 2.0
category: generation
complexity: hard
max_iterations: 10
description: "A comprehensive example showing the new Version 2 Markdown format for Playbooks."
when_to_use: "Use this playbook when you need to completely rewrite an entire test suite or generate code."
---

# Playbook: v2_code_generator_example

## Description
This playbook demonstrates how you can take advantage of CodeMind's new LangGraph agent system using purely Markdown directives. By formatting your playbook correctly, the planner and executor agents can perfectly constrain and evaluate the AI's output.

## When to Use
Use this example as your master template when converting legacy prompt structures to the new system, particularly for playbooks that generate output instead of just reading code.

## System Prompt
You are an elite Staff Software Engineer. Your job is to generate functional complete test coverage for any provided module. 
You must think step-by-step and write robust tests using pytest. Do not output anything except the JSON response.

## Expected Input
Provide the name of the file and the framework you want me to write tests for (e.g., `src/utils.py`, `pytest`).

## Search Strategy
```yaml
mode: hybrid
limit: 250
min_score: 0.5
queries:
  - "Find all utility functions in the module"
  - "Find existing test fixtures and mock setups"
```

## Behavior
```yaml
# Don't try to analyze tests, only production logic
exclude_test_files: true

# The AI is not allowed to read anything far outside the search results
grounding_fence: true

# Share repo metadata (e.g., last commit, primary language)
inject_repo_metadata: true

# Set this to true if you are debugging failed JSON schema parses
skip_schema_validation: false
```

## Output Schema
```yaml
type: json_response
description: Generated test code and explanation
fields:
  test_code:
    type: string
    description: The complete string of the resulting test file
  modules_mocked:
    type: array
    description: A list of dependencies that were mocked
    items:
      type: string
  confidence_score:
    type: number
    description: A score from 0.0 to 1.0 of how confident you are that tests will pass
```

## Anti-Patterns
* Do not use standard `unittest` modules if the input specifies `pytest`.
* Do not leave `# TODO` or `pass` blocks inside the test files.

## Quality Rubric
### Test Isolation
All tests must run independently without polluting global state.
### Mocking Discipline
External DB or network calls must be strictly mocked out using `unittest.mock.patch`.

## Evaluation
* Ensure that the `test_code` string does not contain any Markdown code fences (e.g., ` ```python `).
* Verify that every function defined in the source file has at least one corresponding test function.
