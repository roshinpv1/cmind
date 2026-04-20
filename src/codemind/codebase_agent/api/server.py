import logging
import uuid
import uvicorn
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..db.database import db
from .worker import run_analysis_job

# Set up logging for API
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Codebase Analyzer API", version="0.1.0")

class AnalyzeRequest(BaseModel):
    codebase_path: str
    task: str

class AnalyzeResponse(BaseModel):
    job_id: str
    status: str
    message: str

@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
def analyze_codebase(request: AnalyzeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    
    # Init pending job
    db.create_job(job_id, request.codebase_path, request.task)
    
    # Process synchronously but return immediately to client
    background_tasks.add_task(
        run_analysis_job, 
        job_id, 
        request.codebase_path, 
        request.task
    )
    
    return AnalyzeResponse(
        job_id=job_id,
        status="PENDING",
        message="Job scheduled successfully."
    )

@app.get("/api/v1/jobs/{job_id}")
def get_job_status(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "codebase_path": job["codebase_path"],
        "task": job["task"],
        "created_at": job["created_at"],
        "result": job.get("result")
    }

def start_server():
    """Script entrypoint for pyproject.toml"""
    logger.info("Starting Codebase Analyzer API server...")
    uvicorn.run("codebase_agent.api.server:app", host="0.0.0.0", port=8001, reload=True)

if __name__ == "__main__":
    start_server()
