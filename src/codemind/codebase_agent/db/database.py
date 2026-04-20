import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

class DatabaseManager:
    """Manages SQLite connections and job persistence."""
    
    def __init__(self, db_path: str = "agent_jobs.db"):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    codebase_path TEXT NOT NULL,
                    task TEXT NOT NULL,
                    result_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def create_job(self, job_id: str, codebase_path: str, task: str) -> None:
        """Create a new job in PENDING status."""
        conn = self._get_conn()
        with conn:
            conn.execute(
                "INSERT INTO jobs (job_id, status, codebase_path, task) VALUES (?, ?, ?, ?)",
                (job_id, "PENDING", codebase_path, task)
            )

    def update_job_status(self, job_id: str, status: str, result_dict: Optional[Dict[str, Any]] = None) -> None:
        """Update job status and optionally the result JSON."""
        conn = self._get_conn()
        
        result_json = json.dumps(result_dict) if result_dict is not None else None
        
        with conn:
            if result_json is not None:
                conn.execute(
                    "UPDATE jobs SET status = ?, result_json = ? WHERE job_id = ?",
                    (status, result_json, job_id)
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status = ? WHERE job_id = ?",
                    (status, job_id)
                )

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a job by ID."""
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
        
        if not row:
            return None
            
        result = dict(row)
        if result.get("result_json"):
            try:
                result["result"] = json.loads(result["result_json"])
            except json.JSONDecodeError:
                result["result"] = None
        else:
            result["result"] = None
            
        # Don't return raw string JSON format, just parsed dict
        result.pop("result_json", None)
            
        return result

db = DatabaseManager()
