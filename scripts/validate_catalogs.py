#!/usr/bin/env python3
"""
Catalog Validation Script.

Checks all catalog entries for completeness and diagnoses issues.

Usage:
    python -m scripts.validate_catalogs
    python -m scripts.validate_catalogs --repo-id <id>
    python -m scripts.validate_catalogs --verbose
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from codemind.storage.database import CatalogStore, Database
from codemind.storage.lancedb_storage import LanceDBStorage


# ===========================================================================
# Expected fields and validation rules
# ===========================================================================

# Fields that MUST be non-empty
REQUIRED_FIELDS = {
    "repo_name": "Human-readable project name",
    "description": "One-line summary",
    "summary_high_level": "2-3 sentence overview",
    "summary_detailed": "Multi-paragraph analysis",
    "category": "Software type (Web App, API, CLI Tool, etc.)",
    "architecture": "Architecture description",
    "tech_stack": "Languages, frameworks, databases",
    "quality_score": "Quality score 1-100",
    "topics": "Searchable tags (list)",
    "pros": "Strengths (list)",
    "cons": "Weaknesses (list)",
    "estimated_cost": "Estimated build cost in USD",
    "business_functionalities": "Core business capabilities (list)",
}

# Fields that SHOULD be present but are OK if missing
OPTIONAL_FIELDS = {
    "repo_url": "Repository URL",
    "branch": "Branch name",
    "specification": "Key APIs/interfaces",
    "first_author": "Original author",
    "total_commits": "Total commit count",
    "last_pr_title": "Last merged PR title",
}


# ===========================================================================
# Validators
# ===========================================================================

def validate_catalog_entry(repo_id: str, content: dict, metadata: dict) -> dict:
    """Validate a single catalog entry.

    Returns a report dict with:
        status: PASS | WARN | FAIL
        missing: list of missing required fields
        empty: list of present-but-empty fields
        warnings: list of optional missing fields
        quality_issues: list of data quality problems
    """
    report = {
        "repo_id": repo_id,
        "repo_name": metadata.get("repo_name") or content.get("repo_name") or repo_id,
        "status": "PASS",
        "missing": [],
        "empty": [],
        "warnings": [],
        "quality_issues": [],
    }

    # Merge content + metadata for field checking
    merged = {**content, **metadata}

    # --- Check required fields ---
    for field, desc in REQUIRED_FIELDS.items():
        val = merged.get(field)
        if val is None:
            report["missing"].append(f"{field} ({desc})")
        elif isinstance(val, str) and not val.strip():
            report["empty"].append(f"{field} ({desc})")
        elif isinstance(val, list) and len(val) == 0:
            report["empty"].append(f"{field} ({desc})")
        elif isinstance(val, (int, float)) and val == 0 and field not in ("quality_score",):
            report["empty"].append(f"{field} ({desc}) = 0")

    # --- Check optional fields ---
    for field, desc in OPTIONAL_FIELDS.items():
        val = merged.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            report["warnings"].append(f"{field} ({desc})")

    # --- Quality checks ---
    qs = merged.get("quality_score", 0)
    if isinstance(qs, (int, float)):
        if qs < 1 or qs > 100:
            report["quality_issues"].append(f"quality_score={qs} out of range [1,100]")

    est = merged.get("estimated_cost", 0)
    if isinstance(est, (int, float)) and est > 0 and est < 1000:
        report["quality_issues"].append(f"estimated_cost=${est} seems too low (min ~$5000)")

    summary = merged.get("summary_detailed", "")
    if isinstance(summary, str) and len(summary) < 100:
        report["quality_issues"].append(f"summary_detailed too short ({len(summary)} chars, expected 100+)")

    topics = merged.get("topics", [])
    if isinstance(topics, list) and len(topics) < 3:
        report["quality_issues"].append(f"Only {len(topics)} topics (recommend 3+)")

    biz = merged.get("business_functionalities", [])
    if isinstance(biz, list) and len(biz) == 0:
        report["quality_issues"].append("No business_functionalities listed")

    category = merged.get("category", "")
    valid_categories = {
        "Monolith", "Microservice", "AI Agent", "MCP", "AI Enabled",
        "Frontend", "Backend", "Fullstack", "API", "Web App", "CLI Tool",
        "Library", "Framework", "ML Pipeline", "Data Pipeline",
        "Infrastructure", "DevOps", "Security", "Testing", "Documentation", "Other",
    }
    if category and category not in valid_categories:
        report["quality_issues"].append(
            f"category='{category}' not in standard set: {sorted(valid_categories)}"
        )

    # --- Set status ---
    if report["missing"]:
        report["status"] = "FAIL"
    elif report["empty"] or report["quality_issues"]:
        report["status"] = "WARN"

    return report


def check_lancedb_chunks(lance: LanceDBStorage, repo_id: str) -> dict:
    """Check if LanceDB has catalog chunks for this repo."""
    try:
        items = lance.get_catalog_items(repo_id=repo_id)
        return {
            "chunk_count": len(items),
            "has_embeddings": len(items) > 0
        }
    except Exception as e:
        return {"chunk_count": 0, "has_embeddings": False, "error": str(e)}


# ===========================================================================
# Main
# ===========================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate catalog entries")
    parser.add_argument("--repo-id", help="Validate a specific repo_id only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show field details for each entry")
    parser.add_argument("--db", default="data/codemind.db", help="SQLite DB path")
    parser.add_argument("--lance", default="data/lancedb", help="LanceDB path")
    args = parser.parse_args()

    # Connect
    db = Database(args.db)
    lance = LanceDBStorage(args.lance)

    print("=" * 70)
    print("  CATALOG VALIDATION REPORT")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    # Fetch all catalogs
    with db.get_session() as session:
        query = session.query(CatalogStore)
        if args.repo_id:
            query = query.filter_by(repo_id=args.repo_id)
        catalogs = query.all()

    if not catalogs:
        print("\n  ❌ No catalog entries found in database.")
        if args.repo_id:
            print(f"     Tried repo_id: {args.repo_id}")
        print("\n  Possible causes:")
        print("   1. No repos have been indexed yet")
        print("   2. Catalog generation playbook hasn't been run")
        print("   3. Wrong database path (current: {args.db})")
        sys.exit(1)

    print(f"\n  Found {len(catalogs)} catalog(s)\n")

    # Validate each
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    reports = []

    for cat in catalogs:
        # Parse content JSON
        try:
            content = json.loads(cat.content) if cat.content else {}
        except json.JSONDecodeError:
            content = {}

        metadata = cat.metadata_json or {}
        report = validate_catalog_entry(cat.repo_id, content, metadata)

        # Check LanceDB chunks
        lance_info = check_lancedb_chunks(lance, cat.repo_id)
        report["lance_chunks"] = lance_info["chunk_count"]
        report["has_embeddings"] = lance_info["has_embeddings"]

        if not lance_info["has_embeddings"]:
            report["quality_issues"].append("⚠️ No LanceDB chunks — catalog won't appear in search!")
            if report["status"] == "PASS":
                report["status"] = "WARN"

        counts[report["status"]] += 1
        reports.append(report)

    # --- Print results ---
    for report in reports:
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[report["status"]]
        print(f"  {icon} {report['status']}  {report['repo_name']}")
        print(f"       repo_id: {report['repo_id']}")
        print(f"       LanceDB chunks: {report['lance_chunks']}")

        if report["missing"]:
            print(f"       ❌ Missing required fields ({len(report['missing'])}):")
            for f in report["missing"]:
                print(f"          - {f}")

        if report["empty"]:
            print(f"       ⚠️  Empty/zero fields ({len(report['empty'])}):")
            for f in report["empty"]:
                print(f"          - {f}")

        if report["quality_issues"]:
            print(f"       🔍 Quality issues ({len(report['quality_issues'])}):")
            for q in report["quality_issues"]:
                print(f"          - {q}")

        if report["warnings"] and args.verbose:
            print(f"       ℹ️  Optional missing ({len(report['warnings'])}):")
            for w in report["warnings"]:
                print(f"          - {w}")

        if args.verbose:
            # Show actual values for key fields
            try:
                content = json.loads(cat.content) if cat.content else {}
            except:
                content = {}
            meta = cat.metadata_json or {}
            merged = {**content, **meta}

            print(f"       --- Field Values ---")
            for field in list(REQUIRED_FIELDS.keys()) + list(OPTIONAL_FIELDS.keys()):
                val = merged.get(field)
                if isinstance(val, str):
                    display = val[:80] + "..." if len(val) > 80 else val
                elif isinstance(val, list):
                    display = f"[{len(val)} items] {val[:3]}"
                else:
                    display = val
                status = "✓" if val else "✗"
                print(f"          {status} {field}: {display}")

        print()

    # --- Summary ---
    print("=" * 70)
    print(f"  SUMMARY: {counts['PASS']} PASS | {counts['WARN']} WARN | {counts['FAIL']} FAIL")
    print(f"  Total catalogs: {len(catalogs)}")
    print("=" * 70)

    if counts["FAIL"] > 0:
        print("\n  💡 DIAGNOSIS for FAIL entries:")
        print("     Common causes:")
        print("     1. LLM returned incomplete JSON — check LLM output logs")
        print("     2. Playbook search returned 0 chunks — run with --verbose")
        print("        to check which fields are missing")
        print("     3. Tool call normalization missed a nested field —")
        print("        check _normalize_catalog_params in tools.py")
        print("     4. Smaller model may skip fields — try a larger model")
        print()
        print("  💡 To re-generate a catalog, re-run the indexing pipeline")
        print("     for the affected repository.")

    if counts["WARN"] > 0:
        print("\n  💡 DIAGNOSIS for WARN entries:")
        print("     - Empty fields may indicate LLM couldn't infer the value")
        print("     - quality_score=0 usually means LLM omitted the field")
        print("     - No LanceDB chunks means catalog was saved to SQLite")
        print("       but not embedded — re-run catalog generation")

    # Exit code
    sys.exit(1 if counts["FAIL"] > 0 else 0)


if __name__ == "__main__":
    main()
