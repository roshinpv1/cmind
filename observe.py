"""
CodeMind Agent Observer — zero changes to existing source code.

Strategy: Monkey-patch LocalDriver.generate (and optionally OllamaDriver) at
import time so every LLM request AND response is intercepted and written to
a per-session trace directory before the server even starts.

Usage:
    # Option 1: run the server through this script (recommended)
    PYTHONPATH=src python3 observe.py

    # Option 2: import at the top of any script to activate silently
    import observe  # must come before any codemind imports

Trace files are written to:
    /tmp/codemind_traces/<YYYYMMDD_HHMMSS_xxxxxx>/
        0001_llm_request.json   — full prompt sent to LLM
        0002_llm_response.json  — raw text back from LLM
        0003_tool_call.json     — tool invoked by agent (from trace log)
        ...
"""

import asyncio
import datetime
import json
import os
import sys
import uuid
from pathlib import Path

# ── Session bookkeeping ───────────────────────────────────────────────────────

TRACE_DIR = Path("/tmp/codemind_traces")
SESSION_ID = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
SESSION_DIR = TRACE_DIR / SESSION_ID
SESSION_DIR.mkdir(parents=True, exist_ok=True)

_counter = {"n": 0}


def _next_seq() -> str:
    _counter["n"] += 1
    return f"{_counter['n']:04d}"


def _write(filename: str, data: dict):
    path = SESSION_DIR / filename
    try:
        # The temp folder can be cleaned while the process is running.
        # Recreate trace directories on each write to keep observability resilient.
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[OBSERVER] ⚠ Could not write {filename}: {e}", flush=True)


# ── Core patch: wrap LocalDriver.generate ────────────────────────────────────

def _patch_drivers():
    """
    Monkey-patch LocalDriver (and OllamaDriver if present) after they are
    imported.  We add a thin async wrapper around generate() that:
      1. Logs the full outgoing request (messages, model, params)
      2. Awaits the real generate()
      3. Logs the raw response text
      4. Returns the response unchanged
    """
    try:
        # Ensure the module is importable
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
        from codemind.llm import providers as _prov

        # ── LocalDriver ──────────────────────────────────────────────────────
        _original_local = _prov.LocalDriver.generate

        async def _local_generate_patched(self, prompt: str, **kwargs):
            seq = _next_seq()
            ts = datetime.datetime.now().isoformat()
            system_prompt = kwargs.get("system_prompt", "")
            model = getattr(self.config, "model", "unknown")
            _prev_sys = int(os.getenv("CODEMIND_TRACE_PREVIEW_CHARS", "2500"))
            _prev_user = int(os.getenv("CODEMIND_TRACE_USER_PREVIEW_CHARS", "2000"))
            _req_max = kwargs.get("max_tokens", getattr(self.config, "max_tokens", None))

            # Log request
            _write(f"{seq}_llm_request.json", {
                "event": "llm_request",
                "seq": seq,
                "timestamp": ts,
                "driver": "LocalDriver",
                "model": model,
                "base_url": getattr(self.config, "base_url", ""),
                "max_tokens_requested": _req_max,
                "temperature": kwargs.get("temperature", getattr(self.config, "temperature", None)),
                "system_prompt_chars": len(system_prompt),
                "system_prompt_preview": system_prompt[:_prev_sys],
                "system_prompt_preview_truncated": len(system_prompt) > _prev_sys,
                "system_prompt_note": (
                    f"preview first {_prev_sys} chars only; full system prompt is sent to the model"
                ),
                "prompt_chars": len(prompt),
                "prompt_preview": prompt[:_prev_user],
            })
            print(
                f"[OBSERVER] → LocalDriver #{seq} | model={model} | "
                f"sys={len(system_prompt)}c | prompt={len(prompt)}c | {ts}",
                flush=True,
            )

            # Call the real implementation
            try:
                result = await _original_local(self, prompt, **kwargs)
            except Exception as exc:
                err_seq = _next_seq()
                _write(f"{err_seq}_llm_error.json", {
                    "event": "llm_error",
                    "seq": err_seq,
                    "timestamp": datetime.datetime.now().isoformat(),
                    "request_seq": seq,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                print(f"[OBSERVER] ✗ LocalDriver error #{err_seq}: {exc}", flush=True)
                raise

            # Log response
            resp_seq = _next_seq()
            _write(f"{resp_seq}_llm_response.json", {
                "event": "llm_response",
                "seq": resp_seq,
                "timestamp": datetime.datetime.now().isoformat(),
                "request_seq": seq,
                "model": model,
                "response_chars": len(result),
                "response_preview": result[:3000],
                "response_full": result,
            })
            print(
                f"[OBSERVER] ← LocalDriver #{resp_seq} | {len(result)} chars returned",
                flush=True,
            )
            return result

        _prov.LocalDriver.generate = _local_generate_patched
        print("[OBSERVER] ✅ Patched LocalDriver.generate", flush=True)

        # ── OllamaDriver (if present) ────────────────────────────────────────
        if hasattr(_prov, "OllamaDriver"):
            _original_ollama = _prov.OllamaDriver.generate

            async def _ollama_generate_patched(self, prompt: str, **kwargs):
                seq = _next_seq()
                ts = datetime.datetime.now().isoformat()
                system_prompt = kwargs.get("system_prompt", "")
                model = getattr(self.config, "model", "unknown")

                _write(f"{seq}_llm_request.json", {
                    "event": "llm_request",
                    "seq": seq,
                    "timestamp": ts,
                    "driver": "OllamaDriver",
                    "model": model,
                    "system_prompt_preview": system_prompt[:1000],
                    "prompt_preview": prompt[:2000],
                })
                print(f"[OBSERVER] → OllamaDriver #{seq} | model={model} | {ts}", flush=True)

                try:
                    result = await _original_ollama(self, prompt, **kwargs)
                except Exception as exc:
                    err_seq = _next_seq()
                    _write(f"{err_seq}_llm_error.json", {
                        "event": "llm_error", "seq": err_seq,
                        "request_seq": seq, "error": str(exc),
                    })
                    raise

                resp_seq = _next_seq()
                _write(f"{resp_seq}_llm_response.json", {
                    "event": "llm_response",
                    "seq": resp_seq,
                    "request_seq": seq,
                    "response_chars": len(result),
                    "response_full": result,
                })
                print(f"[OBSERVER] ← OllamaDriver #{resp_seq} | {len(result)} chars", flush=True)
                return result

            _prov.OllamaDriver.generate = _ollama_generate_patched
            print("[OBSERVER] ✅ Patched OllamaDriver.generate", flush=True)

    except ImportError as e:
        print(f"[OBSERVER] ⚠ Could not patch drivers (will still tail logs): {e}", flush=True)


# ── Also tail-process the existing trace log files in real-time ──────────────

def _print_trace_summary():
    """Print existing trace log files so user knows where to look."""
    existing = list(Path("/tmp").glob("codemind_agent_*.log"))
    if existing:
        print(f"\n[OBSERVER] Existing per-playbook trace logs:", flush=True)
        for f in sorted(existing):
            size = f.stat().st_size
            print(f"  tail -f {f}  ({size:,} bytes)", flush=True)
    print(
        f"\n[OBSERVER] New LLM request/response traces → {SESSION_DIR}",
        flush=True,
    )
    print(
        f"[OBSERVER] Live watch: watch -n1 'ls -lt {SESSION_DIR} | head -20'",
        flush=True,
    )
    print(
        f"[OBSERVER] Read latest: ls {SESSION_DIR}/*.json | tail -1 | xargs cat",
        flush=True,
    )


# ── Activate ─────────────────────────────────────────────────────────────────

_patch_drivers()
_print_trace_summary()


# ── If run directly: launch uvicorn IN THE SAME PROCESS ──────────────────────
# Critical: patches are in-memory. A subprocess would start fresh with no patches.
# We must call uvicorn.run() so the server shares this process's patched drivers.

if __name__ == "__main__":
    try:
        import uvicorn
    except ImportError:
        print("[OBSERVER] ✗ uvicorn not found. Install it: pip install uvicorn", flush=True)
        sys.exit(1)

    print("\n[OBSERVER] Starting CodeMind server IN-PROCESS with full LLM observability...", flush=True)
    print(f"[OBSERVER] → All LLM traces will appear in: {SESSION_DIR}\n", flush=True)

    # Add src/ to path BEFORE importing the app so all codemind modules resolve
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    # Import the app AFTER patching — patches are already live on LocalDriver
    from codemind.api.server import app  # noqa: E402

    # Run uvicorn with the app OBJECT (not a string) — no subprocess, patches survive
    uvicorn.run(
        app,               # ← object reference, not "module:attr" string
        host="0.0.0.0",
        port=8000,
        reload=False,      # reload=True spawns child processes and loses patches
    )
