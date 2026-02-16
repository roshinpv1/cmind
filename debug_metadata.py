import sys
import os
import requests
import json
import subprocess
import signal
import time

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

API_URL = "http://localhost:8000"
REPO_ID = "99d2025f600a3a09"

def check_server():
    try:
        resp = requests.get(f"{API_URL}/api/v1/health", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False

def start_server():
    print("Starting server on port 8000...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "codemind.api.server:app", "--port", "8000"],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return process

def run_debug():
    server_process = None
    started_by_me = False
    
    if not check_server():
        server_process = start_server()
        started_by_me = True
        for i in range(30):
            if check_server():
                print("Server is ready.")
                break
            time.sleep(1)
        else:
            print("Server failed to start.")
            if server_process:
                server_process.kill()
            sys.exit(1)
    
    try:
        # Fetch catalog entries
        print(f"Fetching catalog entries for {REPO_ID}...")
        resp = requests.get(f"{API_URL}/api/v1/catalogs/{REPO_ID}")
        if resp.status_code != 200:
            print(f"Failed to fetch: {resp.status_code} {resp.text}")
            return

        entries = resp.json()
        if not entries:
            print("No entries found.")
            return

        print(f"Found {len(entries)} entries.")
        latest = entries[0] # Assuming first is interesting, or last?
        # Usually LanceDB returns in insertion order or search score order.
        # Let's dump all metadata fields
        
        for i, entry in enumerate(entries):
            print(f"\n--- Entry {i} ---")
            print(f"Created At: {entry.get('created_at')}")
            meta_str = entry.get("metadata")
            if meta_str:
                try:
                    if isinstance(meta_str, str):
                        meta = json.loads(meta_str)
                    else:
                        meta = meta_str
                    print(f"Metadata Keys: {list(meta.keys())}")
                    print(f"Content: {json.dumps(meta, indent=2)}")
                except:
                    print(f"Raw Metadata: {meta_str}")
            else:
                print("No metadata.")
                
        # Also check manifest
        print("\n--- Manifest Data ---")
        # Python check
        cmd = [
            sys.executable,
            "-c",
            f"import sys; sys.path.append('src'); from codemind.storage import ManifestManager; r = ManifestManager().get_repository_by_id('{REPO_ID}'); print(r.__dict__ if r else 'None')"
        ]
        subprocess.run(cmd, cwd=os.getcwd())

    finally:
        if server_process and started_by_me:
             os.kill(server_process.pid, signal.SIGTERM)

if __name__ == "__main__":
    run_debug()
