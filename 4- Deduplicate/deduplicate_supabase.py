#!/usr/bin/env python3
"""
Supabase Deduplication
======================
Removes duplicate rows from Supabase tables.
- jobs:        deduplicate by `url`             (scan newest 300, keep oldest id)
- job_details: deduplicate by `job_id`          (scan newest 300, keep oldest id)
- job_chunks:  deduplicate by `job_id+chunk_index` (scan newest 500, keep oldest id)

Only scans the most recently inserted rows (highest ids) because duplicates
can only come from the most recent pipeline run. This avoids fetching the
entire table on every pipeline execution.

Strategy: When duplicates exist, keep the row with the SMALLEST id
(oldest / first inserted) and delete the newer ones.
"""
import os
import sys
import logging
import time
from pathlib import Path
from collections import defaultdict
import requests
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Per-table configuration ─────────────────────────────────────────────────
# Maps table name → fetch config and dedup key.
# scan_limit: how many most-recent rows to inspect (ordered by id DESC).
# Duplicates can only come from the latest pipeline run, so a small window
# is sufficient and much cheaper than scanning the full table.
TABLE_CONFIG = {
    "jobs": {
        "select": "id,url",
        "dedup_key": lambda row: row["url"],   # group by url
        "scan_limit": 300,
    },
    "job_details": {
        "select": "id,job_id",
        "dedup_key": lambda row: row["job_id"],  # group by job_id
        "scan_limit": 300,
    },
    "job_chunks": {
        "select": "id,job_id,chunk_index",
        "dedup_key": lambda row: f"{row['job_id']}|{row['chunk_index']}",  # composite key
        "scan_limit": 500,
    },
}

# Retry settings for transient Supabase/Cloudflare errors
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds


def _is_transient(status_code: int) -> bool:
    """Return True for HTTP status codes that are worth retrying."""
    return status_code in (429, 500, 502, 503, 504)


def _request_with_retry(method: str, url: str, headers: dict, **kwargs) -> requests.Response:
    """Make an HTTP request with retry on transient errors."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
            if resp.ok or not _is_transient(resp.status_code):
                return resp
            logger.warning(
                "Transient %d on %s (attempt %d/%d), retrying in %.0fs...",
                resp.status_code, method.upper(), attempt + 1, MAX_RETRIES,
                RETRY_BACKOFF * (2 ** attempt),
            )
        except requests.exceptions.RequestException as e:
            logger.warning(
                "Request error on %s (attempt %d/%d): %s",
                method.upper(), attempt + 1, MAX_RETRIES, e,
            )
        time.sleep(RETRY_BACKOFF * (2 ** attempt))

    # Final attempt — let it raise
    return requests.request(method, url, headers=headers, timeout=30, **kwargs)


def deduplicate(target_table="jobs"):
    """
    Remove duplicate rows from a Supabase table.
    
    - jobs:        groups by `url`, keeps lowest id
    - job_details: groups by `job_id`, keeps lowest id
    - job_chunks:  groups by `(job_id, chunk_index)`, keeps lowest id
    """
    # Load environment variables
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    
    supabase_url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not service_role_key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)
    
    # Get table config
    cfg = TABLE_CONFIG.get(target_table)
    if not cfg:
        logger.error(f"Unknown table: {target_table}. Supported: {list(TABLE_CONFIG.keys())}")
        return 0

    base_url = f"{supabase_url.rstrip('/')}/rest/v1"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    # 1. Fetch only the most recent N rows (by id DESC) for dedup scan.
    # Duplicates only arise from the latest pipeline run, so there is no need
    # to scan the entire table on every execution.
    scan_limit = cfg["scan_limit"]
    logger.info(f"Fetching newest {scan_limit} rows from {target_table} table...")

    fetch_url = (
        f"{base_url}/{target_table}"
        f"?select={cfg['select']}"
        f"&order=id.desc"
        f"&limit={scan_limit}"
    )
    try:
        response = _request_with_retry("get", fetch_url, headers=headers)
        response.raise_for_status()
        all_rows = response.json()
    except Exception as e:
        logger.error(f"Error fetching from {target_table}: {e}")
        return 0

    if not all_rows:
        logger.info(f"No rows found in {target_table}.")
        return 0

    logger.info(f"Rows fetched from {target_table}: {len(all_rows)} (limit {scan_limit})")
    
    # 2. Identify duplicates — keep the SMALLEST id (oldest row)
    dedup_key_fn = cfg["dedup_key"]
    rows_by_key = defaultdict(list)
    for row in all_rows:
        rows_by_key[dedup_key_fn(row)].append(row['id'])
    
    ids_to_delete = []
    for key, ids in rows_by_key.items():
        if len(ids) > 1:
            # Sort ids: smallest = oldest → keep it, delete the rest
            ids.sort()
            ids_to_delete.extend(ids[1:])
            
    if not ids_to_delete:
        logger.info(f"No duplicates found in {target_table}! Everything is clean.")
        return 0
    
    logger.info(f"Found {len(ids_to_delete)} duplicate rows to remove in {target_table}.")
    
    # 3. Delete duplicates in batches (with retry)
    batch_size = 100
    deleted_count = 0
    
    for i in range(0, len(ids_to_delete), batch_size):
        batch = ids_to_delete[i:i+batch_size]
        # PostgREST syntax for 'in' is id=in.(1,2,3)
        id_list = ",".join(map(str, batch))
        delete_url = f"{base_url}/{target_table}?id=in.({id_list})"
        
        try:
            logger.info(f"Deleting batch {i//batch_size + 1} ({len(batch)} items) from {target_table}...")
            response = _request_with_retry("delete", delete_url, headers=headers)
            response.raise_for_status()
            deleted_count += len(batch)
        except Exception as e:
            logger.error(f"Error deleting batch from {target_table}: {e}")
            
    return deleted_count

if __name__ == "__main__":
    logger.info("Starting deduplication process...")
    
    # Run for all 3 tables
    jobs_removed = deduplicate("jobs")
    details_removed = deduplicate("job_details")
    chunks_removed = deduplicate("job_chunks")
    
    total = jobs_removed + details_removed + chunks_removed
    logger.info("=========================================")
    logger.info("DEDUPLICATION SUMMARY")
    logger.info(f"  Jobs removed:        {jobs_removed}")
    logger.info(f"  Job Details removed: {details_removed}")
    logger.info(f"  Job Chunks removed:  {chunks_removed}")
    logger.info(f"  Total removed:       {total}")
    logger.info("=========================================")

