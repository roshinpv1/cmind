---
name: analyze_tech_debt
version: "1.0"
description: Identifies and assesses technical debt, code smells, and areas needing refactoring
category: analysis
complexity: medium
max_iterations: 5
---

# Playbook: analyze_tech_debt
name: analyze_tech_debt
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

## Examples

### Example: "Assess tech debt in this Node.js API"

**Input goal**: "Analyze technical debt in this codebase"

**Expected output**:
```json
{
  "summary": "The codebase has moderate tech debt concentrated in the API layer. Key issues include 3 god classes exceeding 500 lines, missing error handling in 12 endpoints, and 0% test coverage on payment processing. Overall health: 58/100.",
  "overall_health_score": 58,
  "findings": [
    {"severity": 8, "category": "Code Smell", "file_path": "src/controllers/user.py", "description": "UserController is 847 lines with 23 methods — classic god class", "remediation": "Split into UserAuthController, UserProfileController, UserPrefsController"},
    {"severity": 7, "category": "Missing Tests", "file_path": "src/services/payment.py", "description": "Zero test coverage on critical payment processing logic", "remediation": "Add unit tests for process_payment(), refund(), and webhook handlers"},
    {"severity": 5, "category": "Dependency", "file_path": "requirements.txt", "description": "Django 3.2 is EOL — security patches no longer provided", "remediation": "Upgrade to Django 4.2 LTS"}
  ],
  "top_priorities": [
    "Add test coverage for payment processing (security-critical)",
    "Upgrade Django to 4.2 LTS (EOL dependency)",
    "Refactor UserController god class into focused controllers"
  ],
  "estimated_effort": "2-3 sprints for critical items, 4-5 sprints for full cleanup",
  "positive_observations": [
    "Consistent use of type hints throughout the codebase",
    "Good separation of concerns in the data access layer",
    "CI/CD pipeline is well-configured with automated linting"
  ]
}
```

## Anti-Patterns
- Do NOT report cosmetic issues as critical — use the severity scoring accurately
- Do NOT claim a file has issues without citing the specific file path
- Do NOT leave positive_observations empty — every codebase has strengths
- Do NOT give a health score without justifying it with specific findings
- Do NOT suggest refactoring without explaining the specific remediation

## Quality Rubric
| Criterion | Weight | Pass Condition |
|---|---|---|
| Specificity | 30% | Every finding cites a file path |
| Severity accuracy | 25% | Severity scores match the described impact |
| Actionability | 25% | Every finding has a concrete remediation |
| Balance | 20% | Includes both findings and positive observations |

## Evaluation
- findings must contain >= 3 findings
- summary must not be empty
- top_priorities must not be empty
- positive_observations must not be empty

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
