from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List

class LLMProvider(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    LOCAL = "local"
    OLLAMA = "ollama"
    APIGEE = "apigee"
    ENTERPRISE = "enterprise"

@dataclass
class LLMConfig:
    provider: LLMProvider
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 4000
    timeout: float = 600.0 # Seconds

class LLMDriver(ABC):
    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass
