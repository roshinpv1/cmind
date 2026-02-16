import subprocess
import time
import os
import sys

BASE_URL = "http://localhost:8000/api/v1"

def start_server():
    print("Starting server...")
    subprocess.run(["pkill", "-f", "uvicorn"], stderr=subprocess.DEVNULL)
    time.sleep(2)
    stdout_file = open("server_stdout_seed.log", "w")
    stderr_file = open("server_stderr_seed.log", "w")
    process = subprocess.Popen(
        ["uvicorn", "codemind.api.server:app", "--port", "8000"],
        stdout=stdout_file,
        stderr=stderr_file,
        cwd=os.getcwd()
    )
    time.sleep(5)
    return process

def wait_for_server():
    for _ in range(10):
        try:
            requests.get("http://localhost:8000/health")
            print("Server is ready.")
            return True
        except:
            time.sleep(1)
    return False

def seed_catalog():
    print("Seeding catalog entry for 'CodeMind'...")
    payload = {
        "playbook_name": "catalog_generator",
        "prompt": "Create a catalog entry for CodeMind. It is an autonomous AI agent for software development.",
        "repo_id": "codemind_repo_1",
        "context": {
            "name": "CodeMind",
            "repo_url": "https://github.com/example/codemind",
            "branch": "main",
            "description": "Autonomous coding agent"
        }
    }
    
    try:
        res = requests.post(f"{BASE_URL}/agents/playbook", json=payload)
        res.raise_for_status()
        print("Seed success:", res.json().get("success"))
        print(res.json().get("result"))
    except Exception as e:
        print(f"Seed failed: {e}")

if __name__ == "__main__":
    srv = start_server()
    if wait_for_server():
        seed_catalog()
    srv.terminate()
