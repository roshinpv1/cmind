#!/usr/bin/env python3
"""
Test LLM request via Apigee configuration.

Reads prompt content from a file and sends it to the Apigee LLM endpoint.

Usage:
    python3 -m scripts.test_apigee_llm <prompt_file>
    python3 -m scripts.test_apigee_llm /tmp/catalog_prompt_debug.txt
    python3 -m scripts.test_apigee_llm prompt.txt --system-prompt system.txt
    python3 -m scripts.test_apigee_llm prompt.txt --max-tokens 4096
    python3 -m scripts.test_apigee_llm prompt.txt --output /tmp/response.txt
"""

import asyncio
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()


async def main():
    parser = argparse.ArgumentParser(description="Test LLM via Apigee")
    parser.add_argument("prompt_file", help="File containing the prompt to send")
    parser.add_argument("--system-prompt", "-s", help="File containing system prompt (optional)")
    parser.add_argument("--max-tokens", "-m", type=int, default=4096, help="Max tokens (default: 4096)")
    parser.add_argument("--temperature", "-t", type=float, default=0.1, help="Temperature (default: 0.1)")
    parser.add_argument("--output", "-o", help="Write response to file (default: stdout)")
    parser.add_argument("--model", help="Override model name")
    args = parser.parse_args()

    # Read prompt file
    prompt_path = Path(args.prompt_file)
    if not prompt_path.exists():
        print(f"❌ Prompt file not found: {prompt_path}")
        sys.exit(1)

    prompt = prompt_path.read_text()
    print(f"📄 Prompt file: {prompt_path} ({len(prompt):,} chars)")

    # Read system prompt if provided
    system_prompt = None
    if args.system_prompt:
        sp_path = Path(args.system_prompt)
        if not sp_path.exists():
            print(f"❌ System prompt file not found: {sp_path}")
            sys.exit(1)
        system_prompt = sp_path.read_text()
        print(f"📄 System prompt: {sp_path} ({len(system_prompt):,} chars)")

    # Check Apigee config
    required_vars = [
        "APIGEE_NONPROD_LOGIN_URL", "APIGEE_CONSUMER_KEY", "APIGEE_CONSUMER_SECRET",
        "ENTERPRISE_BASE_URL", "WF_USE_CASE_ID", "WF_CLIENT_ID", "WF_API_KEY"
    ]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing Apigee env vars: {', '.join(missing)}")
        print(f"   Set LLM_PROVIDER=apigee and configure the above in .env")
        sys.exit(1)

    # Force Apigee provider
    os.environ["LLM_PROVIDER"] = "apigee"
    if args.model:
        os.environ["APIGEE_MODEL"] = args.model

    from codemind.llm.factory import get_llm_client
    llm = get_llm_client()

    print(f"🤖 LLM: {llm.config.provider.value} / {llm.config.model}")
    print(f"⚙️  max_tokens={args.max_tokens}, temperature={args.temperature}")
    print(f"📤 Sending request...", flush=True)

    t0 = time.time()
    try:
        kwargs = {
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
        }
        if system_prompt:
            kwargs["system_prompt"] = system_prompt

        response = await llm.generate(prompt, **kwargs)
        elapsed = time.time() - t0

        print(f"✅ Response received in {elapsed:.1f}s ({len(response):,} chars)")

        if args.output:
            out_path = Path(args.output)
            out_path.write_text(response)
            print(f"💾 Response written to: {out_path}")
        else:
            print(f"\n{'═' * 70}")
            print(f"  RESPONSE")
            print(f"{'═' * 70}\n")
            print(response)
            print(f"\n{'═' * 70}")

    except Exception as e:
        elapsed = time.time() - t0
        print(f"❌ Request failed after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
