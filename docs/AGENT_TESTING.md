# Documentation Generator Agent - Test Script

## Quick Test

```bash
# 1. Generate README
curl -X POST http://localhost:8000/api/v1/agents/execute \
  -H "Content-Type: application/json" \
  -d '{
    "agent_type": "doc_generator",
    "repo_id": "1dd450cb2ecd63a9",
    "task": "generate_readme",
    "config": {
      "doc_type": "readme",
      "include_examples": true
    }
  }'

# Response: {"job_id": "xxx", "status": "pending", "created_at": "..."}

# 2. Check status (replace JOB_ID)
curl http://localhost:8000/api/v1/agents/JOB_ID/status

# 3. Get result when completed
curl http://localhost:8000/api/v1/agents/JOB_ID/result
```

## Python Example

```python
import requests
import time

# Start generation
response = requests.post(
    "http://localhost:8000/api/v1/agents/execute",
    json={
        "agent_type": "doc_generator",
        "repo_id": "1dd450cb2ecd63a9",
        "task": "generate_readme",
        "config": {
            "doc_type": "readme",
            "include_examples": True
        }
    }
)

job_id = response.json()["job_id"]
print(f"Job ID: {job_id}")

# Poll for completion
while True:
    status_response = requests.get(
        f"http://localhost:8000/api/v1/agents/{job_id}/status"
    )
    status = status_response.json()["status"]
    print(f"Status: {status}")
    
    if status in ["completed", "failed"]:
        break
    
    time.sleep(2)

# Get result
result_response = requests.get(
    f"http://localhost:8000/api/v1/agents/{job_id}/result"
)
result = result_response.json()

if result["status"] == "completed":
    print("\n=== Generated README ===\n")
    print(result["result"])
else:
    print(f"Error: {result.get('error')}")
```

## Features

The README generator will:
- Analyze repository structure
- Identify main components (API, config, routes)
- Extract key features
- Generate comprehensive README with LLM
- Include installation, usage, structure overview
