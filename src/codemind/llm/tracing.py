"""
LangSmith Tracing Configuration.

Configures LangChain/LangGraph tracing via LangSmith.
When enabled, all LangChain operations (LLM calls, tool invocations,
graph transitions) are automatically traced without code changes.

Enable by setting env vars:
    LANGSMITH_API_KEY=your-key
    LANGSMITH_PROJECT=cmind  (optional, defaults to 'cmind')
"""

import os


def configure_tracing() -> dict:
    """Configure LangSmith tracing from environment variables.
    
    Returns:
        Status dict with tracing configuration info.
    """
    api_key = os.environ.get("LANGSMITH_API_KEY", "").strip()
    project = os.environ.get("LANGSMITH_PROJECT", "cmind").strip()
    
    if not api_key:
        return {
            "enabled": False,
            "reason": "LANGSMITH_API_KEY not set",
        }
    
    # Enable LangChain tracing v2
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = project
    
    # Ensure endpoint is set (default to LangSmith cloud)
    if not os.environ.get("LANGCHAIN_ENDPOINT"):
        os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
    
    return {
        "enabled": True,
        "project": project,
        "endpoint": os.environ["LANGCHAIN_ENDPOINT"],
    }
