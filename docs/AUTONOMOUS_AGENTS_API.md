# Autonomous Agent API Guide

## Overview

The autonomous agent system provides a skill-based AI agent that can interpret natural language goals and autonomously execute tasks using the codebase.

## How It Works

1. **User provides goal** - Natural language description
2. **Agent thinks** - LLM interprets goal and selects skill
3. **Agent acts** - Executes skill via executor
4. **Agent observes** - Processes results
5. **Repeat** - Until goal satisfied or max iterations
6. **Synthesize** - Returns final answer

## API Endpoints

### 1. Execute Autonomous Agent

```bash
POST /api/v1/agents/autonomous
```

**Request:**
```json
{
  "goal": "Find all authentication code in the repository",
  "repo_id": "abc123",
  "max_iterations": 10
}
```

**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "pending",
  "created_at": "2024-01-01T12:00:00",
  "goal": "Find all authentication code..."
}
```

### 2. Get Job Status

```bash
GET /api/v1/agents/autonomous/{job_id}/status
```

**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "running",
  "created_at": "2024-01-01T12:00:00",
  "goal": "Find all authentication code...",
  "iterations": 2,
  "steps_taken": 1
}
```

Statuses: `pending`, `running`, `completed`, `failed`

### 3. Get Job Result

```bash
GET /api/v1/agents/autonomous/{job_id}/result
```

**Response (Success):**
```json
{
  "job_id": "uuid-here",
  "status": "completed",
  "goal": "Find all authentication code...",
  "answer": "The authentication code is located in src/auth/ directory:\n1. src/auth/middleware.py - JWT verification\n2. src/auth/handlers.py - Login handlers",
  "steps_taken": 1,
  "iterations": 2,
  "skills_used": ["search_codebase"]
}
```

**Response (Failed):**
```json
{
  "job_id": "uuid-here",
  "status": "failed",
  "goal": "...",
  "error": "Error message here"
}
```

## Example Usage

### cURL

```bash
# 1. Start autonomous agent
JOB_ID=$(curl -X POST http://localhost:8000/api/v1/agents/autonomous \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Find database connection code",
    "repo_id": "my_repo",
    "max_iterations": 10
  }' | jq -r '.job_id')

echo "Job ID: $JOB_ID"

# 2. Poll status
curl http://localhost:8000/api/v1/agents/autonomous/$JOB_ID/status

# 3. Get result
curl http://localhost:8000/api/v1/agents/autonomous/$JOB_ID/result
```

### Python

```python
import httpx
import asyncio

async def run_autonomous_agent(goal: str, repo_id: str):
    async with httpx.AsyncClient() as client:
        # Create job
        response = await client.post(
            "http://localhost:8000/api/v1/agents/autonomous",
            json={
                "goal": goal,
                "repo_id": repo_id,
                "max_iterations": 10
            }
        )
        
        job_id = response.json()["job_id"]
        print(f"Job created: {job_id}")
        
        # Poll status
        while True:
            await asyncio.sleep(2)
            
            status_response = await client.get(
                f"http://localhost:8000/api/v1/agents/autonomous/{job_id}/status"
            )
            
            status = status_response.json()["status"]
            print(f"Status: {status}")
            
            if status in ["completed", "failed"]:
                break
        
        # Get result
        result_response = await client.get(
            f"http://localhost:8000/api/v1/agents/autonomous/{job_id}/result"
        )
        
        result = result_response.json()
        print(f"\nAnswer:\n{result['answer']}")
        
        return result

# Usage
asyncio.run(run_autonomous_agent(
    goal="Find all API endpoints",
    repo_id="my_repo"
))
```

## Available Skills

The agent can autonomously select from these skills:

1. **search_codebase** - Semantic search for code
2. **analyze_structure** - Repository structure analysis
3. **explain_code** - LLM-powered code explanation
4. **find_dependencies** - Graph-based dependency traversal
5. **generate_documentation** - Multi-step doc generation

## Example Goals

### Simple Search
```
"Find all database connection code"
"Locate authentication middleware"
"Find API endpoints"
```

### Analysis
```
"What are the main Python files in this repo?"
"Show me the project structure"
"How many TypeScript files are there?"
```

### Understanding
```
"Explain what src/auth/middleware.py does"
"What is the purpose of utils.py?"
```

### Documentation
```
"Generate a README for this project"
"Create API documentation"
```

## Configuration

- **max_iterations**: Safety limit (default: 10, max: 50)
- **repo_id**: Must be already indexed

## Error Handling

- `404` - Job not found
- `425` - Job not yet complete (still running)
- `503` - Agent system not initialized

## Notes

- Jobs run in background (async)
- Use polling for status updates
- Results stored in-memory (restart clears jobs)
- Agent is fully autonomous (no human intervention needed)
