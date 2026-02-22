#!/usr/bin/env python3
"""
Catalog Generation Debugger.

Runs the catalog_generator pipeline step-by-step for a given repo_id,
tracing each stage to show exactly what is happening and where it fails.

Usage:
    python3 -m scripts.debug_catalog --repo-id <repo_id>
    python3 -m scripts.debug_catalog --repo-id <repo_id> --skip-llm   # search only
    python3 -m scripts.debug_catalog --list                            # list indexed repos
"""

import asyncio
import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

# ─── Helpers ────────────────────────────────────────────────────────────────

WIDTH = 78

def banner(title: str):
    print(f"\n{'═' * WIDTH}")
    print(f"  {title}")
    print(f"{'═' * WIDTH}")

def section(title: str):
    print(f"\n{'─' * WIDTH}")
    print(f"  {title}")
    print(f"{'─' * WIDTH}")

def ok(msg: str):
    print(f"  ✅ {msg}")

def warn(msg: str):
    print(f"  ⚠️  {msg}")

def fail(msg: str):
    print(f"  ❌ {msg}")

def info(msg: str):
    print(f"  ℹ️  {msg}")

def indent(text: str, prefix: str = "     "):
    for line in text.split("\n")[:20]:
        print(f"{prefix}{line}")

def truncate(s, n=120):
    s = str(s)
    return s[:n] + "..." if len(s) > n else s


# ─── List repos ─────────────────────────────────────────────────────────────

def list_repos(db_path: str):
    """List all indexed repos from the manifest table."""
    from codemind.storage.database import Database
    try:
        from codemind.storage.manifest import ManifestManager
        mm = ManifestManager(db_path)
        repos = mm.list_repositories()
        if not repos:
            print("  No indexed repositories found.")
            return
        print(f"\n  {'repo_id':<20} {'repo_name':<30} {'branch':<12} {'status'}")
        print(f"  {'─'*20} {'─'*30} {'─'*12} {'─'*12}")
        for r in repos:
            name = (r.repo_path or "").split("/")[-2] if r.repo_path and "/" in r.repo_path else r.repo_id
            print(f"  {r.repo_id:<20} {name:<30} {r.branch or '':<12} {r.status or ''}")
    except Exception as e:
        # Fallback: just list catalog_store entries
        db = Database(db_path)
        from codemind.storage.database import CatalogStore
        with db.get_session() as session:
            cats = session.query(CatalogStore).all()
            if not cats:
                print("  No catalog entries found.")
                return
            print(f"\n  {'repo_id':<20} {'repo_name':<40}")
            print(f"  {'─'*20} {'─'*40}")
            for c in cats:
                print(f"  {c.repo_id:<20} {c.repo_name or '?':<40}")


# ─── Main pipeline ──────────────────────────────────────────────────────────

async def debug_catalog(repo_id: str, skip_llm: bool = False, db_path: str = "data/codemind.db",
                        lance_path: str = "data/lancedb"):

    banner(f"CATALOG GENERATION DEBUG — repo_id={repo_id}")
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  skip_llm: {skip_llm}")

    # ── Step 1: Initialize components ─────────────────────────────────────
    section("STEP 1: Initialize Components")

    from codemind.storage.database import Database, CatalogStore
    from codemind.storage.lancedb_storage import LanceDBStorage
    from codemind.indexer.embedder import EmbeddingGenerator
    from codemind.playbooks import PlaybookRegistry
    from codemind.playbooks.tools import PlaybookTools

    db = Database(db_path)
    lance = LanceDBStorage(lance_path)
    embedder = EmbeddingGenerator()
    tools = PlaybookTools(lance, None, embedder, db)
    registry = PlaybookRegistry()

    ok(f"Database: {db_path}")
    ok(f"LanceDB: {lance_path}")
    ok(f"Embedding model: {embedder.model_name} (dim={embedder.embedding_dim})")
    ok(f"Playbooks loaded: {', '.join(registry.list_playbooks())}")

    # ── Step 2: Load playbook ─────────────────────────────────────────────
    section("STEP 2: Load catalog_generator Playbook")

    playbook = registry.get_playbook("catalog_generator")
    if not playbook:
        fail("catalog_generator playbook not found!")
        return

    strategy = playbook.search_strategy
    queries = strategy.queries or []
    ok(f"Queries: {len(queries)}")
    for i, q in enumerate(queries):
        print(f"     [{i+1:2d}] {q}")
    info(f"mode={strategy.mode}, limit={strategy.limit}, min_score={strategy.min_score}")
    info(f"exclude_test_files={playbook.exclude_test_files}")
    info(f"inject_repo_metadata={playbook.inject_repo_metadata}")
    info(f"output_type={playbook.output_type}, tool_name={playbook.tool_name}")

    # ── Step 3: Check if repo exists in LanceDB ───────────────────────────
    section("STEP 3: Verify Repo in LanceDB")

    all_chunks = lance.get_all_chunks(repo_id=repo_id)
    if not all_chunks:
        fail(f"No code chunks found in LanceDB for repo_id='{repo_id}'")
        warn("This repo may not be indexed yet. Run indexing first.")
        return
    ok(f"Found {len(all_chunks)} code chunks in LanceDB")

    # Show file distribution
    from collections import Counter
    file_counts = Counter(c.get("file_path", "?") for c in all_chunks)
    info(f"Files: {len(file_counts)}")
    for fp, count in file_counts.most_common(10):
        print(f"     {count:3d} chunks  {fp}")
    if len(file_counts) > 10:
        print(f"     ... and {len(file_counts) - 10} more files")

    # ── Step 4: Run each query and show results ───────────────────────────
    section("STEP 4: Execute Search Queries")

    total_results = 0
    query_results = {}

    for i, query in enumerate(queries):
        query_emb = embedder.encode_query(query)
        results = lance.search(
            query_emb, repo_id=repo_id,
            limit=strategy.limit * 2,
            min_score=strategy.min_score
        )

        # Compute scores
        for r in results:
            if "_distance" in r and "score" not in r:
                r["score"] = 1.0 - r["_distance"]

        query_results[query] = results
        total_results += len(results)

        status = "✅" if results else "❌"
        print(f"\n  {status} Query [{i+1:2d}]: \"{query}\"")
        print(f"       Results: {len(results)}")

        if results:
            # Show top 3
            for r in sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:3]:
                score = r.get("score", 0)
                dist = r.get("_distance", "?")
                fp = r.get("file_path", "?")
                text_preview = r.get("chunk_text", "")[:80].replace("\n", " ")
                print(f"       [{score:.3f}] {fp}")
                print(f"              {text_preview}...")
        else:
            # Diagnostic: run without min_score to see what we're missing
            raw = lance.search(query_emb, repo_id=repo_id, limit=5, min_score=0.0)
            if raw:
                top_dist = [r.get("_distance", 1.0) for r in raw[:3]]
                warn(f"Without min_score filter, found {len(raw)} results. "
                     f"Top distances: {[f'{d:.3f}' for d in top_dist]}")
                warn(f"These were filtered by min_score={strategy.min_score} "
                     f"(distance_threshold={1.0 - strategy.min_score:.2f})")
            else:
                warn("No results even without min_score — embedding might not match")

    section("Search Summary")
    queries_with_results = sum(1 for r in query_results.values() if r)
    queries_without = len(queries) - queries_with_results
    ok(f"Queries with results: {queries_with_results}/{len(queries)}")
    if queries_without > 0:
        warn(f"Queries with ZERO results: {queries_without}")
    info(f"Total chunks retrieved: {total_results}")

    # ── Step 5: Deduplicate & pack context ────────────────────────────────
    section("STEP 5: Context Packing")

    all_search_results = []
    dedupe = set()
    for results in query_results.values():
        for r in results:
            key = f"{r.get('file_path', '')}:{r.get('chunk_text', '')[:50]}"
            if key not in dedupe:
                all_search_results.append(r)
                dedupe.add(key)

    # Sort by score descending
    all_search_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    final_chunks = all_search_results[:strategy.limit]

    ok(f"After dedup: {len(all_search_results)} unique chunks")
    ok(f"After limit ({strategy.limit}): {len(final_chunks)} chunks for LLM")

    total_chars = sum(len(c.get("chunk_text", "")) for c in final_chunks)
    info(f"Total context chars: {total_chars:,}")
    info(f"Approx tokens: ~{total_chars // 4:,}")

    if not final_chunks:
        fail("NO chunks to send to LLM — catalog will be empty/hallucinated!")
        warn("Fix: lower min_score in playbook, or re-index this repo")
        return

    # Show score distribution
    scores = [c.get("score", 0) for c in final_chunks]
    info(f"Score range: {min(scores):.3f} — {max(scores):.3f}")
    info(f"Score mean:  {sum(scores)/len(scores):.3f}")

    # ── Step 6: Build LLM prompt (preview) ────────────────────────────────
    section("STEP 6: LLM Prompt Preview")

    sys_prompt = playbook.system_prompt
    info(f"System prompt length: {len(sys_prompt)} chars")
    print(f"\n     First 300 chars of system prompt:")
    indent(sys_prompt[:300])

    # Build code context
    code_lines = []
    for c in final_chunks:
        fp = c.get("file_path", "unknown")
        text = c.get("chunk_text", "")
        code_lines.append(f"--- FILE: {fp} ---\n{text}\n")
    code_context = "\n".join(code_lines)
    info(f"Code context: {len(code_context)} chars ({len(code_context)//4:,} est. tokens)")

    # ── Step 7: Check existing catalog ────────────────────────────────────
    section("STEP 7: Existing Catalog Check")

    with db.get_session() as session:
        existing = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
        if existing:
            ok(f"Existing catalog found (updated: {datetime.fromtimestamp(existing.updated_at, timezone.utc).strftime('%Y-%m-%d %H:%M')})")
            try:
                content = json.loads(existing.content)
                meta = existing.metadata_json or {}
                merged = {**content, **meta}
                for field in ["repo_name", "description", "category", "tech_stack",
                              "quality_score", "estimated_cost"]:
                    val = merged.get(field)
                    status = "✓" if val else "✗"
                    print(f"     {status} {field}: {truncate(val, 80)}")
            except:
                warn("Could not parse existing catalog content")
        else:
            info("No existing catalog for this repo — will be created fresh")

    # Check LanceDB catalog chunks
    cat_chunks = lance.get_catalog_items(repo_id=repo_id)
    info(f"LanceDB catalog chunks: {len(cat_chunks)}")

    # ── Step 8: Run LLM (optional) ────────────────────────────────────────
    if skip_llm:
        section("STEP 8: LLM Generation (SKIPPED — --skip-llm)")
        info("Use without --skip-llm to actually run the LLM and see its output")
    else:
        section("STEP 8: LLM Generation")

        from codemind.llm.factory import get_llm_client
        llm = get_llm_client()
        ok(f"LLM: {llm.config.provider.value} / {llm.config.model}")

        # Build the full prompt
        from codemind.playbooks.token_utils import format_code_chunks_for_llm, estimate_tokens
        max_code_tokens = int(llm.config.max_tokens * 0.5)
        formatted_context = format_code_chunks_for_llm(final_chunks, max_tokens=max_code_tokens)

        # Get repo metadata
        repo_meta_str = ""
        try:
            from codemind.storage.manifest_manager import ManifestManager
            mm = ManifestManager(db_path)
            repo = mm.get_repository_by_id(repo_id)
            if repo:
                repo_meta_str = (
                    f"\n\n## Repository Metadata\n"
                    f"- repo_id: {repo_id}\n"
                    f"- repo_url: {repo.repo_url}\n"
                    f"- branch: {repo.branch}\n"
                    f"- first_author: {repo.first_author}\n"
                    f"- total_commits: {repo.total_commits}\n"
                    f"- last_pr_title: {repo.last_pr_title}\n"
                    f"- last_pr_user: {repo.last_pr_user}\n"
                    f"- last_pr_merged_at: {repo.last_pr_merged_at}\n"
                )
                ok(f"Repo metadata injected: url={repo.repo_url}, branch={repo.branch}")
        except Exception as e:
            warn(f"Could not fetch repo metadata: {e}")

        user_msg = (
            f"RETRIEVED CODE:\n{formatted_context}"
            f"{repo_meta_str}\n\n"
            f"Generate your output based on the instructions and code:\n\n"
            f"IMPORTANT: You MUST output a JSON block invoking 'save_catalog_entry'. "
            f"Do not output any other text."
        )

        total_prompt_tokens = estimate_tokens(sys_prompt) + estimate_tokens(user_msg)
        info(f"Total prompt tokens: ~{total_prompt_tokens:,}")
        info(f"LLM max_tokens: {llm.config.max_tokens:,}")

        # Write full prompt to project tmp/ dir
        tmp_dir = Path("/tmp")
        tmp_dir.mkdir(exist_ok=True)
        prompt_file = tmp_dir / "catalog_prompt_debug.txt"
        response_file = tmp_dir / "catalog_response_debug.txt"
        with open(prompt_file, "w") as f:
            f.write("=" * 80 + "\n")
            f.write("SYSTEM PROMPT\n")
            f.write("=" * 80 + "\n\n")
            f.write(sys_prompt)
            f.write("\n\n")
            f.write("=" * 80 + "\n")
            f.write("USER MESSAGE\n")
            f.write("=" * 80 + "\n\n")
            f.write(user_msg)
        ok(f"Full prompt written to: {prompt_file}")
        info(f"  System prompt: {len(sys_prompt):,} chars")
        info(f"  User message:  {len(user_msg):,} chars")

        if total_prompt_tokens > llm.config.max_tokens * 0.7:
            warn(f"Prompt may exceed context window! "
                 f"({total_prompt_tokens}/{int(llm.config.max_tokens * 0.7)})")

        print(f"\n     Calling LLM... ", end="", flush=True)
        try:
            import time
            t0 = time.time()
            raw_output = await llm.generate(
                user_msg,
                system_prompt=sys_prompt,
                max_tokens=int(llm.config.max_tokens * 0.3)
            )
            elapsed = time.time() - t0
            ok(f"Response received in {elapsed:.1f}s ({len(raw_output)} chars)")

            # Write LLM response to file
            with open(response_file, "w") as f:
                f.write(f"LLM: {llm.config.provider.value} / {llm.config.model}\n")
                f.write(f"Time: {elapsed:.1f}s\n")
                f.write(f"Length: {len(raw_output)} chars\n")
                f.write("=" * 80 + "\n\n")
                f.write(raw_output)
            ok(f"LLM response written to: {response_file}")
        except Exception as e:
            fail(f"LLM call failed: {e}")
            return

        # ── Step 9: Parse LLM output ──────────────────────────────────────
        section("STEP 9: Parse LLM Output")

        info(f"Raw output length: {len(raw_output)} chars")
        print(f"\n     First 500 chars:")
        indent(raw_output[:500])

        # Try to extract JSON
        parsed = None

        # Method 1: Find JSON block
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', raw_output, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                ok("Parsed JSON from ```json block")
            except json.JSONDecodeError as e:
                warn(f"JSON block found but invalid: {e}")

        # Method 2: Find raw JSON object
        if not parsed:
            brace_start = raw_output.find("{")
            if brace_start >= 0:
                depth = 0
                for i in range(brace_start, len(raw_output)):
                    if raw_output[i] == "{": depth += 1
                    elif raw_output[i] == "}": depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(raw_output[brace_start:i+1])
                            ok("Parsed JSON from raw braces")
                        except json.JSONDecodeError:
                            # Try repair
                            from codemind.playbooks.executors import _repair_json
                            parsed = _repair_json(raw_output[brace_start:i+1])
                            if parsed:
                                ok("Parsed JSON after repair")
                        break

        if not parsed:
            fail("Could not extract any valid JSON from LLM output!")
            warn("The LLM did not follow instructions. Try:")
            warn("  1. A larger/better model")
            warn("  2. Simplifying the system prompt")
            warn("  3. Adding few-shot examples")
            return

        # ── Step 10: Validate parsed output ───────────────────────────────
        section("STEP 10: Validate Parsed Output")

        # Check for tool_call wrapper
        if "tool" in parsed and "params" in parsed:
            ok(f"Tool call format detected: tool={parsed['tool']}")
            params = parsed["params"]
        elif "params" in parsed:
            params = parsed["params"]
        else:
            params = parsed

        # Normalize
        from codemind.playbooks.tools import PlaybookTools
        normalized = PlaybookTools._normalize_catalog_params(params)

        # Check each required field
        from scripts.validate_catalogs import REQUIRED_FIELDS, OPTIONAL_FIELDS

        missing = []
        present = []
        for field, desc in {**REQUIRED_FIELDS, **OPTIONAL_FIELDS}.items():
            val = normalized.get(field)
            if val is None or (isinstance(val, str) and not val.strip()) or \
               (isinstance(val, list) and len(val) == 0) or \
               (isinstance(val, (int, float)) and val == 0 and field not in ("quality_score",)):
                missing.append(field)
                print(f"     ✗ {field}: {val}")
            else:
                present.append(field)
                display = truncate(val, 80) if isinstance(val, str) else val
                if isinstance(val, list):
                    display = f"[{len(val)} items] {val[:3]}"
                print(f"     ✓ {field}: {display}")

        print()
        if missing:
            warn(f"Missing/empty fields: {', '.join(missing)}")
            info("These fields are missing from the LLM output.")
            info("Possible causes:")
            info("  1. LLM didn't have enough code context to infer them")
            info("  2. System prompt doesn't emphasize these fields enough")
            info("  3. Model capacity — try a larger model")
        else:
            ok("All fields populated!")

    # ── Final Summary ─────────────────────────────────────────────────────
    banner("DEBUG COMPLETE")
    queries_ok = sum(1 for r in query_results.values() if r)
    print(f"  Repo:              {repo_id}")
    print(f"  Code chunks:       {len(all_chunks)} in LanceDB")
    print(f"  Queries matched:   {queries_ok}/{len(queries)}")
    print(f"  Chunks for LLM:    {len(final_chunks)}")
    if not skip_llm:
        print(f"  LLM fields OK:     {len(present)}/{len(present) + len(missing)}")
        if missing:
            print(f"  Fields missing:    {', '.join(missing)}")
    print()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Debug catalog generation pipeline")
    parser.add_argument("--repo-id", help="Repository ID to debug")
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM call (just test search)")
    parser.add_argument("--list", action="store_true", dest="list_repos", help="List all indexed repos")
    parser.add_argument("--db", default="data/codemind.db", help="SQLite DB path")
    parser.add_argument("--lance", default="data/lancedb", help="LanceDB path")
    args = parser.parse_args()

    if args.list_repos:
        banner("INDEXED REPOSITORIES")
        list_repos(args.db)
        return

    if not args.repo_id:
        parser.error("--repo-id is required (use --list to see available repos)")

    asyncio.run(debug_catalog(
        repo_id=args.repo_id,
        skip_llm=args.skip_llm,
        db_path=args.db,
        lance_path=args.lance,
    ))


if __name__ == "__main__":
    main()
