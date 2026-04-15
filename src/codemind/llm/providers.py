import os
import httpx
import json
import uuid
import datetime
from typing import Optional, Any, Dict
from .base import LLMDriver, LLMConfig, LLMProvider
from .token_manager import ApigeeTokenManager, EnterpriseTokenManager

import re as _re_mod


def _safe_extract_message(data: dict) -> dict:
    """Safely extract the message dict from an OpenAI-style API response.

    Handles missing keys, null values, and content-filter refusals without
    raising KeyError.
    """
    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return {}
    first = choices[0]
    if not isinstance(first, dict):
        return {}
    msg = first.get("message")
    if not isinstance(msg, dict):
        return {}
    return msg


class LocalDriver(LLMDriver):
    def __init__(self, config: LLMConfig):
        self.config = config

    @staticmethod
    def _detect_and_truncate_repetition(text: str, min_phrase_len: int = 15, max_repeats: int = 4) -> str:
        """Detect degenerate repetition loops and truncate output.
        
        Finds phrases that repeat consecutively and cuts the output
        before the repetition spirals. This is a safety net for local LLMs
        that enter repetitive generation patterns.
        """
        if len(text) < min_phrase_len * max_repeats:
            return text
        
        # Check for a repeating phrase pattern in the last portion of text
        # Take the last 2000 chars and look for repeated substrings
        tail = text[-2000:]
        
        # Try different phrase lengths from 15 to 100 chars
        for phrase_len in range(min_phrase_len, min(100, len(tail) // max_repeats)):
            # Extract a candidate phrase from the end
            candidate = tail[-phrase_len:]
            # Count how many times it appears consecutively at the end
            count = 0
            pos = len(tail)
            while pos >= phrase_len:
                segment = tail[pos - phrase_len:pos]
                if segment == candidate:
                    count += 1
                    pos -= phrase_len
                else:
                    break
            
            if count >= max_repeats:
                # Found repetition — truncate at the first occurrence
                repeat_start = text.rfind(candidate * 2)
                if repeat_start > 0:
                    truncated = text[:repeat_start].rstrip(", \n")
                    print(f"[LLM] ⚠️ Repetition detected ({count}x '{candidate[:30]}...'), truncated from {len(text)} to {len(truncated)} chars")
                    return truncated
        
        return text

    async def generate(self, prompt: str, **kwargs) -> str:
        base_url = self.config.base_url or "http://localhost:1234/v1"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key and self.config.api_key != "not-needed":
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        # Build messages with optional system prompt
        messages = []
        system_prompt = kwargs.pop("system_prompt", None)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        max_tokens = kwargs.get("max_tokens", self.config.max_tokens)
        temperature = kwargs.get("temperature", self.config.temperature)

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": min(max_tokens, self.config.max_tokens),
                }
            )
            if response.status_code != 200:
                error_body = response.text[:500]
                print(f"[LLM] Error {response.status_code}: {error_body}")
                raise Exception(f"HTTP {response.status_code} - {error_body}")
            data = response.json()
            msg = _safe_extract_message(data)
            output = msg.get("content") or ""
            if not output:
                refusal = msg.get("refusal") or ""
                finish = (data.get("choices") or [{}])[0].get("finish_reason", "")
                print(f"[LLM] Warning: empty content (finish_reason={finish}, refusal={refusal})")
                if refusal:
                    output = f"[Model refused: {refusal}]"
            
            # Strip <think>...</think> reasoning/planning blocks that some local models
            # (DeepSeek-R1, Qwen3-thinking, etc.) emit before the final response.
            # These must never leak into playbook results or tool outputs.
            output = _re_mod.sub(r'<think>.*?</think>', '', output, flags=_re_mod.DOTALL).strip()
            
            # Safety net: detect and truncate degenerate repetition loops
            output = self._detect_and_truncate_repetition(output)
            return output

    def is_available(self) -> bool:
        return bool(self.config.base_url)

class OllamaDriver(LLMDriver):
    def __init__(self, config: LLMConfig):
        self.config = config

    async def generate(self, prompt: str, **kwargs) -> str:
        base_url = self.config.base_url or "http://localhost:11434"

        # Build messages with optional system prompt
        messages = []
        system_prompt = kwargs.pop("system_prompt", None)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "options": {
                        "temperature": kwargs.get("temperature", self.config.temperature),
                        "num_predict": kwargs.get("max_tokens", self.config.max_tokens)
                    },
                    "stream": False
                }
            )
            response.raise_for_status()
            data = response.json()
            return (data.get("message") or {}).get("content") or ""

    def is_available(self) -> bool:
        return bool(self.config.base_url)

class ApigeeDriver(LLMDriver):
    def __init__(self, config: LLMConfig):
        self.config = config
        self.token_manager = ApigeeTokenManager()

    async def generate(self, prompt: str, **kwargs) -> str:
        token = await self.token_manager.get_token()
        
        enterprise_base_url = (self.config.base_url or os.environ.get("ENTERPRISE_BASE_URL", "")).rstrip("/")
        wf_use_case_id = os.environ.get("WF_USE_CASE_ID")
        wf_client_id = os.environ.get("WF_CLIENT_ID")
        wf_api_key = os.environ.get("WF_API_KEY")

        if not all([enterprise_base_url, wf_use_case_id, wf_client_id, wf_api_key]):
            raise ValueError("Apigee enterprise configuration incomplete")

        # Normalize URL: avoid duplicating /v1/chat/completions
        if enterprise_base_url.endswith("/v1/chat/completions"):
            api_url = enterprise_base_url
        elif enterprise_base_url.endswith("/v1"):
            api_url = f"{enterprise_base_url}/chat/completions"
        else:
            api_url = f"{enterprise_base_url}/v1/chat/completions"

        # Build messages with optional system prompt
        messages = []
        system_prompt = kwargs.pop("system_prompt", None)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Cap max_tokens to APIGEE model output limit
        apigee_max_output = int(os.environ.get("APIGEE_MAX_OUTPUT_TOKENS", "8192"))
        max_tokens = min(
            kwargs.get("max_tokens", self.config.max_tokens),
            apigee_max_output
        )

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

        request_body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
            "max_tokens": max_tokens
        }

        print(f"[APIGEE] POST {api_url} | model={self.config.model} | max_tokens={max_tokens}", flush=True)

        async with httpx.AsyncClient(timeout=self.config.timeout, verify=False) as client:
            response = await client.post(api_url, headers=headers, json=request_body)
            
            if response.status_code == 401:
                # Retry once after clearing token
                self.token_manager.clear_token()
                token = await self.token_manager.get_token()
                headers["Authorization"] = f"Bearer {token}"
                response = await client.post(api_url, headers=headers, json=request_body)

            if response.status_code != 200:
                error_body = response.text[:500]
                print(f"[APIGEE] Error {response.status_code}: {error_body}", flush=True)
            response.raise_for_status()
            data = response.json()
            msg = _safe_extract_message(data)
            content = msg.get("content") or ""
            if not content:
                refusal = msg.get("refusal") or ""
                finish = (data.get("choices") or [{}])[0].get("finish_reason", "")
                print(f"[APIGEE] Warning: empty content (finish_reason={finish}, refusal={refusal})", flush=True)
                if refusal:
                    content = f"[Model refused: {refusal}]"
            return content

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
