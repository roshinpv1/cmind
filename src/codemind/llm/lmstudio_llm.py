
import os, httpx
BASE=os.getenv("LMSTUDIO_BASE_URL","http://localhost:1234/v1")
MODEL=os.getenv("LMSTUDIO_MODEL","qwen2.5-coder:7b")

class LMStudioLLM:
    async def generate(self, prompt):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(f"{BASE}/chat/completions", json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a code reasoning engine"},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2
                })
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Error executing LLM request: {e}"
