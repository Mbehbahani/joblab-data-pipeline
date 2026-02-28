#!/usr/bin/env python3
"""
Migration Script: Separate Tools from Skills in Supabase
=========================================================
This script:
1. Adds a 'tools' TEXT column to the Supabase 'jobs' table
2. Reads all existing jobs
3. For each job, splits tool names out of the 'skills' column
   using tools_reference.json as the source of truth
4. Writes the separated tools to the new 'tools' column
5. Updates the 'skills' column to exclude tool entries

Run once to migrate existing data. Future pipeline runs will
populate both columns correctly from the start.

Usage:
    cd SupaBackend
    python scripts/migrate_tools_column.py
"""

import os
import sys
import json
import time
from pathlib import Path

# Add parent dir so we can import from src/
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✓ Loaded .env from {env_path}")
except ImportError:
    pass

import requests

# ---- Configuration --------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

TOOLS_REFERENCE_PATH = Path(__file__).parent.parent / "src" / "config" / "tools_reference.json"

BATCH_SIZE = 100  # rows per PATCH request


def get_tool_names() -> set:
    """Load tool names from tools_reference.json."""
    if not TOOLS_REFERENCE_PATH.exists():
        print(f"❌ Tools reference not found: {TOOLS_REFERENCE_PATH}")
        sys.exit(1)

    with open(TOOLS_REFERENCE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Collect tool names (exact match against comma-separated skills values)
    names = {t["name"] for t in data.get("tools", []) if t.get("name")}
    # Also add category names that were previously used as skill categories
    # (Pyomo, OR-Tools, GAMS, AMPL, CPLEX, Gurobi were listed as categories in skills_reference.json)
    legacy_categories = {"Pyomo", "OR-Tools", "GAMS", "AMPL", "CPLEX", "Gurobi"}
    names.update(legacy_categories)
    print(f"✓ Loaded {len(names)} tool names to match against skills")
    return names


def headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def add_tools_column():
    """Add the 'tools' column via Supabase REST RPC or direct SQL.
    
    NOTE: Supabase REST API cannot run DDL. You must run this SQL manually
    in the Supabase SQL Editor if the column doesn't exist yet:
    
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tools TEXT;
    """
    print("\n" + "=" * 60)
    print("STEP 1: Add 'tools' column")
    print("=" * 60)
    print()
    print("  Please ensure this SQL has been run in Supabase SQL Editor:")
    print()
    print("    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tools TEXT;")
    print()
    
    # Verify column exists by fetching one row
    url = f"{SUPABASE_URL}/rest/v1/jobs?select=tools&limit=1"
    resp = requests.get(url, headers={
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    })
    
    if resp.status_code == 200:
        print("  ✓ 'tools' column exists in Supabase")
        return True
    else:
        print(f"  ✗ Column check failed (status {resp.status_code}): {resp.text[:200]}")
        print("  → Run the ALTER TABLE statement above, then re-run this script.")
        return False


def fetch_all_jobs():
    """Fetch all jobs with their id, skills, and tools columns."""
    print("\n" + "=" * 60)
    print("STEP 2: Fetch existing jobs")
    print("=" * 60)
    
    all_jobs = []
    offset = 0
    page_size = 1000
    
    while True:
        url = f"{SUPABASE_URL}/rest/v1/jobs?select=id,skills,tools&offset={offset}&limit={page_size}"
        resp = requests.get(url, headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        })
        
        if resp.status_code != 200:
            print(f"  ✗ Fetch failed: {resp.status_code} - {resp.text[:200]}")
            break
        
        data = resp.json()
        if not data:
            break
        
        all_jobs.extend(data)
        print(f"  Fetched {len(all_jobs)} jobs so far...")
        
        if len(data) < page_size:
            break
        offset += page_size
    
    print(f"  ✓ Total jobs fetched: {len(all_jobs)}")
    return all_jobs


def separate_skills_and_tools(jobs, tool_names):
    """For each job, split the skills column into skills-only and tools-only."""
    print("\n" + "=" * 60)
    print("STEP 3: Separate tools from skills")
    print("=" * 60)
    
    updates = []  # list of (id, new_skills, new_tools)
    
    jobs_updated = 0
    jobs_already_done = 0
    jobs_no_skills = 0
    
    for job in jobs:
        job_id = job["id"]
        skills_str = job.get("skills") or ""
        existing_tools = job.get("tools") or ""
        
        # Skip if already migrated (tools column is populated)
        if existing_tools.strip():
            jobs_already_done += 1
            continue
        
        if not skills_str.strip():
            jobs_no_skills += 1
            continue
        
        # Split comma-separated values
        items = [s.strip() for s in skills_str.split(",") if s.strip()]
        
        # Separate
        new_skills = []
        new_tools = []
        
        for item in items:
            if item in tool_names:
                new_tools.append(item)
            else:
                new_skills.append(item)
        
        if new_tools:
            updates.append({
                "id": job_id,
                "skills": ", ".join(new_skills) if new_skills else None,
                "tools": ", ".join(new_tools),
            })
            jobs_updated += 1
    
    print(f"  Jobs to update:      {jobs_updated}")
    print(f"  Jobs already done:   {jobs_already_done}")
    print(f"  Jobs with no skills: {jobs_no_skills}")
    print(f"  Jobs unchanged:      {len(jobs) - jobs_updated - jobs_already_done - jobs_no_skills}")
    
    return updates


def apply_updates(updates):
    """Patch each row in Supabase with the separated skills and tools values."""
    print("\n" + "=" * 60)
    print("STEP 4: Apply updates to Supabase")
    print("=" * 60)
    
    if not updates:
        print("  Nothing to update.")
        return
    
    success = 0
    failed = 0
    
    for i, upd in enumerate(updates, 1):
        row_id = upd["id"]
        url = f"{SUPABASE_URL}/rest/v1/jobs?id=eq.{row_id}"
        
        payload = {
            "skills": upd["skills"],
            "tools": upd["tools"],
        }
        
        resp = requests.patch(url, json=payload, headers=headers())
        
        if resp.status_code in (200, 204):
            success += 1
        else:
            failed += 1
            if failed <= 5:
                print(f"  ✗ Row {row_id} failed: {resp.status_code} - {resp.text[:100]}")
        
        if i % 100 == 0:
            print(f"  Progress: {i}/{len(updates)} ({success} ok, {failed} failed)")
            time.sleep(0.1)  # rate-limit safety
    
    print(f"\n  ✓ Updated: {success}")
    if failed:
        print(f"  ✗ Failed:  {failed}")


def main():
    print("=" * 60)
    print("MIGRATION: Separate Tools from Skills")
    print("=" * 60)
    
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")
        sys.exit(1)
    
    print(f"Supabase URL: {SUPABASE_URL[:40]}...")
    
    # Step 1: Ensure column exists
    if not add_tools_column():
        return
    
    # Step 2: Fetch jobs
    jobs = fetch_all_jobs()
    if not jobs:
        print("No jobs found. Nothing to migrate.")
        return
    
    # Step 3: Separate
    tool_names = get_tool_names()
    updates = separate_skills_and_tools(jobs, tool_names)
    
    # Step 4: Apply
    if updates:
        print(f"\nReady to update {len(updates)} rows. Proceeding...")
        apply_updates(updates)
    
    print("\n" + "=" * 60)
    print("✅ MIGRATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
