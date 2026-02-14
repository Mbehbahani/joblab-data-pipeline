#!/usr/bin/env python3
"""
Convex HTTP Client for Job Pipeline
====================================
Pushes job data to Convex via HTTP mutations.
This is the ONLY Convex-related code in the Python pipeline.

Convex schema/mutations/queries live in the Next.js frontend repo.
This module only knows Convex as an HTTP API endpoint.

DEDUPLICATION STRATEGY (Daily Incremental):
- Daily scraping with 25-hour lookback creates natural overlap
- Uses S3 to track ONLY yesterday's NEW job_ids (not all history)
- Each run:
  1. Load yesterday's NEW job_ids from S3
  2. Push only jobs NOT in yesterday's set
  3. Delete yesterday's S3 file
  4. Save TODAY's NEW job_ids for tomorrow
- Result: Minimal tracking file, perfect for daily overlap scenario

Example:
  Day 1: Find 1000 jobs → Push 1000 → Save 1000 IDs to S3
  Day 2: Find 1100 jobs → 1000 overlap + 100 new
         → Compare against yesterday's 1000 IDs
         → Push only 100 new jobs
         → Delete yesterday's file
         → Save only today's 100 NEW IDs to S3
  Day 3: Find 1150 jobs → 100 overlap + 1050 new
         → Compare against yesterday's 100 IDs
         → Push 1050 new jobs
         → Save only today's 1050 NEW IDs to S3

Environment Variables:
- CONVEX_DEPLOYMENT_URL: Your Convex deployment URL
- DRY_RUN: Skip actual push if 'true' (default: 'false')
- CLEAR_CONVEX: Clear Convex tables before seeding if 'true' (default: 'false')
- BATCH_SIZE: Number of jobs per batch (default: 50)
- S3_BUCKET: S3 bucket for storing pushed job IDs (required for dedup)
- S3_PREFIX: S3 prefix (default: 'joblab')
- AWS_REGION: AWS region (default: 'us-east-1')
"""

import os
import json
import sqlite3
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class ConvexConfig:
    """Configuration for Convex HTTP client"""
    deployment_url: str
    dry_run: bool = False
    clear_convex: bool = False
    batch_size: int = 50
    timeout: int = 30  # Request timeout in seconds
    # S3 config for deduplication tracking
    s3_bucket: Optional[str] = None
    s3_prefix: str = "joblab"
    aws_region: str = "us-east-1"


def get_convex_config() -> ConvexConfig:
    """Load Convex configuration from environment variables"""
    url = os.environ.get("CONVEX_DEPLOYMENT_URL") or os.environ.get("NEXT_PUBLIC_CONVEX_URL")
    
    if not url:
        raise ValueError(
            "CONVEX_DEPLOYMENT_URL or NEXT_PUBLIC_CONVEX_URL not set. "
            "Set via environment variable or .env file."
        )
    
    return ConvexConfig(
        deployment_url=url.rstrip("/"),
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        clear_convex=os.environ.get("CLEAR_CONVEX", "false").lower() == "true",
        batch_size=int(os.environ.get("BATCH_SIZE", "50")),
        s3_bucket=os.environ.get("S3_BUCKET"),
        s3_prefix=os.environ.get("S3_PREFIX", "joblab"),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
    )


# ============================================================================
# S3-BASED DEDUPLICATION TRACKING
# ============================================================================

class S3JobTracker:
    """
    Tracks yesterday's NEW job_ids using S3.
    
    Strategy for daily 25-hour scraping overlap:
    - Each day, save ONLY today's newly pushed job_ids
    - Tomorrow, compare against today's IDs to find new jobs
    - Replace today's file with tomorrow's new IDs
    - No accumulation, just yesterday's new jobs
    
    This is perfect because:
    1. Fast local comparison (no HTTP calls to Convex)
    2. Minimal storage (only 1 day's worth of IDs)
    3. Handles the 25-hour overlap perfectly
    """
    
    def __init__(self, bucket: str, prefix: str, region: str = "us-east-1"):
        self.bucket = bucket
        self.prefix = prefix
        self.region = region
        self._s3_client = None
    
    @property
    def s3_client(self):
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client('s3', region_name=self.region)
        return self._s3_client
    
    @property
    def tracking_key(self) -> str:
        """S3 key for the pushed job IDs file"""
        return f"{self.prefix}/latest/pushed_job_ids.json"
    
    def load_previous_job_ids(self) -> Set[str]:
        """
        Load YESTERDAY's NEW job_ids from S3.
        Returns empty set if file doesn't exist (first run).
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=self.tracking_key
            )
            data = json.loads(response['Body'].read().decode('utf-8'))
            job_ids = set(data.get("job_ids", []))
            logger.info(f"   ✓ Loaded {len(job_ids)} job_ids from yesterday's push")
            return job_ids
        except self.s3_client.exceptions.NoSuchKey:
            logger.info("   ℹ️  No previous tracking file (first run or cleared)")
            return set()
        except Exception as e:
            logger.warning(f"   ⚠️  Could not load previous job_ids: {e}")
            return set()
    
    def save_new_job_ids(self, job_ids: Set[str]) -> bool:
        """
        Save ONLY today's NEW job_ids for tomorrow's comparison.
        This replaces yesterday's file completely (no accumulation).
        
        Strategy: Each day we only check against yesterday's NEW jobs.
        This handles the 25-hour overlap perfectly without growing the tracking file.
        """
        try:
            data = {
                "job_ids": list(job_ids),
                "count": len(job_ids),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "note": "Contains only NEW jobs from this run (not all historical jobs)"
            }
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=self.tracking_key,
                Body=json.dumps(data),
                ContentType='application/json'
            )
            logger.info(f"   ✓ Saved {len(job_ids)} NEW job_ids for tomorrow's comparison")
            logger.info(f"   ✓ Deleted previous day's tracking data (replaced with today's)")
            return True
        except Exception as e:
            logger.warning(f"   ⚠️  Could not save job_ids: {e}")
            return False
    
    def clear_tracking(self) -> bool:
        """Clear the tracking file (used when CLEAR_CONVEX=true)"""
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket,
                Key=self.tracking_key
            )
            logger.info("   ✓ Cleared previous job tracking data")
            return True
        except Exception as e:
            logger.warning(f"   ⚠️  Could not clear tracking: {e}")
            return False


# ============================================================================
# CONVEX HTTP CLIENT
# ============================================================================

class ConvexHTTPClient:
    """HTTP client for Convex mutations and queries"""
    
    def __init__(self, config: ConvexConfig):
        self.config = config
        self.base_url = config.deployment_url
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })
    
    def _call_function(self, path: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a Convex function via HTTP.
        
        Args:
            path: 'mutation' or 'query'
            name: Function name (e.g., 'mutations:seedJobs')
            args: Arguments to pass to the function
        
        Returns:
            Response JSON
        """
        url = f"{self.base_url}/api/{path}"
        payload = {
            "path": name,
            "args": args,
            "format": "json",
        }
        
        logger.debug(f"Calling {path} {name} with {len(str(args))} bytes")
        
        try:
            response = self.session.post(
                url,
                json=payload,
                timeout=self.config.timeout
            )
            response.raise_for_status()
            
            result = response.json()
            
            # Convex wraps the result in a 'value' field
            if "value" in result:
                return result["value"]
            return result
            
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout calling {name}")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error calling {name}: {e}")
            logger.error(f"Response: {e.response.text if e.response else 'No response'}")
            raise
        except Exception as e:
            logger.error(f"Error calling {name}: {e}")
            raise
    
    def mutation(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Call a Convex mutation"""
        return self._call_function("mutation", name, args)
    
    def query(self, name: str, args: Dict[str, Any] = None) -> Any:
        """Call a Convex query"""
        return self._call_function("query", name, args or {})
    
    # Specific mutations
    
    def seed_jobs(self, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Insert jobs (skip existing duplicates).
        Returns: {"inserted": N, "skipped": N}
        """
        return self.mutation("mutations:seedJobs", {"jobs": jobs})
    
    def seed_job_details(self, details: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Insert job descriptions (skip existing duplicates).
        Returns: {"inserted": N, "skipped": N}
        """
        return self.mutation("mutations:seedJobDetails", {"details": details})
    
    def clear_all_data(self) -> Dict[str, Any]:
        """
        Clear all jobs and job details.
        Returns: {"deletedJobs": N, "deletedDetails": N}
        """
        return self.mutation("mutations:clearAllData", {})


# ============================================================================
# DATABASE READING
# ============================================================================

def read_jobs_from_db(db_path: Path) -> List[Dict[str, Any]]:
    """
    Read jobs from SQLite database and transform for Convex.
    
    Args:
        db_path: Path to the jobs_enriched.db file
    
    Returns:
        List of job dictionaries ready for Convex
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    logger.info(f"Reading database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get column info
    cursor.execute("PRAGMA table_info(jobs)")
    columns = {row["name"] for row in cursor.fetchall()}
    logger.info(f"Database columns: {len(columns)}")
    
    # Build dynamic query based on available columns
    select_columns = [
        "job_id",
        "platform",
        "url",
        "actual_role",
        "skills",
        "search_term",
        "job_type_filled",
        "job_level_std",
        "job_function_std",
        "company_industry_std",
        "education_level",
        "company_name",
        "country",
        "location",
        "is_remote",
        "posted_date",
        "has_url_duplicate",
        "is_research",
    ]
    
    # Add job_description (prefer clean version)
    if "job_description_clean" in columns:
        select_columns.append("job_description_clean AS job_description")
    elif "job_description" in columns:
        select_columns.append("job_description")
    
    # Add job_relevance_score if exists
    if "job_relevance_score" in columns:
        select_columns.append("job_relevance_score")
    
    query = f"SELECT {', '.join(select_columns)} FROM jobs"
    logger.info(f"Query: {query[:80]}...")
    
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    
    logger.info(f"Found {len(rows)} jobs in database")
    
    # Transform to Convex format
    jobs = []
    for row in rows:
        job = {
            "job_id": row["job_id"],
            "platform": row["platform"] or "unknown",
            "url": row["url"] or "",
            "actual_role": row["actual_role"] or "Unknown",
            "skills": row["skills"] if row["skills"] else None,
            "search_term": row["search_term"] if row["search_term"] else None,
            "job_type_filled": row["job_type_filled"] or "Not Specified",
            "job_level_std": row["job_level_std"] or "Not Specified",
            "job_function_std": row["job_function_std"] or "Other",
            "company_industry_std": row["company_industry_std"] or "Other",
            "education_level": row["education_level"] if row["education_level"] else None,
            "company_name": row["company_name"] or "Unknown",
            "country": row["country"] or "Unknown",
            "location": row["location"] if row["location"] else None,
            "is_remote": bool(row["is_remote"]),
            "posted_date": row["posted_date"] if row["posted_date"] else None,
            "has_url_duplicate": row["has_url_duplicate"] or 0,
            "is_research": bool(row["is_research"]),
        }
        
        # Add job_relevance_score if present
        if "job_relevance_score" in row.keys():
            score = row["job_relevance_score"]
            job["job_relevance_score"] = float(score) if score is not None else None
        
        # Store description for later (only for new jobs)
        if "job_description" in row.keys() and row["job_description"]:
            job["_description"] = row["job_description"]
        
        jobs.append(job)
    
    return jobs


# ============================================================================
# MAIN PUSH FUNCTION
# ============================================================================

@dataclass
class PushResult:
    """Result of pushing jobs to Convex"""
    jobs_inserted: int = 0
    jobs_skipped: int = 0
    jobs_updated: int = 0
    details_inserted: int = 0
    details_skipped: int = 0
    total_in_db: int = 0
    new_jobs_count: int = 0
    errors: List[str] = field(default_factory=list)
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def push_jobs_to_convex(
    db_path: Path,
    config: Optional[ConvexConfig] = None
) -> PushResult:
    """
    Push jobs from SQLite database to Convex.
    
    Daily incremental push (no S3-based dedup):
    - Each pipeline run starts fresh with only the current run's jobs
    - All jobs in the DB are new for this run (self-dedup already done by scraper)
    - Convex-side seedJobs mutation handles any remaining duplicates via job_id
    
    Args:
        db_path: Path to the jobs_enriched.db file
        config: Optional ConvexConfig (loads from env if not provided)
    
    Returns:
        PushResult with statistics
    """
    result = PushResult()
    
    # Load config if not provided
    if config is None:
        try:
            config = get_convex_config()
        except ValueError as e:
            logger.error(str(e))
            result.errors.append(str(e))
            return result
    
    logger.info("=" * 60)
    logger.info("CONVEX PUSH")
    logger.info("=" * 60)
    logger.info(f"Deployment URL: {config.deployment_url[:40]}...")
    logger.info(f"Database:       {db_path}")
    logger.info(f"Dry Run:        {config.dry_run}")
    logger.info(f"Clear Convex:   {config.clear_convex}")
    logger.info(f"Batch Size:     {config.batch_size}")
    logger.info("")
    
    # Read jobs from database
    try:
        all_jobs = read_jobs_from_db(db_path)
        result.total_in_db = len(all_jobs)
    except Exception as e:
        logger.error(f"Failed to read database: {e}")
        result.errors.append(f"Database read: {str(e)}")
        return result
    
    if not all_jobs:
        logger.warning("No jobs found in database")
        return result
    
    # Initialize HTTP client
    client = ConvexHTTPClient(config)
    
    # Dry run check
    if config.dry_run:
        logger.info("")
        logger.info("🏜️  DRY RUN MODE - No changes will be made to Convex")
        logger.info(f"   Would process {len(all_jobs)} jobs")
        logger.info(f"   CLEAR_CONVEX: {config.clear_convex}")
        logger.info("")
        logger.info("✅ Dry run complete!")
        return result
    
    # Clear data if requested
    if config.clear_convex:
        logger.info("")
        logger.info("⚠️  CLEAR_CONVEX=true - Clearing all existing data...")
        try:
            cleared = client.clear_all_data()
            logger.info(f"   ✓ Cleared {cleared.get('deletedJobs', 0)} jobs and {cleared.get('deletedDetails', 0)} details")
        except Exception as e:
            logger.error(f"   ✗ Failed to clear: {e}")
            result.errors.append(f"Clear failed: {str(e)}")
            return result
    
    # All jobs in the current DB are from this run — push them all
    # Convex seedJobs mutation handles duplicates server-side via job_id
    new_jobs = all_jobs
    result.new_jobs_count = len(new_jobs)
    logger.info(f"📊 Jobs to push: {len(new_jobs)}")
    
    if not new_jobs:
        logger.info("")
        logger.info("✅ No jobs to upload")
        return result
    
    # Track which job_ids were actually inserted (for S3 tracking)
    inserted_job_ids: Set[str] = set()
    
    # Upload jobs in batches
    logger.info(f"\n⬆️  Pushing {len(new_jobs)} NEW jobs in batches of {config.batch_size}...")
    
    batch_size = config.batch_size
    total_batches = (len(new_jobs) + batch_size - 1) // batch_size
    
    for i in range(0, len(new_jobs), batch_size):
        batch = new_jobs[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        # Prepare batch for Convex (remove internal _description field)
        convex_batch = []
        for job in batch:
            job_copy = {k: v for k, v in job.items() if not k.startswith("_")}
            convex_batch.append(job_copy)
        
        try:
            batch_result = client.seed_jobs(convex_batch)
            inserted = int(batch_result.get("inserted", 0))
            skipped = int(batch_result.get("skipped", 0))
            
            result.jobs_inserted += inserted
            
            # Track inserted job_ids for description push
            # If all were inserted, add all; otherwise Convex handled duplicates
            if inserted == len(batch):
                for job in batch:
                    inserted_job_ids.add(job["job_id"])
            elif inserted > 0:
                # Some were inserted - we don't know which, so add all
                # Convex will handle description dedup
                for job in batch:
                    inserted_job_ids.add(job["job_id"])
            
            logger.info(
                f"   ✓ Batch {batch_num}/{total_batches}: "
                f"{inserted} inserted, {skipped} skipped"
            )
            
            # Small delay to avoid rate limiting
            if batch_num < total_batches:
                time.sleep(0.1)
                
        except Exception as e:
            logger.error(f"   ✗ Batch {batch_num}/{total_batches} failed: {e}")
            result.errors.append(f"Batch {batch_num} failed: {str(e)}")
            # Continue with next batch
    
    logger.info(f"\n✅ Jobs: {result.jobs_inserted} inserted")
    
    # Upload job descriptions ONLY for inserted jobs
    if inserted_job_ids:
        # Extract descriptions only for newly inserted jobs
        descriptions = []
        for job in new_jobs:
            if job["job_id"] in inserted_job_ids and job.get("_description"):
                descriptions.append({
                    "job_id": job["job_id"],
                    "job_description": job["_description"]
                })
        
        if descriptions:
            logger.info(f"\n⬆️  Pushing {len(descriptions)} descriptions for NEW jobs only...")
            
            total_detail_batches = (len(descriptions) + batch_size - 1) // batch_size
            
            for i in range(0, len(descriptions), batch_size):
                batch = descriptions[i:i + batch_size]
                batch_num = i // batch_size + 1
                
                try:
                    batch_result = client.seed_job_details(batch)
                    inserted = int(batch_result.get("inserted", 0))
                    skipped = int(batch_result.get("skipped", 0))
                    
                    result.details_inserted += inserted
                    result.details_skipped += skipped
                    
                    logger.info(
                        f"   ✓ Batch {batch_num}/{total_detail_batches}: "
                        f"{inserted} inserted, {skipped} skipped"
                    )
                    
                    if batch_num < total_detail_batches:
                        time.sleep(0.1)
                        
                except Exception as e:
                    logger.error(f"   ✗ Batch {batch_num}/{total_detail_batches} failed: {e}")
                    result.errors.append(f"Details batch {batch_num} failed: {str(e)}")
            
            logger.info(f"\n✅ Descriptions: {result.details_inserted} inserted, {result.details_skipped} skipped")
        else:
            logger.info("\n✅ No descriptions to push (none found for new jobs)")
    
    # Final summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ CONVEX PUSH COMPLETE")
    logger.info("=" * 60)
    logger.info("📊 Summary:")
    logger.info(f"   Jobs:         {result.jobs_inserted} inserted, {result.jobs_skipped} skipped")
    logger.info(f"   Descriptions: {result.details_inserted} inserted, {result.details_skipped} skipped")
    logger.info(f"   Source DB:    {db_path}")
    logger.info("=" * 60)
    
    return result


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    
    parser = argparse.ArgumentParser(description="Push jobs to Convex")
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to jobs_enriched.db",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip actual push",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear Convex before pushing",
    )
    
    args = parser.parse_args()
    
    # Determine database path
    if args.db_path:
        db_path = Path(args.db_path)
    else:
        db_path = Path(os.environ.get(
            "JOB_DB_PATH",
            Path(__file__).parent.parent.parent / "3- Enrichment + Standardization" / "jobs_enriched.db"
        ))
    
    # Override config from args
    if args.dry_run:
        os.environ["DRY_RUN"] = "true"
    if args.clear:
        os.environ["CLEAR_CONVEX"] = "true"
    
    result = push_jobs_to_convex(db_path)
    
    exit(0 if result.success else 1)
