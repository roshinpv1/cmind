"""
LangChain BaseChatModel wrapper for existing cmind LLMDriver.

This bridges our custom LLM providers (LocalDriver, OllamaDriver, 
ApigeeDriver, EnterpriseDriver) to LangChain's BaseChatModel interface,
enabling LangGraph features like bind_tools(), ToolNode, and 
with_structured_output() without replacing any LLM provider code.

Since our LLM drivers don't support native tool/function calling,
this wrapper implements prompt-based tool calling: tool schemas are 
injected into the system prompt, and JSON tool calls are parsed from 
the LLM's text output.

Usage:
    from codemind.llm.chat_wrapper import CmindChatModel
    from codemind.llm.factory import get_llm_client
    
    driver = get_llm_client()
    chat_model = CmindChatModel(driver=driver)
    
    # Now works with LangGraph features
    bound = chat_model.bind_tools([...])
    structured = chat_model.with_structured_output(MySchema)
"""

import asyncio
import json
import os
import re
import uuid
from copy import deepcopy
from typing import Any, Optional, List, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.runnables import Runnable

from .base import LLMDriver, LLMConfig
from .content_guard import get_content_guard
from .token_counter import count_tokens, truncate_to_tokens


# ─── Tool Call Parsing ────────────────────────────────────────────────────────

def _repair_truncated_json(text: str) -> dict | None:
    """Try to repair JSON truncated by token limits (missing closing brackets).

    Local models often emit valid tool_calls JSON but run out of tokens before
    closing all brackets.  We detect the last complete string value and close
    any outstanding brackets/braces.
    """
    s = text.strip()
    if not s.startswith("{"):
        return None

    # Count open brackets/braces
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    last_good = -1

    for i, c in enumerate(s):
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth_brace += 1
        elif c == '}':
            depth_brace -= 1
        elif c == '[':
            depth_bracket += 1
        elif c == ']':
            depth_bracket -= 1

        if depth_brace == 0 and depth_bracket == 0:
            last_good = i
            break

    if last_good > 0:
        return None  # already complete, no repair needed

    # Try appending missing closers
    # First, find the last valid string boundary to truncate at
    # Then close any open structures
    repair = s
    # If the last non-whitespace is an unfinished string, close it
    stripped = repair.rstrip()
    if stripped and stripped[-1] not in ('"', '}', ']', ',', ':'):
        # Likely mid-string; close the string
        repair = stripped + '"'

    # Recount and add missing closers
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    for c in repair:
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth_brace += 1
        elif c == '}':
            depth_brace -= 1
        elif c == '[':
            depth_bracket += 1
        elif c == ']':
            depth_bracket -= 1

    repair += ']' * depth_bracket + '}' * depth_brace

    try:
        obj = json.loads(repair)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _extract_leading_json_dict(text: str) -> tuple[dict | None, str | None]:
    """Parse the first complete JSON object from text using balanced decoding.

    Regex-based extraction fails on nested objects (e.g. tool ``args``), which
    caused the planner to treat raw ``{"tool_calls":[...]}`` as the final answer.
    """
    s = text.strip()
    start = s.find("{")
    if start < 0:
        return None, None
    dec = json.JSONDecoder()
    try:
        obj, end = dec.raw_decode(s[start:])
        if isinstance(obj, dict):
            return obj, s[start : start + end]
    except json.JSONDecodeError:
        # Try repair for truncated JSON (common with local models)
        repaired = _repair_truncated_json(s[start:])
        if repaired:
            return repaired, s[start:]
    return None, None


def _resolve_tool_name(data: dict, available_tools: dict) -> str:
    """Return the canonical tool name from any of the common name-key aliases, or ''."""
    for key in ("name", "tool_name", "tool", "action"):
        raw = data.get(key)
        if raw and isinstance(raw, str) and raw in available_tools:
            return raw
    return ""


def _resolve_args(data: dict) -> dict:
    """Return the args dict from any of the common arg-key aliases."""
    for key in ("args", "arguments", "parameters", "params"):
        v = data.get(key)
        if isinstance(v, dict):
            return v
    # Fallback: strip all known meta-keys and treat the remainder as args
    meta = {"name", "tool_name", "tool", "action", "phase",
            "args", "arguments", "parameters", "params", "id", "type"}
    return {k: v for k, v in data.items() if k not in meta}


def _parse_tool_json(data: dict, available_tools: dict) -> list:
    """Try to interpret a JSON object as tool call(s).

    Handles every format that local models commonly emit:
      {"tool_calls": [...]}                            — standard wrapper
      {"name": "...", "args": {...}}                   — direct single call
      {"tool_name": "...", "params": {...}}            — alternate naming
      {"tool": "...", "args": {...}}                   — tool key
      {"action": "...", ...}                           — planning JSON
    """
    calls = []

    # Format: {"tool_calls": [...]}
    if "tool_calls" in data:
        for tc in data["tool_calls"]:
            if not isinstance(tc, dict):
                continue
            name = _resolve_tool_name(tc, available_tools)
            if name:
                calls.append({
                    "name": name,
                    "args": _resolve_args(tc),
                    "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                    "type": "tool_call",
                })
        return calls

    # Single call object — any combo of (name|tool_name|tool|action) + (args|params|...)
    name = _resolve_tool_name(data, available_tools)
    if name:
        calls.append({
            "name": name,
            "args": _resolve_args(data),
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "tool_call",
        })

    return calls


def _extract_tool_calls(text: str, available_tools: dict) -> tuple[str, list]:
    """Parse tool calls from LLM text output.

    Handles JSON objects AND top-level JSON arrays, in both fenced code blocks
    and raw text.  Recognises all common field-name variants produced by local
    models (name/tool_name/tool/action, args/params/arguments/parameters).
    """
    tool_calls = []
    remaining_text = text

    def _calls_from_value(parsed) -> list:
        """Extract calls from a parsed JSON value (dict or list of dicts)."""
        result = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    result.extend(_parse_tool_json(item, available_tools))
        elif isinstance(parsed, dict):
            result.extend(_parse_tool_json(parsed, available_tools))
        return result

    # Strategy 1: fenced ```json ... ``` blocks (object OR array)
    for match in re.finditer(r'```(?:json)?\s*([{\[].*?[}\]])\s*```', text, re.DOTALL):
        try:
            parsed = json.loads(match.group(1))
            calls = _calls_from_value(parsed)
            if calls:
                tool_calls.extend(calls)
                remaining_text = remaining_text.replace(match.group(0), "").strip()
        except json.JSONDecodeError:
            continue

    if tool_calls:
        return remaining_text, tool_calls

    # Strategy 1b: leading balanced JSON — try array first, then object
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            dec = json.JSONDecoder()
            parsed, end = dec.raw_decode(stripped)
            calls = _calls_from_value(parsed)
            if calls:
                remaining_text = stripped[end:].strip()
                return remaining_text, calls
        except json.JSONDecodeError:
            pass

    data, matched = _extract_leading_json_dict(text)
    if isinstance(data, dict):
        calls = _parse_tool_json(data, available_tools)
        if calls:
            remaining_text = text.replace(matched, "", 1).strip() if matched else ""
            return remaining_text, calls

    # Strategy 2: every JSON object in the text
    brace_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    for match in re.finditer(brace_pattern, text, re.DOTALL):
        try:
            data = json.loads(match.group(0))
            calls = _parse_tool_json(data, available_tools)
            if calls:
                tool_calls.extend(calls)
                remaining_text = remaining_text.replace(match.group(0), "").strip()
        except json.JSONDecodeError:
            continue

    return remaining_text, tool_calls


def _render_tool_schemas(tools: list) -> str:
    """Render tool schemas as text for system prompt injection."""
    lines = ["You have access to the following tools:\n"]
    max_schema_chars = int(os.getenv("CODEMIND_TOOL_SCHEMA_MAX_CHARS", "5000"))

    def _compact_desc(desc: str) -> str:
        d = " ".join((desc or "").split())
        if not d:
            return ""
        # Keep first sentence (or first 180 chars) to avoid giant tool blocks.
        first = d.split(". ", 1)[0]
        if len(first) > 180:
            first = first[:180].rstrip() + "..."
        return first
    
    for t in tools:
        name = t.name
        desc = _compact_desc(t.description or "")
        
        # Get input schema
        if hasattr(t, 'args_schema') and t.args_schema:
            schema = t.args_schema.model_json_schema()
            # Simplify schema for prompt
            props = schema.get("properties", {})
            required = schema.get("required", [])
            
            params = []
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                req = " (required)" if pname in required else ""
                params.append(f"    - {pname}: {ptype}{req} — {pdesc}")
            
            params_text = "\n".join(params) if params else "    (no parameters)"
        else:
            params_text = "    (no parameters)"
        
        lines.append(f"### {name}\n{desc}\nParameters:\n{params_text}\n")
    
    lines.append("""
To call a tool, respond with a JSON block:
```json
{"tool_calls": [{"name": "tool_name", "args": {"param1": "value1"}}]}
```

You may call multiple tools at once by adding more items to the tool_calls array.

RULES:
- When you output a JSON block to call a tool, STOP generating immediately after the tool call block. Do NOT output any text pretending to be the 'Tool Result' — wait for the system to execute the tool and provide the real result.
- Limit each turn to a small number of tool calls (prefer <= 4).
- If a tool returns an error, adapt your approach — try a different tool or provide your answer based on what you know.
""")

    rendered = "\n".join(lines)
    if len(rendered) > max_schema_chars:
        rendered = rendered[:max_schema_chars] + "\n...[tool schema instructions truncated]"
    return rendered


# ─── CmindChatModelWithTools ────────────────────────────────────────────────

class CmindChatModelWithTools(BaseChatModel):
    """CmindChatModel with tools bound — injects schemas and parses calls."""
    
    base_model: Any = None  # The underlying CmindChatModel
    bound_tools: list = []  # Tool objects
    tool_schemas_text: str = ""  # Pre-rendered tool descriptions
    tool_names: dict = {}  # {name: tool} lookup
    
    model_config = {"arbitrary_types_allowed": True}
    
    @property
    def _llm_type(self) -> str:
        return "cmind-with-tools"
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Sync generation with tool calling."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run, self._agenerate_impl(messages, **kwargs)
                    ).result()
                return result
            else:
                return loop.run_until_complete(
                    self._agenerate_impl(messages, **kwargs))
        except RuntimeError:
            return asyncio.run(self._agenerate_impl(messages, **kwargs))
    
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await self._agenerate_impl(messages, **kwargs)
    
    async def _agenerate_impl(
        self,
        messages: List[BaseMessage],
        **kwargs: Any,
    ) -> ChatResult:
        """Generate with tool schema injection and response parsing."""
        # Prompt-based tool calls only need a short completion; passing the full model
        # max_tokens (e.g. 25k) to local servers makes first-turn routing unnecessarily slow.
        cfg = getattr(self.base_model.driver, "config", None)
        cfg_max = int(getattr(cfg, "max_tokens", 8192) or 8192)
        cap = int(os.getenv("CODEMIND_TOOL_CALL_MAX_TOKENS", "8192"))
        cap = max(256, min(cap, cfg_max))
        raw_mt = kwargs.get("max_tokens")
        if raw_mt is not None:
            try:
                kwargs = {
                    **kwargs,
                    "max_tokens": max(256, min(int(raw_mt), cap)),
                }
            except (TypeError, ValueError):
                kwargs = {**kwargs, "max_tokens": cap}
        else:
            kwargs = {**kwargs, "max_tokens": cap}

        # Inject tool schemas into system prompt
        augmented_messages = list(messages)
        
        # Find or create system message
        has_system = any(isinstance(m, SystemMessage) for m in augmented_messages)
        if has_system:
            augmented_messages = [
                SystemMessage(content=m.content + "\n\n" + self.tool_schemas_text)
                if isinstance(m, SystemMessage) else m
                for m in augmented_messages
            ]
        else:
            augmented_messages.insert(0, SystemMessage(content=self.tool_schemas_text))
        
        # Generate via underlying model
        result = await self.base_model._agenerate_impl(augmented_messages, **kwargs)
        
        # Parse response for tool calls
        ai_content = result.generations[0].message.content
        remaining_text, tool_calls = _extract_tool_calls(ai_content, self.tool_names)
        
        if tool_calls:
            # Return AIMessage with tool_calls set
            message = AIMessage(
                content=remaining_text or "",
                tool_calls=tool_calls,
            )
        else:
            message = AIMessage(content=ai_content)
        
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])
    
    @property
    def _identifying_params(self) -> dict:
        params = self.base_model._identifying_params
        params["tools"] = [t.name for t in self.bound_tools]
        return params


# ─── Main CmindChatModel ─────────────────────────────────────────────────────

class CmindChatModel(BaseChatModel):
    """Wraps existing cmind LLMDriver as a LangChain BaseChatModel.
    
    This enables LangGraph features (bind_tools, ToolNode, 
    with_structured_output) while keeping our custom LLM providers unchanged.
    
    The wrapper translates between:
    - LangChain messages (SystemMessage, HumanMessage, AIMessage, ToolMessage)
    - Our driver's generate(prompt, system_prompt=...) interface
    
    Tool calling is implemented via prompt injection + JSON parsing,
    since our LLM drivers don't support native function calling.
    """
    
    # Pydantic v2 fields
    driver: Any = None      # LLMDriver instance
    model_name: str = ""    # For display/logging
    
    model_config = {"arbitrary_types_allowed": True}
    
    def __init__(self, driver: LLMDriver, **kwargs):
        """Initialize with an existing LLMDriver."""
        model_name = getattr(driver, 'config', None)
        if model_name and hasattr(model_name, 'model'):
            model_name = model_name.model
        else:
            model_name = "cmind"
        
        super().__init__(driver=driver, model_name=str(model_name), **kwargs)
    
    @property
    def _llm_type(self) -> str:
        return "cmind"
    
    def bind_tools(
        self,
        tools: Sequence,
        **kwargs: Any,
    ) -> "CmindChatModelWithTools":
        """Bind tools to this model for prompt-based tool calling.
        
        Returns a new model instance that:
        1. Injects tool schemas into the system prompt
        2. Parses JSON tool calls from the LLM output
        3. Returns AIMessage with tool_calls for LangGraph ToolNode
        """
        tool_names = {}
        for t in tools:
            name = getattr(t, 'name', None) or getattr(t, '__name__', str(t))
            tool_names[name] = t
        
        schemas_text = _render_tool_schemas(tools)
        
        return CmindChatModelWithTools(
            base_model=self,
            bound_tools=list(tools),
            tool_schemas_text=schemas_text,
            tool_names=tool_names,
        )
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Synchronous generation — delegates to async via event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run, self._agenerate_impl(messages, **kwargs)
                    ).result()
                return result
            else:
                return loop.run_until_complete(
                    self._agenerate_impl(messages, **kwargs))
        except RuntimeError:
            return asyncio.run(self._agenerate_impl(messages, **kwargs))
    
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async generation — the primary path for our async drivers."""
        return await self._agenerate_impl(messages, **kwargs)
    
    async def _agenerate_impl(
        self,
        messages: List[BaseMessage],
        **kwargs: Any,
    ) -> ChatResult:
        """Core implementation: convert messages → driver.generate() call."""
        system_prompt, user_prompt = self._messages_to_prompts(messages)
        
        # Extract kwargs that our drivers understand
        driver_kwargs = {}
        if system_prompt:
            driver_kwargs["system_prompt"] = system_prompt
        
        # Pass through max_tokens and temperature if provided
        config = getattr(self.driver, 'config', None)
        if "max_tokens" in kwargs:
            driver_kwargs["max_tokens"] = kwargs["max_tokens"]
        elif config and hasattr(config, 'max_tokens'):
            driver_kwargs["max_tokens"] = config.max_tokens
            
        if "temperature" in kwargs:
            driver_kwargs["temperature"] = kwargs["temperature"]

        # Ensure request fits model context (critical for small local contexts, e.g., 4k).
        if system_prompt or user_prompt:
            cfg = getattr(self.driver, "config", None)
            context_window = (
                int(getattr(cfg, "effective_context_window", 0) or 0)
                if cfg
                else 0
            )
            explicit_ctx = os.getenv("LLM_CONTEXT_WINDOW")
            if explicit_ctx:
                context_window = int(explicit_ctx)
            # Local providers often expose smaller true context than config.max_tokens*4.
            # When no explicit window is configured, apply a conservative ceiling.
            provider_name = str(getattr(getattr(cfg, "provider", ""), "value", getattr(cfg, "provider", ""))).lower()
            if not explicit_ctx and ("local" in provider_name or "ollama" in provider_name):
                local_ctx = int(os.getenv("LOCAL_PROVIDER_CONTEXT_WINDOW", "4096"))
                context_window = min(context_window or local_ctx, local_ctx)
            if context_window <= 0:
                context_window = int(os.getenv("LLM_CONTEXT_WINDOW", "16384"))
            out_tokens = int(driver_kwargs.get("max_tokens", getattr(cfg, "max_tokens", 4096) if cfg else 4096))
            # Keep output allocation realistic relative to total context.
            out_cap = max(256, context_window // 3)
            if out_tokens > out_cap:
                out_tokens = out_cap
                driver_kwargs["max_tokens"] = out_tokens
            # Keep safety headroom for provider/system overhead.
            input_budget = max(512, context_window - out_tokens - 256)

            sys_text = system_prompt or ""
            usr_text = user_prompt or ""
            total_in = count_tokens(sys_text + "\n\n" + usr_text)
            if total_in > input_budget:
                # Prefer preserving user text; shrink system prompt first.
                usr_tokens = count_tokens(usr_text)
                min_user_budget = min(max(256, usr_tokens), max(256, input_budget // 2))
                sys_budget = max(128, input_budget - min_user_budget)
                shrunk_sys = truncate_to_tokens(sys_text, sys_budget)
                remaining_budget = max(128, input_budget - count_tokens(shrunk_sys))
                shrunk_usr = truncate_to_tokens(usr_text, remaining_budget)
                system_prompt = shrunk_sys
                user_prompt = shrunk_usr
        
        # Mask sensitive patterns before sending to the LLM
        guard = get_content_guard()
        if guard.enabled:
            if system_prompt:
                system_prompt, _ = guard.mask(system_prompt)
                driver_kwargs["system_prompt"] = system_prompt
            user_prompt, _ = guard.mask(user_prompt)

        # Call the existing driver
        result_text = await self.driver.generate(user_prompt, **driver_kwargs)
        
        # Wrap in LangChain response format
        message = AIMessage(content=result_text)
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])
    
    @staticmethod
    def _messages_to_prompts(messages: List[BaseMessage]) -> tuple[str, str]:
        """Convert LangChain message list to (system_prompt, user_prompt).
        
        Handles the conversation history by concatenating messages into
        a format our drivers understand.
        """
        system_parts = []
        conversation_parts = []
        
        for msg in messages:
            if isinstance(msg, SystemMessage):
                system_parts.append(msg.content)
            elif isinstance(msg, HumanMessage):
                conversation_parts.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                content = msg.content or ""
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    calls_list = []
                    for tc in msg.tool_calls:
                        calls_list.append({
                            "name": tc["name"],
                            "args": tc.get("args", {})
                        })
                    tool_json = json.dumps({"tool_calls": calls_list})
                    if content.strip():
                        content += "\n\n"
                    content += f"```json\n{tool_json}\n```"
                if content.strip():
                    conversation_parts.append(f"Assistant: {content}")
            elif isinstance(msg, ToolMessage):
                conversation_parts.append(
                    f"System (Tool Result from {msg.name}):\n{msg.content}"
                )
        
        system_prompt = "\n\n".join(system_parts) if system_parts else None
        
        # If only one user message (common case), return it directly
        if len(conversation_parts) == 1 and conversation_parts[0].startswith("User: "):
            user_prompt = conversation_parts[0][6:]
        else:
            user_prompt = "\n\n".join(conversation_parts)
        
        return system_prompt, user_prompt
    
    @property
    def _identifying_params(self) -> dict:
        config = getattr(self.driver, 'config', None)
        if config:
            return {
                "model": getattr(config, 'model', 'unknown'),
                "provider": str(getattr(config, 'provider', 'unknown')),
                "base_url": getattr(config, 'base_url', None),
            }
        return {"model": "cmind"}

    def with_structured_output(
        self,
        schema: type,
        **kwargs: Any,
    ) -> Runnable:
        """Return a Runnable that produces structured (Pydantic) output.
        
        Since our LLM drivers don't support native structured output,
        this works via:
        1. Inject schema description into system prompt
        2. Parse JSON from LLM response
        3. Validate against Pydantic model
        
        Args:
            schema: Pydantic model class defining expected output
            
        Returns:
            Runnable that outputs validated Pydantic objects
        """
        return _StructuredOutputRunnable(chat_model=self, schema=schema)


class _StructuredOutputRunnable(Runnable):
    """Runnable that wraps CmindChatModel for structured output."""
    
    def __init__(self, chat_model: "CmindChatModel", schema: type):
        self.chat_model = chat_model
        self.schema = schema
    
    def invoke(self, input, config=None, **kwargs):
        """Synchronous structured output generation."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        asyncio.run, self.ainvoke(input, config, **kwargs)
                    ).result()
            else:
                return loop.run_until_complete(self.ainvoke(input, config, **kwargs))
        except RuntimeError:
            return asyncio.run(self.ainvoke(input, config, **kwargs))
    
    async def ainvoke(self, input, config=None, **kwargs):
        """Async structured output generation with retry on parse failure."""
        import re as _re

        schema_json = json.dumps(self.schema.model_json_schema(), indent=2)

        # Build a concrete filled example from schema defaults so the LLM
        # sees exactly what shape we expect, not just an abstract JSON Schema spec.
        def _make_example(schema_class) -> str:
            example = {}
            for fname, finfo in schema_class.model_fields.items():
                ann = finfo.annotation
                origin = getattr(ann, "__origin__", None)
                default = finfo.default
                from pydantic_core import PydanticUndefined
                if default not in (None, ..., PydanticUndefined, "", 0, []):
                    example[fname] = default
                elif origin is list:
                    example[fname] = []
                elif ann is int:
                    example[fname] = 75
                elif ann is float:
                    example[fname] = 0.75
                elif ann is bool:
                    example[fname] = True
                else:
                    example[fname] = f"<{fname}>"
            return json.dumps(example, indent=2)

        try:
            example_json = _make_example(self.schema)
        except Exception:
            example_json = "{}"

        schema_instruction = (
            f"\n\nYou MUST respond with a valid JSON object matching this schema:\n"
            f"```json\n{schema_json}\n```\n\n"
            f"Example of the EXACT shape expected (fill with real values):\n"
            f"```json\n{example_json}\n```\n"
            f"Output ONLY the JSON object. No markdown prose, no extra keys, no trailing text. "
            f"Wrap it in a ```json code block."
        )

        # Augment messages with schema instruction
        if isinstance(input, list):
            messages = list(input)
        elif isinstance(input, dict):
            messages = input.get("messages", [])
        else:
            messages = [HumanMessage(content=str(input))]

        # Inject into system message
        augmented = []
        has_system = False
        for msg in messages:
            if isinstance(msg, SystemMessage):
                augmented.append(SystemMessage(content=msg.content + schema_instruction))
                has_system = True
            else:
                augmented.append(msg)

        if not has_system:
            augmented.insert(0, SystemMessage(content=schema_instruction))

        # ── Retry loop: attempt up to 2 times before raising ─────────────
        last_error = None
        for attempt in range(2):
            result = await self.chat_model._agenerate_impl(augmented, **kwargs)
            raw_text = result.generations[0].message.content

            # Strip <think> tags that may still slip through
            raw_text = _re.sub(r'<think>.*?</think>', '', raw_text, flags=_re.DOTALL).strip()

            try:
                return _parse_structured_output(raw_text, self.schema)
            except Exception as e:
                last_error = e
                if attempt == 0:
                    # First failure: strengthen the prompt and retry
                    print(f"[STRUCTURED_OUTPUT] Parse attempt 1 failed ({e}), retrying with stricter prompt...")
                    repair_instruction = (
                        "\n\nPREVIOUS RESPONSE WAS INVALID. You MUST return ONLY a JSON object "
                        f"with these exact keys: {list(self.schema.model_fields.keys())}. "
                        "No extra keys, no markdown, just the JSON object wrapped in ```json."
                    )
                    # Append correction as a new human message so the model sees the failure
                    augmented = augmented + [
                        AIMessage(content=raw_text),
                        HumanMessage(content=repair_instruction)
                    ]

        raise ValueError(
            f"Structured output failed after 2 attempts. Last error: {last_error}"
        )


def _parse_structured_output(text: str, schema: type) -> Any:
    """Parse LLM output text into a validated Pydantic object.
    
    Tries multiple extraction strategies:
    1. ```json code block
    2. Raw JSON object
    3. First { to last } extraction
    4. _repair_json — strip comments, trailing commas, rebalance braces
    """
    import re

    # Strategy 1: ```json code block
    match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return schema.model_validate(data)
        except Exception as e:
            print(f"[PARSER] Strategy 1 failed: {e}")

    # Strategy 2: Try the whole text as JSON
    try:
        data = json.loads(text.strip())
        return schema.model_validate(data)
    except Exception as e:
        print(f"[PARSER] Strategy 2 failed: {e}")

    # Strategy 3: Find first { to last }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return schema.model_validate(data)
        except Exception as e:
            print(f"[PARSER] Strategy 3 failed: {e}")

    # Strategy 4: Aggressive repair — strip JS comments, trailing commas, rebalance braces
    def _repair(s: str) -> dict | None:
        s = re.sub(r'//.*?$', '', s, flags=re.MULTILINE)   # strip // comments
        s = re.sub(r',\s*([}\]])', r'\1', s)                # trailing commas
        # Find outermost brace pair
        depth, start_i, end_i = 0, None, None
        for i, c in enumerate(s):
            if c == '{':
                if depth == 0:
                    start_i = i
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end_i = i + 1
                    break
        if start_i is not None and end_i is not None:
            s = s[start_i:end_i]
        try:
            return json.loads(s)
        except Exception:
            pass
        # Last resort: strip non-ASCII and retry
        try:
            return json.loads(''.join(c for c in s if ord(c) < 128))
        except Exception:
            return None

    repaired = _repair(text)
    if repaired is not None:
        try:
            return schema.model_validate(repaired)
        except Exception as e:
            print(f"[PARSER] Strategy 4 (repair) failed: {e}")

    raise ValueError(f"Could not parse structured output from LLM response: {text[:200]}...")

