#!/usr/bin/env python3
"""
Supabase Client for Job Pipeline
=================================
Pushes job data to Supabase PostgreSQL via the Supabase REST API.
This is the ONLY Supabase-related code in the Python pipeline.

DEDUPLICATION STRATEGY (Daily Incremental):
- Daily scraping with 25-hour lookback creates natural overlap
- Uses S3 to track ONLY yesterday's NEW job_ids (not all history)
- Each run:
  1. Load yesterday's NEW job_ids from S3
  2. Push only jobs NOT in yesterday's set
  3. Delete yesterday's S3 file
  4. Save TODAY's NEW job_ids for tomorrow
- Result: Minimal tracking file, perfect for daily overlap scenario

Environment Variables:
- SUPABASE_URL: Your Supabase project URL
- SUPABASE_SERVICE_ROLE_KEY: Service role key (for server-side inserts)
- DRY_RUN: Skip actual push if 'true' (default: 'false')
- CLEAR_SUPABASE: Clear Supabase tables before seeding if 'true' (default: 'false')
- BATCH_SIZE: Number of jobs per batch (default: 50)
- S3_BUCKET: S3 bucket for storing pushed job IDs (required for dedup)
- S3_PREFIX: S3 prefix (default: 'joblab-supabase')
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
class SupabaseConfig:
    """Configuration for Supabase client"""
    supabase_url: str
    service_role_key: str
    dry_run: bool = False
    clear_supabase: bool = False
    batch_size: int = 50
    timeout: int = 30
    # S3 config for deduplication tracking
    s3_bucket: Optional[str] = None
    s3_prefix: str = "joblab-supabase"
    aws_region: str = "us-east-1"


def get_supabase_config() -> SupabaseConfig:
    """Load Supabase configuration from environment variables"""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    
    if not url:
        raise ValueError(
            "SUPABASE_URL not set. "
            "Set via environment variable or .env file."
        )
    if not key:
        raise ValueError(
            "SUPABASE_SERVICE_ROLE_KEY not set. "
            "Set via environment variable or .env file."
        )
    
    return SupabaseConfig(
        supabase_url=url.rstrip("/"),
        service_role_key=key,
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        clear_supabase=os.environ.get("CLEAR_SUPABASE", "false").lower() == "true",
        batch_size=int(os.environ.get("BATCH_SIZE", "50")),
        s3_bucket=os.environ.get("S3_BUCKET"),
        s3_prefix=os.environ.get("S3_PREFIX", "joblab-supabase"),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
    )


# ============================================================================
# S3-BASED DEDUPLICATION TRACKING
# ============================================================================

class S3JobTracker:
    """
    Tracks yesterday's NEW job_ids using S3.
    Same strategy as Convex version but for Supabase pipeline.
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
        return f"{self.prefix}/latest/pushed_job_ids.json"
    
    def load_previous_job_ids(self) -> Set[str]:
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
            return True
        except Exception as e:
            logger.warning(f"   ⚠️  Could not save job_ids: {e}")
            return False
    
    def clear_tracking(self) -> bool:
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
# SUPABASE REST API CLIENT
# ============================================================================

class SupabaseRESTClient:
    """
    HTTP client for Supabase REST API (PostgREST).
    Uses the service_role key for server-side operations.
    """
    
    def __init__(self, config: SupabaseConfig):
        self.config = config
        self.base_url = f"{config.supabase_url}/rest/v1"
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": config.service_role_key,
            "Authorization": f"Bearer {config.service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        })
    
    def upsert_jobs(self, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upsert jobs into the 'jobs' table.
        Uses ON CONFLICT (job_id) DO UPDATE for deduplication.
        Returns count of affected rows.
        """
        url = f"{self.base_url}/jobs"
        headers = {
            **self.session.headers,
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        
        try:
            response = self.session.post(
                url,
                json=jobs,
                headers=headers,
                timeout=self.config.timeout
            )
            response.raise_for_status()
            result = response.json()
            return {"upserted": len(result)}
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error upserting jobs: {e}")
            logger.error(f"Response: {e.response.text if e.response else 'No response'}")
            raise
        except Exception as e:
            logger.error(f"Error upserting jobs: {e}")
            raise
    
    def upsert_job_details(self, details: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upsert job details into the 'job_details' table.
        Uses ON CONFLICT (job_id) DO UPDATE for deduplication.
        """
        url = f"{self.base_url}/job_details"
        headers = {
            **self.session.headers,
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        
        try:
            response = self.session.post(
                url,
                json=details,
                headers=headers,
                timeout=self.config.timeout
            )
            response.raise_for_status()
            result = response.json()
            return {"upserted": len(result)}
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error upserting details: {e}")
            logger.error(f"Response: {e.response.text if e.response else 'No response'}")
            raise
        except Exception as e:
            logger.error(f"Error upserting details: {e}")
            raise
    
    def clear_all_data(self) -> Dict[str, Any]:
        """Clear all jobs and job details."""
        deleted_details = 0
        deleted_jobs = 0
        
        try:
            # Delete details first (foreign-key-safe order)
            url_details = f"{self.base_url}/job_details?id=gt.0"
            headers = {**self.session.headers, "Prefer": "return=representation"}
            resp = self.session.delete(url_details, headers=headers, timeout=self.config.timeout)
            if resp.status_code == 200:
                deleted_details = len(resp.json())
            elif resp.status_code == 204:
                deleted_details = 0
            
            # Delete all jobs
            url_jobs = f"{self.base_url}/jobs?id=gt.0"
            resp = self.session.delete(url_jobs, headers=headers, timeout=self.config.timeout)
            if resp.status_code == 200:
                deleted_jobs = len(resp.json())
            elif resp.status_code == 204:
                deleted_jobs = 0
                
        except Exception as e:
            logger.error(f"Error clearing data: {e}")
            raise
        
        return {"deletedJobs": deleted_jobs, "deletedDetails": deleted_details}
    
    def get_existing_job_ids(self) -> Set[str]:
        """Get all existing job_ids from Supabase for dedup checking."""
        url = f"{self.base_url}/jobs?select=job_id"
        all_ids = set()
        offset = 0
        limit = 1000
        
        while True:
            try:
                resp = self.session.get(
                    f"{url}&offset={offset}&limit={limit}",
                    timeout=self.config.timeout
                )
                resp.raise_for_status()
                rows = resp.json()
                if not rows:
                    break
                for row in rows:
                    all_ids.add(row["job_id"])
                if len(rows) < limit:
                    break
                offset += limit
            except Exception as e:
                logger.warning(f"Error fetching existing job_ids: {e}")
                break
        
        return all_ids


# ============================================================================
# DATABASE READING
# ============================================================================

def read_jobs_from_db(db_path: Path) -> List[Dict[str, Any]]:
    """
    Read jobs from SQLite database and transform for Supabase.
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
    
    # Add tools column if exists (new column added for optimization tools)
    if "tools" in columns:
        select_columns.append("tools")
    
    query = f"SELECT {', '.join(select_columns)} FROM jobs"
    logger.info(f"Query: {query[:80]}...")
    
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    
    logger.info(f"Found {len(rows)} jobs in database")
    
    # Transform to Supabase format (PostgreSQL-compatible)
    jobs = []
    for row in rows:
        job = {
            "job_id": row["job_id"],
            "platform": row["platform"] or "unknown",
            "url": row["url"] or "",
            "actual_role": row["actual_role"] or "Unknown",
            "skills": row["skills"] if row["skills"] else None,
            "tools": row["tools"] if "tools" in row.keys() and row["tools"] else None,
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
        
        # Store description separately
        if "job_description" in row.keys() and row["job_description"]:
            job["_description"] = row["job_description"]
        
        jobs.append(job)
    
    return jobs


# ============================================================================
# MAIN PUSH FUNCTION
# ============================================================================

@dataclass
class PushResult:
    """Result of pushing jobs to Supabase"""
    jobs_inserted: int = 0
    jobs_skipped: int = 0
    jobs_updated: int = 0
    details_inserted: int = 0
    details_skipped: int = 0
    total_in_db: int = 0
    new_jobs_count: int = 0
    pushed_job_ids: List[str] = field(default_factory=list)  # job_ids that were upserted
    errors: List[str] = field(default_factory=list)
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def push_jobs_to_supabase(
    db_path: Path,
    config: Optional[SupabaseConfig] = None
) -> PushResult:
    """
    Push jobs from SQLite database to Supabase.
    
    Uses PostgreSQL UPSERT (ON CONFLICT) for deduplication:
    - job_id is the unique constraint
    - New jobs are inserted, existing jobs are updated
    """
    result = PushResult()
    
    # Load config if not provided
    if config is None:
        try:
            config = get_supabase_config()
        except ValueError as e:
            logger.error(str(e))
            result.errors.append(str(e))
            return result
    
    logger.info("=" * 60)
    logger.info("SUPABASE PUSH")
    logger.info("=" * 60)
    logger.info(f"Supabase URL:   {config.supabase_url[:40]}...")
    logger.info(f"Database:       {db_path}")
    logger.info(f"Dry Run:        {config.dry_run}")
    logger.info(f"Clear Supabase: {config.clear_supabase}")
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
    
    # Initialize REST client
    client = SupabaseRESTClient(config)
    
    # Dry run check
    if config.dry_run:
        logger.info("")
        logger.info("🏜️  DRY RUN MODE - No changes will be made to Supabase")
        logger.info(f"   Would process {len(all_jobs)} jobs")
        logger.info(f"   CLEAR_SUPABASE: {config.clear_supabase}")
        logger.info("")
        logger.info("✅ Dry run complete!")
        return result
    
    # Clear data if requested
    if config.clear_supabase:
        logger.info("")
        logger.info("⚠️  CLEAR_SUPABASE=true - Clearing all existing data...")
        try:
            cleared = client.clear_all_data()
            logger.info(f"   ✓ Cleared {cleared.get('deletedJobs', 0)} jobs and {cleared.get('deletedDetails', 0)} details")
        except Exception as e:
            logger.error(f"   ✗ Failed to clear: {e}")
            result.errors.append(f"Clear failed: {str(e)}")
            return result
    
    # All jobs from the current DB — Supabase UPSERT handles dedup via job_id
    new_jobs = all_jobs
    result.new_jobs_count = len(new_jobs)
    logger.info(f"📊 Jobs to push: {len(new_jobs)}")
    
    if not new_jobs:
        logger.info("")
        logger.info("✅ No jobs to upload")
        return result
    
    # Upload jobs in batches using UPSERT
    logger.info(f"\n⬆️  Upserting {len(new_jobs)} jobs in batches of {config.batch_size}...")
    
    batch_size = config.batch_size
    total_batches = (len(new_jobs) + batch_size - 1) // batch_size
    
    for i in range(0, len(new_jobs), batch_size):
        batch = new_jobs[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        # Prepare batch for Supabase (remove internal _description field)
        supabase_batch = []
        for job in batch:
            job_copy = {k: v for k, v in job.items() if not k.startswith("_")}
            supabase_batch.append(job_copy)
        
        try:
            batch_result = client.upsert_jobs(supabase_batch)
            upserted = int(batch_result.get("upserted", 0))
            result.jobs_inserted += upserted
            
            # Track pushed job_ids for downstream embedding step
            for job in batch:
                if "job_id" in job:
                    result.pushed_job_ids.append(job["job_id"])
            
            logger.info(
                f"   ✓ Batch {batch_num}/{total_batches}: "
                f"{upserted} upserted"
            )
            
            # Small delay to avoid rate limiting
            if batch_num < total_batches:
                time.sleep(0.1)
                
        except Exception as e:
            logger.error(f"   ✗ Batch {batch_num}/{total_batches} failed: {e}")
            result.errors.append(f"Batch {batch_num} failed: {str(e)}")
    
    logger.info(f"\n✅ Jobs: {result.jobs_inserted} upserted")
    
    # Upload job descriptions
    descriptions = []
    for job in new_jobs:
        if job.get("_description"):
            descriptions.append({
                "job_id": job["job_id"],
                "job_description": job["_description"]
            })
    
    if descriptions:
        logger.info(f"\n⬆️  Upserting {len(descriptions)} descriptions...")
        
        total_detail_batches = (len(descriptions) + batch_size - 1) // batch_size
        
        for i in range(0, len(descriptions), batch_size):
            batch = descriptions[i:i + batch_size]
            batch_num = i // batch_size + 1
            
            try:
                batch_result = client.upsert_job_details(batch)
                upserted = int(batch_result.get("upserted", 0))
                result.details_inserted += upserted
                
                logger.info(
                    f"   ✓ Batch {batch_num}/{total_detail_batches}: "
                    f"{upserted} upserted"
                )
                
                if batch_num < total_detail_batches:
                    time.sleep(0.1)
                    
            except Exception as e:
                logger.error(f"   ✗ Batch {batch_num}/{total_detail_batches} failed: {e}")
                result.errors.append(f"Details batch {batch_num} failed: {str(e)}")
        
        logger.info(f"\n✅ Descriptions: {result.details_inserted} upserted")
    else:
        logger.info("\n✅ No descriptions to push")
    
    # Final summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ SUPABASE PUSH COMPLETE")
    logger.info("=" * 60)
    logger.info("📊 Summary:")
    logger.info(f"   Jobs:         {result.jobs_inserted} upserted")
    logger.info(f"   Descriptions: {result.details_inserted} upserted")
    logger.info(f"   Source DB:    {db_path}")
    logger.info("=" * 60)
    
    return result


# ============================================================================
# SUPABASE TABLE CREATION (run once)
# ============================================================================

SUPABASE_SCHEMA_SQL = """
-- Jobs table
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL DEFAULT 'unknown',
    url TEXT NOT NULL DEFAULT '',
    actual_role TEXT NOT NULL DEFAULT 'Unknown',
    skills TEXT,
    tools TEXT,
    search_term TEXT,
    job_type_filled TEXT NOT NULL DEFAULT 'Not Specified',
    job_level_std TEXT NOT NULL DEFAULT 'Not Specified',
    job_function_std TEXT NOT NULL DEFAULT 'Other',
    company_industry_std TEXT NOT NULL DEFAULT 'Other',
    education_level TEXT,
    company_name TEXT NOT NULL DEFAULT 'Unknown',
    country TEXT NOT NULL DEFAULT 'Unknown',
    location TEXT,
    is_remote BOOLEAN NOT NULL DEFAULT FALSE,
    posted_date TEXT,
    job_relevance_score FLOAT,
    has_url_duplicate INTEGER NOT NULL DEFAULT 0,
    is_research BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Job details table (descriptions, lazy loaded)
CREATE TABLE IF NOT EXISTS job_details (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT UNIQUE NOT NULL,
    job_description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country);
CREATE INDEX IF NOT EXISTS idx_jobs_job_type ON jobs(job_type_filled);
CREATE INDEX IF NOT EXISTS idx_jobs_job_level ON jobs(job_level_std);
CREATE INDEX IF NOT EXISTS idx_jobs_job_function ON jobs(job_function_std);
CREATE INDEX IF NOT EXISTS idx_jobs_industry ON jobs(company_industry_std);
CREATE INDEX IF NOT EXISTS idx_jobs_platform ON jobs(platform);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_date ON jobs(posted_date);
CREATE INDEX IF NOT EXISTS idx_jobs_duplicate ON jobs(has_url_duplicate);
CREATE INDEX IF NOT EXISTS idx_jobs_job_id ON jobs(job_id);
CREATE INDEX IF NOT EXISTS idx_job_details_job_id ON job_details(job_id);

-- Enable Row Level Security (RLS) for public read access
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_details ENABLE ROW LEVEL SECURITY;

-- Allow anonymous read access (dashboard users)
CREATE POLICY IF NOT EXISTS "Allow public read access on jobs"
    ON jobs FOR SELECT
    USING (true);

CREATE POLICY IF NOT EXISTS "Allow public read access on job_details"
    ON job_details FOR SELECT
    USING (true);

-- Allow service_role full access (for Python pipeline inserts)
CREATE POLICY IF NOT EXISTS "Allow service role full access on jobs"
    ON jobs FOR ALL
    USING (true)
    WITH CHECK (true);

CREATE POLICY IF NOT EXISTS "Allow service role full access on job_details"
    ON job_details FOR ALL
    USING (true)
    WITH CHECK (true);
"""


def setup_supabase_tables(config: Optional[SupabaseConfig] = None):
    """
    Create tables in Supabase using the REST API's rpc endpoint.
    Run this once before first pipeline execution.
    
    NOTE: You should run the SUPABASE_SCHEMA_SQL manually in the
    Supabase SQL Editor (Dashboard > SQL Editor) for the initial setup.
    """
    if config is None:
        config = get_supabase_config()
    
    logger.info("=" * 60)
    logger.info("SUPABASE TABLE SETUP")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Please run the following SQL in your Supabase SQL Editor:")
    logger.info("(Dashboard > SQL Editor > New Query)")
    logger.info("")
    logger.info(SUPABASE_SCHEMA_SQL)
    logger.info("")
    logger.info("=" * 60)


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    
    parser = argparse.ArgumentParser(description="Push jobs to Supabase")
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
        help="Clear Supabase before pushing",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Print SQL needed to set up Supabase tables",
    )
    
    args = parser.parse_args()
    
    if args.setup:
        setup_supabase_tables()
        exit(0)
    
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
        os.environ["CLEAR_SUPABASE"] = "true"
    
    # Load .env if available
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.info(f"Loaded .env from {env_path}")
    except ImportError:
        pass
    
    result = push_jobs_to_supabase(db_path)
    
    exit(0 if result.success else 1)
