#!/usr/bin/env python3
"""
Migrate repository clones from the legacy name/branch layout to the
repo_id-based layout.

Legacy:  {CODEMIND_REPOS_PATH}/{repo_name}/{branch}/
New:     {CODEMIND_REPOS_PATH}/{repo_id}/repo/

Safe to re-run — already-migrated repos and missing sources are skipped.

Usage:
    python scripts/migrate_repo_layout.py [--dry-run]
"""

import argparse
import os
import shutil
import sqlite3
from pathlib import Path


def load_env(env_path: Path) -> None:
    """Minimal .env loader (no external dependencies)."""
    if not env_path.exists():
        return
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


def migrate(dry_run: bool = False) -> None:
    load_env(Path(__file__).parent.parent / ".env")

    base       = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
    repos_path = Path(os.getenv("CODEMIND_REPOS_PATH", os.path.join(base, "repos")))
    db_path    = Path(os.getenv("CODEMIND_DB_PATH",    os.path.join(base, "codemind.db")))

    print(f"Repos path : {repos_path}")
    print(f"DB path    : {db_path}")
    print(f"Dry run    : {dry_run}")
    print()

    if not db_path.exists():
        print("ERROR: database not found — is CODEMIND_DB_PATH set correctly?")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    cur.execute("SELECT repo_id, repo_path, repo_url, branch FROM repository_manifests")
    repos = cur.fetchall()
    print(f"Found {len(repos)} repo(s) in DB\n")

    moved = skipped = errors = 0

    for row in repos:
        repo_id  = row["repo_id"]
        old_path = Path(row["repo_path"])
        branch   = row["branch"] or "main"
        new_path = repos_path / repo_id / "repo"

        # Already at (or under) the new location
        try:
            old_path.relative_to(repos_path / repo_id)
            print(f"  SKIP  {repo_id}  (already under {repos_path / repo_id})")
            skipped += 1
            continue
        except ValueError:
            pass

        if not old_path.exists():
            print(f"  MISS  {repo_id}  {old_path}  (source not found — skipping)")
            skipped += 1
            continue

        print(f"  MOVE  {repo_id}")
        print(f"        {old_path}")
        print(f"     →  {new_path}")

        if not dry_run:
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(new_path))

                # Remove empty parent dir left behind (e.g. data/repos/opencode/)
                try:
                    old_path.parent.rmdir()
                    print(f"        removed empty dir {old_path.parent}")
                except OSError:
                    pass  # Not empty — fine, leave it

                # Update DB
                cur.execute(
                    "UPDATE repository_manifests SET repo_path = ? WHERE repo_id = ?",
                    (str(new_path), repo_id),
                )
                conn.commit()
                print(f"        updated DB repo_path → {new_path}")
                moved += 1

            except Exception as exc:
                print(f"        ERROR: {exc}")
                errors += 1
        else:
            moved += 1  # count what would be moved

    conn.close()

    print()
    print(f"Done — moved: {moved}  skipped: {skipped}  errors: {errors}")
    if dry_run:
        print("(dry-run — nothing was actually moved or updated)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate repo clones to repo_id layout")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
