import os
from .base import LLMProvider, LLMConfig
from .providers import LocalDriver, OllamaDriver, ApigeeDriver, EnterpriseDriver

def get_llm_client():
    # Priority order for auto-detection if LLM_PROVIDER is not set
    # 1. Apigee
    # 2. Local (LMStudio)
    # 3. Ollama
    
    forced_provider = os.environ.get("LLM_PROVIDER")
    
    # helper to create config
    def create_config(provider):
        if provider == LLMProvider.APIGEE:
            return LLMConfig(
                provider=provider,
                model=os.environ.get("APIGEE_MODEL", "gpt-4"),
                timeout=float(os.environ.get("APIGEE_TIMEOUT", "600"))
            )
        elif provider == LLMProvider.LOCAL:
            return LLMConfig(
                provider=provider,
                model=os.environ.get("LOCAL_LLM_MODEL", os.environ.get("LMSTUDIO_MODEL", "google/gemma-3n-e4b")),
                base_url=os.environ.get("LOCAL_LLM_URL", os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")),
                api_key=os.environ.get("LOCAL_LLM_API_KEY", "not-needed")
            )
        elif provider == LLMProvider.OLLAMA:
            return LLMConfig(
                provider=provider,
                model=os.environ.get("OLLAMA_MODEL", "llama-3.2-3b-instruct"),
                base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            )
        elif provider == LLMProvider.ENTERPRISE:
            return LLMConfig(
                provider=provider,
                model=os.environ.get("ENTERPRISE_LLM_MODEL", "llama-3.2-3b-instruct"),
                base_url=os.environ.get("ENTERPRISE_LLM_URL")
            )
        return None

    if forced_provider:
        try:
            p = LLMProvider(forced_provider.lower())
            config = create_config(p)
            if p == LLMProvider.APIGEE: return ApigeeDriver(config)
            if p == LLMProvider.LOCAL: return LocalDriver(config)
            if p == LLMProvider.OLLAMA: return OllamaDriver(config)
            if p == LLMProvider.ENTERPRISE: return EnterpriseDriver(config)
        except ValueError:
            print(f"Warning: Unknown LLM_PROVIDER '{forced_provider}'. Falling back to auto-detection.")

    # Auto-detection
    apigee_test = ApigeeDriver(create_config(LLMProvider.APIGEE))
    if apigee_test.is_available():
        return apigee_test
        
    local_config = create_config(LLMProvider.LOCAL)
    if local_config.base_url:
        return LocalDriver(local_config)
        
    ollama_config = create_config(LLMProvider.OLLAMA)
    if ollama_config.base_url:
        return OllamaDriver(ollama_config)

    # Final fallback to standard Local
    return LocalDriver(create_config(LLMProvider.LOCAL))
