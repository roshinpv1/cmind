"""
Schema migration script for CodeMind SQLite database.

Adds missing columns to existing tables without losing data.
Also backfills NULL org values with random org assignments.
Run: python3 scripts/migrate_schema.py [--db-path data/codemind.db]

Safe to run multiple times — only adds columns that don't already exist.
"""
import argparse
import random
import sqlite3
import sys
from pathlib import Path


# All columns that should exist in each table, with their SQL types
EXPECTED_COLUMNS = {
    "repository_manifests": {
        "id": "INTEGER PRIMARY KEY",
        "repo_path": "VARCHAR",
        "repo_id": "VARCHAR",
        "repo_url": "VARCHAR",
        "branch": "VARCHAR",
        "org": "VARCHAR",
        "last_indexed_at": "DATETIME",
        "last_commit_hash": "VARCHAR",
        "embedding_model": "VARCHAR DEFAULT 'all-MiniLM-L6-v2'",
        "embedding_version": "INTEGER DEFAULT 1",
        "total_files_indexed": "INTEGER DEFAULT 0",
        "first_commit_at": "DATETIME",
        "first_author": "VARCHAR",
        "last_authors": "VARCHAR",
        "total_commits": "INTEGER DEFAULT 0",
        "last_pr_title": "VARCHAR",
        "last_pr_user": "VARCHAR",
        "last_pr_merged_at": "VARCHAR",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "catalog_store": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "repo_id": "VARCHAR",
        "repo_name": "VARCHAR",
        "org": "VARCHAR",
        "content": "TEXT",
        "metadata_json": "JSON",
        "status": "VARCHAR DEFAULT 'active'",
        "created_by": "VARCHAR",
        "source_gap": "VARCHAR",
        "source_analysis_id": "VARCHAR",
        "requirements": "JSON",
        "git_url": "VARCHAR",
        "git_branch": "VARCHAR",
        "quality_score": "INTEGER DEFAULT 0",
        "created_at": "INTEGER DEFAULT 0",
        "updated_at": "INTEGER DEFAULT 0",
    },
}

# Columns that should NOT be altered (primary keys, etc.)
SKIP_COLUMNS = {"id"}


def get_existing_columns(cursor, table_name):
    """Get the list of existing columns in a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def get_existing_tables(cursor):
    """Get the list of existing tables."""
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cursor.fetchall()}


def migrate_table(cursor, table_name, expected_cols):
    """Add missing columns to a table."""
    existing_tables = get_existing_tables(cursor)
    
    if table_name not in existing_tables:
        print(f"  ⚠️  Table '{table_name}' does not exist — it will be created on next server start")
        return 0
    
    existing = get_existing_columns(cursor, table_name)
    added = 0
    
    for col_name, col_type in expected_cols.items():
        if col_name in SKIP_COLUMNS:
            continue
        if col_name not in existing:
            # For ALTER TABLE, we need simple type (no PRIMARY KEY, etc.)
            alter_type = col_type.replace("PRIMARY KEY", "").replace("AUTOINCREMENT", "").strip()
            sql = f"ALTER TABLE {table_name} ADD COLUMN {col_name} {alter_type}"
            try:
                cursor.execute(sql)
                print(f"  ✅ Added column: {table_name}.{col_name} ({alter_type})")
                added += 1
            except Exception as e:
                print(f"  ❌ Failed to add {table_name}.{col_name}: {e}")
        else:
            pass  # Column already exists
    
    if added == 0:
        print(f"  ✓ Table '{table_name}' is up to date (all {len(existing)} columns present)")
    
    return added


def main():
    parser = argparse.ArgumentParser(description="Migrate CodeMind SQLite schema")
    parser.add_argument("--db-path", default="data/codemind.db", help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()
    
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        print("   Make sure you're running from the project root directory")
        sys.exit(1)
    
    print(f"=== CodeMind Schema Migration ===")
    print(f"Database: {db_path.resolve()}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}\n")
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Show existing tables
    tables = get_existing_tables(cursor)
    print(f"Existing tables: {sorted(tables)}\n")
    
    total_added = 0
    for table_name, expected_cols in EXPECTED_COLUMNS.items():
        print(f"--- {table_name} ---")
        
        if args.dry_run:
            if table_name not in tables:
                print(f"  ⚠️  Table does not exist")
                continue
            existing = get_existing_columns(cursor, table_name)
            missing = set(expected_cols.keys()) - existing - SKIP_COLUMNS
            if missing:
                for col in sorted(missing):
                    print(f"  → Would add: {col} ({expected_cols[col]})")
            else:
                print(f"  ✓ All columns present")
        else:
            total_added += migrate_table(cursor, table_name, expected_cols)
    
    if not args.dry_run:
        conn.commit()
        print(f"\n{'='*40}")
        print(f"Migration complete! {total_added} column(s) added.")
        
        # Backfill NULL org values with random assignments
        backfill_org_values(cursor, conn)
        
        # Verify final state
        print(f"\n--- Final Schema ---")
        for table_name in EXPECTED_COLUMNS:
            if table_name in get_existing_tables(cursor):
                cols = get_existing_columns(cursor, table_name)
                print(f"{table_name}: {sorted(cols)}")
    
    conn.close()


ORG_VALUES = ["CT", "DTI", "CTO"]


def backfill_org_values(cursor, conn):
    """Randomly assign org values to rows with NULL org."""
    print(f"\n--- Backfilling org values ---")
    
    tables = ["repository_manifests", "catalog_store"]
    for table in tables:
        if table not in get_existing_tables(cursor):
            continue
        
        # Check if org column exists
        cols = get_existing_columns(cursor, table)
        if "org" not in cols:
            print(f"  ⚠️  {table}: no 'org' column, skipping")
            continue
        
        # Get rows with NULL org
        cursor.execute(f"SELECT rowid FROM {table} WHERE org IS NULL")
        null_rows = cursor.fetchall()
        
        if not null_rows:
            print(f"  ✓ {table}: no NULL org values")
            continue
        
        # Update each row with a random org
        for (rowid,) in null_rows:
            org = random.choice(ORG_VALUES)
            cursor.execute(f"UPDATE {table} SET org = ? WHERE rowid = ?", (org, rowid))
        
        conn.commit()
        print(f"  ✅ {table}: assigned random org to {len(null_rows)} row(s)")
        
        # Show distribution
        cursor.execute(f"SELECT org, COUNT(*) FROM {table} GROUP BY org")
        dist = cursor.fetchall()
        for org_val, count in dist:
            print(f"      {org_val}: {count}")


if __name__ == "__main__":
    main()
