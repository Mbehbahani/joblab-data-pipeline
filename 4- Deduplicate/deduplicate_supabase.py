#!/usr/bin/env python3
import os
import sys
import logging
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

def deduplicate(target_table="jobs"):
    # Load environment variables
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    
    supabase_url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not service_role_key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)
    
    base_url = f"{supabase_url.rstrip('/')}/rest/v1"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    # 1. Fetch all urls and their primary IDs
    logger.info(f"Fetching rows from {target_table} table...")
    all_rows = []
    offset = 0
    limit = 1000
    
    while True:
        url = f"{base_url}/{target_table}?select=id,url&order=url,id&offset={offset}&limit={limit}"
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            all_rows.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
            logger.info(f"  Fetched {len(all_rows)} rows so far...")
        except Exception as e:
            logger.error(f"Error fetching from {target_table}: {e}")
            break
            
    if not all_rows:
        logger.info(f"No rows found in {target_table}.")
        return 0
    
    logger.info(f"Total rows found in {target_table}: {len(all_rows)}")
    
    # 2. Identify duplicates
    # We'll keep the first ID for each url and delete the rest
    rows_by_url = defaultdict(list)
    for row in all_rows:
        rows_by_url[row['url']].append(row['id'])
    
    ids_to_delete = []
    for url, ids in rows_by_url.items():
        if len(ids) > 1:
            # Sort ids to be sure we keep the smallest one (oldest)
            ids.sort()
            # Keep the first one, delete the others
            ids_to_delete.extend(ids[1:])
            
    if not ids_to_delete:
        logger.info(f"No duplicates found in {target_table}! Everything is clean.")
        return 0
    
    logger.info(f"Found {len(ids_to_delete)} duplicate rows to remove in {target_table}.")
    
    # 3. Delete duplicates in batches
    batch_size = 100
    deleted_count = 0
    
    for i in range(0, len(ids_to_delete), batch_size):
        batch = ids_to_delete[i:i+batch_size]
        # PostgREST syntax for 'in' is id=in.(1,2,3)
        id_list = ",".join(map(str, batch))
        delete_url = f"{base_url}/{target_table}?id=in.({id_list})"
        
        try:
            logger.info(f"Deleting batch {i//batch_size + 1} ({len(batch)} items) from {target_table}...")
            response = requests.delete(delete_url, headers=headers)
            response.raise_for_status()
            deleted_count += len(batch)
        except Exception as e:
            logger.error(f"Error deleting batch from {target_table}: {e}")
            
    return deleted_count

if __name__ == "__main__":
    logger.info("Starting deduplication process...")
    
    # Run for 'jobs' table
    jobs_removed = deduplicate("jobs")
    
    # Also run for 'job_details' table as they go together
    details_removed = deduplicate("job_details")
    
    logger.info("=========================================")
    logger.info("DEDUPLICATION SUMMARY")
    logger.info(f"  Jobs removed: {jobs_removed}")
    logger.info(f"  Job Details removed: {details_removed}")
    logger.info(f"  Total duplicates removed: {jobs_removed + details_removed}")
    logger.info("=========================================")

