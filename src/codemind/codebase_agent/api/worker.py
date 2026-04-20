import logging
from pathlib import Path

from ..agents.manager import AgentManager
from ..config.configuration import ConfigurationManager
from ..db.database import db

logger = logging.getLogger(__name__)

def run_analysis_job(job_id: str, codebase_path: str, task: str) -> None:
    """Run codebase analysis in background and update DB status."""
    try:
        logger.info(f"Starting job {job_id} for path {codebase_path}")
        db.update_job_status(job_id, "RUNNING")
        
        path_obj = Path(codebase_path).resolve()
        
        # Initialize config using the current working directory (where .env is located)
        config_manager = ConfigurationManager(Path.cwd())
        config_manager.load_environment()
        
        missing_keys = config_manager.validate_configuration()
        if missing_keys:
            error_msg = f"Missing configuration keys: {', '.join(missing_keys)}"
            logger.error(f"Job {job_id} failed: {error_msg}")
            db.update_job_status(job_id, "FAILED", {"error": error_msg})
            return
            
        agent_manager = AgentManager(config_manager)
        agent_manager.initialize_agents(str(path_obj))
        
        # Run process
        result, statistics = agent_manager.process_query_with_review_cycle(task, str(path_obj))
        
        final_payload = {
            "analysis_result": result,
            "statistics": statistics,
        }
        
        logger.info(f"Job {job_id} completed successfully.")
        db.update_job_status(job_id, "COMPLETED", final_payload)
        
    except Exception as e:
        logger.exception(f"Exception during job {job_id}")
        db.update_job_status(job_id, "FAILED", {"error": str(e)})

