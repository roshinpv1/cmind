---
name: migration_analysis
description: Analyzes a codebase to identify migration targets, dependencies, and structural bottlenecks.
when_to_use: When a user wants to plan a technical migration (e.g. monolith to microservices, language upgrade, cloud migration).
version: 1.0
category: analysis
complexity_level: high
max_iterations: 4
---

## Description
Scans the codebase to determine the current architecture, identifies dependencies that are difficult to migrate, and outputs a structured migration strategy and blockers list.

## System Prompt
You are an expert Cloud and Migration Architect. Your job is to analyze the retrieved codebase and generate a comprehensive Migration Analysis document. 
Focus on:
1. Identifying tightly coupled components and legacy dependencies.
2. Spotting hardcoded configurations, local file system dependencies, or stateful patterns.
3. Proposing a logical, phased approach for migrating to the target architecture specified by the user's goal.

Provide a detailed, practical assessment.

## Search Strategy
```yaml
queries:
  - "database connection configs OR API endpoints OR external service integrations"
  - "core business logic AND tightly coupled modules"
  - "state management, caching, or file system access"
limit: 15
mode: hybrid
max_context_tokens: 12000
```

## Output Schema
```yaml
type: object
properties:
  current_architecture_summary:
    type: string
    description: Summary of the current architectural state and its main flaws for the target environment.
  technical_blockers:
    type: array
    items:
      type: string
    description: Specific code patterns or dependencies that will block migration.
  migration_phases:
    type: array
    items:
      type: object
      properties:
        phase_name:
          type: string
          description: Name of the phase (e.g., "Phase 1: Decouple DB").
        components:
          type: array
          items:
            type: string
            description: Files or modules involved in this phase.
        actions:
          type: array
          items:
            type: string
            description: Specific structural changes required.
```
