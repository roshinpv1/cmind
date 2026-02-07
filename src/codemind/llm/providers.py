import os
import httpx
import json
import uuid
import datetime
from typing import Optional, Any, Dict
from .base import LLMDriver, LLMConfig, LLMProvider
from .token_manager import ApigeeTokenManager, EnterpriseTokenManager

class LocalDriver(LLMDriver):
    def __init__(self, config: LLMConfig):
        self.config = config

    async def generate(self, prompt: str, **kwargs) -> str:
        base_url = self.config.base_url or "http://localhost:1234/v1"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key and self.config.api_key != "not-needed":
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": kwargs.get("temperature", self.config.temperature),
                    "max_tokens": kwargs.get("max_tokens", self.config.max_tokens)
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def is_available(self) -> bool:
        return bool(self.config.base_url)

class OllamaDriver(LLMDriver):
    def __init__(self, config: LLMConfig):
        self.config = config

    async def generate(self, prompt: str, **kwargs) -> str:
        base_url = self.config.base_url or "http://localhost:11434"
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {
                        "temperature": kwargs.get("temperature", self.config.temperature),
                        "num_predict": kwargs.get("max_tokens", self.config.max_tokens)
                    },
                    "stream": False
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]

    def is_available(self) -> bool:
        return bool(self.config.base_url)

class ApigeeDriver(LLMDriver):
    def __init__(self, config: LLMConfig):
        self.config = config
        self.token_manager = ApigeeTokenManager()

    async def generate(self, prompt: str, **kwargs) -> str:
        token = await self.token_manager.get_token()
        
        enterprise_base_url = os.environ.get("ENTERPRISE_BASE_URL")
        wf_use_case_id = os.environ.get("WF_USE_CASE_ID")
        wf_client_id = os.environ.get("WF_CLIENT_ID")
        wf_api_key = os.environ.get("WF_API_KEY")

        if not all([enterprise_base_url, wf_use_case_id, wf_client_id, wf_api_key]):
            raise ValueError("Apigee enterprise configuration incomplete")

        headers = {
            "x-wf-request-date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "Authorization": f"Bearer {token}",
            "x-request-id": str(uuid.uuid4()),
            "x-correlation-id": str(uuid.uuid4()),
            "X-WF-client-id": wf_client_id,
            "X-WF-api-key": wf_api_key,
            "X-WF-usecase-id": wf_use_case_id,
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(timeout=self.config.timeout, verify=False) as client:
            response = await client.post(
                f"{enterprise_base_url}/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": kwargs.get("temperature", self.config.temperature),
                    "max_tokens": kwargs.get("max_tokens", self.config.max_tokens)
                }
            )
            
            if response.status_code == 401:
                # Retry once after clearing token
                self.token_manager.clear_token()
                token = await self.token_manager.get_token()
                headers["Authorization"] = f"Bearer {token}"
                response = await client.post(
                    f"{enterprise_base_url}/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self.config.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": kwargs.get("temperature", self.config.temperature),
                        "max_tokens": kwargs.get("max_tokens", self.config.max_tokens)
                    }
                )

            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def is_available(self) -> bool:
        required_vars = [
            'APIGEE_NONPROD_LOGIN_URL', 'APIGEE_CONSUMER_KEY', 'APIGEE_CONSUMER_SECRET',
            'ENTERPRISE_BASE_URL', 'WF_USE_CASE_ID', 'WF_CLIENT_ID', 'WF_API_KEY'
        ]
class EnterpriseDriver(LLMDriver):
    def __init__(self, config: LLMConfig):
        self.config = config
        self.token_manager = EnterpriseTokenManager()

    async def generate(self, prompt: str, **kwargs) -> str:
        token = await self.token_manager.get_token()
        if not self.config.base_url:
            raise ValueError("Enterprise base URL not provided")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Add additional headers from env if present
        extra_headers_str = os.environ.get("ENTERPRISE_LLM_HEADERS", "{}")
        try:
            extra_headers = json.loads(extra_headers_str)
            headers.update(extra_headers)
        except:
            pass

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                self.config.base_url,
                headers=headers,
                json={
                    "model": self.config.model,
                    "prompt": prompt, # Enterprise often uses 'prompt' instead of messages
                    "temperature": kwargs.get("temperature", self.config.temperature),
                    "max_tokens": kwargs.get("max_tokens", self.config.max_tokens)
                }
            )
            response.raise_for_status()
            data = response.json()
            # Handle variations in response format
            return data.get("response") or data.get("choices", [{}])[0].get("message", {}).get("content") or data.get("content") or ""

    def is_available(self) -> bool:
        return bool(os.environ.get("ENTERPRISE_LLM_TOKEN") and self.config.base_url)
