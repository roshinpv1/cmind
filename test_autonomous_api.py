"""
End-to-end test for autonomous agent API.

Tests the full flow:
1. POST /api/v1/agents/autonomous
2. GET /status (poll)
3. GET /result
"""

import asyncio
import httpx


async def test_autonomous_api():
    """Test autonomous agent via API."""
    
    base_url = "http://localhost:8000"
    
    print("=" * 60)
    print("Testing Autonomous Agent API")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        # Step 1: Create autonomous job
        print("\n1. Creating autonomous job...")
        
        request = {
            "goal": "Find all Python files in the repository",
            "repo_id": "test_repo",
            "max_iterations": 5
        }
        
        response = await client.post(
            f"{base_url}/api/v1/agents/autonomous",
            json=request
        )
        
        if response.status_code != 200:
            print(f"✗ Failed to create job: {response.status_code}")
            print(response.text)
            return
        
        job_data = response.json()
        job_id = job_data["job_id"]
        
        print(f"✓ Job created: {job_id}")
        print(f"  Status: {job_data['status']}")
        print(f"  Goal: {job_data['goal']}")
        
        # Step 2: Poll status
        print("\n2. Polling job status...")
        
        max_polls = 20
        for i in range(max_polls):
            await asyncio.sleep(2)
            
            response = await client.get(
                f"{base_url}/api/v1/agents/autonomous/{job_id}/status"
            )
            
            if response.status_code != 200:
                print(f"✗ Failed to get status: {response.status_code}")
                return
            
            status_data = response.json()
            status = status_data["status"]
            
            print(f"  Poll {i+1}: {status}", end="")
            if status_data.get("iterations"):
                print(f" (iterations: {status_data['iterations']})", end="")
            print()
            
            if status == "completed" or status == "failed":
                break
        
        # Step 3: Get result
        print("\n3. Getting result...")
        
        response = await client.get(
            f"{base_url}/api/v1/agents/autonomous/{job_id}/result"
        )
        
        if response.status_code != 200:
            print(f"✗ Failed to get result: {response.status_code}")
            print(response.text)
            return
        
        result_data = response.json()
        
        print(f"\n{'='*60}")
        print("🎯 RESULT")
        print(f"{'='*60}")
        print(f"\nGoal: {result_data['goal']}")
        print(f"Status: {result_data['status']}")
        
        if result_data.get('answer'):
            print(f"\nAnswer:\n{result_data['answer']}")
        
        print(f"\nSteps Taken: {result_data.get('steps_taken')}")
        print(f"Iterations: {result_data.get('iterations')}")
        print(f"Skills Used: {result_data.get('skills_used')}")
        
        if result_data.get('error'):
            print(f"\nError: {result_data['error']}")
        
        print(f"\n{'='*60}")
        print("✅ API test complete!")
        print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(test_autonomous_api())
