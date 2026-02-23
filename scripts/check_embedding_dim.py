#!/usr/bin/env python3
"""
Embedding Dimension Diagnostic Tool

Checks:
1. Configured dimension (env var / provider auto-detect)
2. Stored dimension in LanceDB tables (code_chunks, catalogs)
3. Gap analysis between configured and stored dimensions
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Helpers ──────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET} {msg}")
def err(msg):  print(f"  {RED}✗{RESET} {msg}")
def info(msg): print(f"  {CYAN}ℹ{RESET} {msg}")
def header(msg): print(f"\n{BOLD}{'─'*60}\n  {msg}\n{'─'*60}{RESET}")


# ── 1. Configured Dimension ─────────────────────────────────────────

def check_configured_dimension():
    header("1. CONFIGURED EMBEDDING DIMENSION")

    env_dim = os.getenv("EMBEDDING_DIMENSION")
    provider = os.getenv("EMBEDDING_PROVIDER", "local")
    model = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    api_url = os.getenv("EMBEDDING_API_URL")

    info(f"EMBEDDING_PROVIDER = {provider}")
    info(f"EMBEDDING_MODEL    = {model}")
    info(f"EMBEDDING_API_URL  = {api_url or '(not set)'}")
    info(f"EMBEDDING_DIMENSION = {env_dim or '(not set — will auto-detect)'}")

    configured_dim = None

    # Try to get the actual dimension from the provider
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from codemind.indexer.embedder import EmbeddingGenerator

        embedder = EmbeddingGenerator(model_name=model)
        configured_dim = embedder.embedding_dim
        provider_name = type(embedder.provider).__name__
        ok(f"Provider: {provider_name}")
        ok(f"Auto-detected dimension: {BOLD}{configured_dim}{RESET}")
    except Exception as e:
        warn(f"Could not initialize embedder: {e}")
        if env_dim:
            configured_dim = int(env_dim)
            info(f"Using EMBEDDING_DIMENSION env fallback: {configured_dim}")
        else:
            configured_dim = 768
            warn(f"Using hardcoded fallback: {configured_dim}")

    return configured_dim


# ── 2. Stored Dimension in LanceDB ──────────────────────────────────

def check_lancedb_dimension(table_name: str, db_path: str = "data/lancedb"):
    """Read the actual embedding dimension from a LanceDB table."""
    try:
        import lancedb
        import pyarrow as pa

        db = lancedb.connect(db_path)
        tables = db.table_names()

        if table_name not in tables:
            warn(f"Table '{table_name}' does not exist in LanceDB")
            return None, 0

        tbl = db.open_table(table_name)
        schema = tbl.schema
        row_count = tbl.count_rows()

        # Find the embedding field
        emb_field = None
        for field in schema:
            if field.name == "embedding":
                emb_field = field
                break

        if not emb_field:
            warn(f"No 'embedding' field found in '{table_name}'")
            return None, row_count

        # Extract dimension from the type
        emb_type = emb_field.type
        stored_dim = None

        if isinstance(emb_type, pa.FixedSizeListType):
            stored_dim = emb_type.list_size
        elif isinstance(emb_type, pa.ListType):
            # Variable-size list — need to sample a row
            if row_count > 0:
                sample = tbl.to_pandas().head(1)
                if "embedding" in sample.columns:
                    vec = sample["embedding"].iloc[0]
                    stored_dim = len(vec)

        return stored_dim, row_count

    except Exception as e:
        err(f"Error reading '{table_name}': {e}")
        return None, 0


def check_stored_dimensions():
    header("2. STORED DIMENSIONS IN LANCEDB")

    db_path = os.getenv("LANCEDB_PATH", "data/lancedb")
    info(f"LanceDB path: {db_path}")

    if not Path(db_path).exists():
        err(f"LanceDB directory not found at {db_path}")
        return {}

    # List all tables
    try:
        import lancedb
        db = lancedb.connect(db_path)
        tables = db.table_names()
        info(f"Tables found: {tables}")
    except Exception as e:
        err(f"Could not connect to LanceDB: {e}")
        return {}

    results = {}
    for table_name in ["code_chunks", "catalogs"]:
        dim, rows = check_lancedb_dimension(table_name, db_path)
        if dim is not None:
            ok(f"{BOLD}{table_name}{RESET}: dimension={BOLD}{dim}{RESET}, rows={rows:,}")
        elif table_name in tables:
            warn(f"{table_name}: could not determine dimension (rows={rows:,})")
        results[table_name] = {"dim": dim, "rows": rows}

    # Check for any other tables with embeddings
    for table_name in tables:
        if table_name not in ["code_chunks", "catalogs"]:
            dim, rows = check_lancedb_dimension(table_name, db_path)
            if dim is not None:
                info(f"{table_name}: dimension={dim}, rows={rows:,}")
                results[table_name] = {"dim": dim, "rows": rows}

    return results


# ── 3. Gap Analysis ─────────────────────────────────────────────────

def gap_analysis(configured_dim, stored_dims):
    header("3. GAP ANALYSIS")

    if not stored_dims:
        warn("No stored dimensions to compare against")
        return

    all_good = True

    for table_name, data in stored_dims.items():
        stored_dim = data["dim"]
        rows = data["rows"]

        if stored_dim is None:
            continue

        if stored_dim == configured_dim:
            ok(f"{table_name}: {GREEN}MATCH{RESET} — configured={configured_dim}, stored={stored_dim} ({rows:,} rows)")
        else:
            all_good = False
            err(f"{table_name}: {RED}MISMATCH{RESET} — configured={BOLD}{configured_dim}{RESET}, stored={BOLD}{stored_dim}{RESET} ({rows:,} rows)")
            print(f"      → Gap: {abs(configured_dim - stored_dim)} dimensions")
            print(f"      → {RED}This will cause 'ListType cast error' on next index/search!{RESET}")
            print(f"      → Fix: Either change EMBEDDING_DIMENSION={stored_dim} in .env,")
            print(f"              or delete {table_name} table and re-index")

    if all_good:
        print(f"\n  {GREEN}{BOLD}All dimensions match! No issues detected.{RESET}")
    else:
        print(f"\n  {RED}{BOLD}Dimension mismatches found! See above for fixes.{RESET}")


# ── 4. Hardcoded Fallback Audit ──────────────────────────────────────

def audit_hardcoded_fallbacks():
    header("4. HARDCODED FALLBACK AUDIT")

    src_root = Path(__file__).resolve().parent.parent / "src" / "codemind"
    fallback_locations = []

    for py_file in src_root.rglob("*.py"):
        try:
            content = py_file.read_text()
            for i, line in enumerate(content.split("\n"), 1):
                # Look for hardcoded dimension fallbacks
                if '"768"' in line or "'768'" in line or "= 768" in line:
                    if "EMBEDDING_DIMENSION" in line or "embedding_dim" in line or "fallback" in line.lower():
                        rel = py_file.relative_to(src_root.parent.parent)
                        fallback_locations.append((str(rel), i, line.strip()))
        except Exception:
            pass

    if fallback_locations:
        warn(f"Found {len(fallback_locations)} hardcoded 768 fallbacks:")
        for path, line_num, content in fallback_locations:
            print(f"      {CYAN}{path}:{line_num}{RESET}")
            print(f"        {content}")
    else:
        ok("No hardcoded 768 fallbacks found in source code")

    # Also check tests
    test_root = Path(__file__).resolve().parent.parent / "tests"
    test_fallbacks = []
    if test_root.exists():
        for py_file in test_root.rglob("*.py"):
            try:
                content = py_file.read_text()
                for i, line in enumerate(content.split("\n"), 1):
                    if ("768" in line) and ("EMBEDDING" in line or "embedding" in line or "dim" in line.lower()):
                        rel = py_file.relative_to(test_root.parent)
                        test_fallbacks.append((str(rel), i, line.strip()))
            except Exception:
                pass

    if test_fallbacks:
        info(f"Found {len(test_fallbacks)} dimension references in tests:")
        for path, line_num, content in test_fallbacks:
            print(f"      {CYAN}{path}:{line_num}{RESET}")
            print(f"        {content}")


# ── Main ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}🔍 Embedding Dimension Diagnostic Tool{RESET}")
    print(f"{'='*60}")

    configured_dim = check_configured_dimension()
    stored_dims = check_stored_dimensions()
    gap_analysis(configured_dim, stored_dims)
    audit_hardcoded_fallbacks()

    print(f"\n{'='*60}")
    print(f"{BOLD}Done.{RESET}\n")


if __name__ == "__main__":
    main()
