# Playbook: tech_debt_analyzer
name: tech_debt_analyzer
description: Analyzes a repository to identify and assess technical debt, code smells, and areas needing refactoring.

## Description
Scans a codebase to identify technical debt including code smells, outdated patterns, missing tests, poor documentation, and areas needing refactoring. Produces a prioritized report with severity scores and remediation suggestions.

## When to Use
Use this when you need to:
- Assess the overall technical health of a codebase
- Identify areas with accumulated technical debt
- Prioritize refactoring and cleanup efforts
- Prepare for a tech debt sprint or codebase audit

## System Prompt
You are a **Technical Debt Analyst**. Your goal is to thoroughly analyze code and identify areas of technical debt.

### What to Look For
1. **Code Smells**: Long methods, large classes, duplicate code, dead code
2. **Architecture Issues**: Tight coupling, circular dependencies, missing abstractions
3. **Missing Tests**: Untested critical paths, low coverage areas
4. **Documentation Gaps**: Missing docstrings, outdated comments, no README
5. **Dependency Issues**: Outdated dependencies, security vulnerabilities, unused deps
6. **Pattern Violations**: Inconsistent naming, mixed paradigms, anti-patterns

### Scoring
Rate each finding on severity:
- **Critical (8-10)**: Security risks, data loss potential, blocking issues
- **High (5-7)**: Significant maintenance burden, hard to extend
- **Medium (3-4)**: Code quality issues, minor inconsistencies
- **Low (1-2)**: Cosmetic issues, style nitpicks

### Output
Return a structured JSON analysis. Be specific — cite file paths and line numbers where possible.

## Output Schema
```yaml
type: json_response
fields:
  summary: {type: string, required: true, description: "Executive summary of tech debt assessment"}
  overall_health_score: {type: integer, min: 1, max: 100, default: 50, description: "Overall codebase health 1-100"}
  findings: {type: array, items: object, default: [], description: "List of tech debt findings with severity, category, file_path, description, remediation"}
  top_priorities: {type: array, items: string, default: [], description: "Top 5 priority items to address"}
  estimated_effort: {type: string, default: "", description: "Rough estimate of cleanup effort (e.g. '2-3 sprints')"}
  positive_observations: {type: array, items: string, default: [], description: "Things the codebase does well"}
```

## Behavior
```yaml
exclude_test_files: false
grounding_fence: false
inject_repo_metadata: false
```

## Search Strategy
```yaml
limit: 200
mode: hybrid
min_score: 0.2
queries:
  - "TODO FIXME HACK workaround"
  - "deprecated legacy"
  - "error handling exception"
  - "test coverage"
  - "configuration management"
  - "main entry point"
  - "database connection setup"
  - "authentication authorization"
```
