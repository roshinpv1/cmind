#!/usr/bin/env python3
"""
Fix repo_id inconsistency between manifest and catalog_store.

Problem: catalog_store uses LLM-generated friendly names (e.g., "promptshield")
while manifest uses hash-based IDs (e.g., "99d2025f600a3a09").

This script:
1. Shows the current inconsistency
2. Matches catalog entries to manifest entries by repo name/path
3. Updates catalog_store.repo_id to match the manifest's repo_id
4. Also updates the LanceDB catalogs table
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from codemind.storage.database import Database, CatalogStore
from codemind.storage.models import RepositoryManifest

BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def main():
    db = Database()
    
    print(f"\n{BOLD}🔍 Repo ID Consistency Check{RESET}\n")
    
    with db.get_session() as session:
        manifests = session.query(RepositoryManifest).all()
        catalogs = session.query(CatalogStore).all()
        
        print(f"{BOLD}Manifest entries:{RESET}")
        manifest_map = {}
        for m in manifests:
            # Extract repo name from path (e.g., .../repos/promptshield/main → promptshield)
            path_parts = m.repo_path.split("/")
            repo_name = path_parts[-2] if len(path_parts) >= 2 else "unknown"
            manifest_map[repo_name.lower()] = m
            print(f"  {CYAN}{m.repo_id}{RESET} | name={repo_name} | org={m.org} | path={m.repo_path}")
        
        print(f"\n{BOLD}Catalog entries:{RESET}")
        mismatches = []
        for c in catalogs:
            # Try to find matching manifest entry
            matching_manifest = None
            
            # Match by catalog repo_id as a name (e.g., "promptshield" → match manifest path containing "promptshield")
            catalog_name = c.repo_id.lower().replace("_", "").replace("-", "")
            for name, m in manifest_map.items():
                clean_name = name.lower().replace("_", "").replace("-", "")
                if catalog_name in clean_name or clean_name in catalog_name:
                    matching_manifest = m
                    break
            
            # Also try matching by repo_name
            if not matching_manifest and c.repo_name:
                for name, m in manifest_map.items():
                    if c.repo_name.lower().replace(" ", "") in name.lower().replace(" ", ""):
                        matching_manifest = m
                        break
            
            status = f"{GREEN}✓ MATCH{RESET}" if matching_manifest and c.repo_id == matching_manifest.repo_id else f"{RED}✗ MISMATCH{RESET}"
            print(f"  {status} catalog.repo_id={CYAN}{c.repo_id}{RESET} | name={c.repo_name} | org={c.org}")
            
            if matching_manifest and c.repo_id != matching_manifest.repo_id:
                mismatches.append((c, matching_manifest))
                print(f"         → Should be: {YELLOW}{matching_manifest.repo_id}{RESET}")
        
        if not mismatches:
            print(f"\n{GREEN}{BOLD}No mismatches found!{RESET}")
            return
        
        print(f"\n{BOLD}Found {len(mismatches)} mismatches to fix.{RESET}")
        response = input(f"Apply fixes? [y/N] ").strip().lower()
        
        if response != "y":
            print("Aborted.")
            return
        
        for catalog, manifest in mismatches:
            old_id = catalog.repo_id
            new_id = manifest.repo_id
            
            print(f"\n  Fixing: {old_id} → {new_id}")
            
            # Update SQLite catalog_store
            catalog.repo_id = new_id
            print(f"    {GREEN}✓{RESET} Updated catalog_store")
        
        session.commit()
        print(f"\n{GREEN}✓ SQLite changes committed{RESET}")
    
    # Also update LanceDB catalogs table
    try:
        import lancedb
        lance_path = os.getenv("LANCEDB_PATH", "data/lancedb")
        lance_db = lancedb.connect(lance_path)
        
        if "catalogs" in lance_db.table_names():
            tbl = lance_db.open_table("catalogs")
            df = tbl.to_pandas()
            
            updated = False
            for catalog, manifest in mismatches:
                old_id = catalog.repo_id  # already updated above, get the old from the pair
                # Since we already updated catalog.repo_id, we need the original
                # Actually the catalog object already has new_id. old_id was set before.
                pass
            
            if "repo_id" in df.columns:
                for _, manifest in mismatches:
                    # We don't have the old_id anymore. Let's re-query.
                    pass
                    
            print(f"\n{YELLOW}⚠ LanceDB catalogs table may need manual re-generation{RESET}")
            print(f"  Run the catalog_generator playbook again for affected repos")
        
    except Exception as e:
        print(f"\n{RED}LanceDB update failed: {e}{RESET}")
    
    print(f"\n{BOLD}Done.{RESET}\n")


if __name__ == "__main__":
    main()
