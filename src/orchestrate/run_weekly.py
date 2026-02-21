#!/usr/bin/env python3
"""
Weekly Job Pipeline Orchestrator
================================
Single entrypoint for AWS ECS scheduled task that runs:
1. Scraping (jobs.db)
2. Preprocessing (jobs_processed.db)
3. Enrichment/Standardization (jobs_enriched.db)
4. S3 Upload (atomic snapshot)
5. Supabase Push (upsert via REST API)
6. Deduplication (remove duplicate jobs from Supabase)

Environment Variables:
- S3_BUCKET: S3 bucket name for snapshots
- S3_PREFIX: Prefix path in bucket (default: 'joblab-supabase')
- AWS_REGION: AWS region (default: 'us-east-1')
- JOB_DB_PATH: Override for final DB path (optional)
- SUPABASE_URL: Your Supabase project URL
- SUPABASE_SERVICE_ROLE_KEY: Service role key for server-side inserts
- DRY_RUN: Skip Supabase push if 'true' (default: 'false')
- CLEAR_SUPABASE: Clear Supabase tables before seeding if 'true' (default: 'false')
- SCRAPE_COUNTRIES: Comma-separated countries (optional)
- SCRAPE_MAX_JOBS: Max jobs per country (default: 50)
- DAYS_BACK: How many days back to scrape (default: 7)

Usage:
    python run_weekly.py                    # Full pipeline
    python run_weekly.py --dry-run          # Skip Supabase push
    python run_weekly.py --skip-scrape      # Skip scraping (use existing DB)
    python run_weekly.py --local            # Local mode (skip S3 upload)

Note:
    Supabase push is pure Python (REST API client).
    No Node.js/TypeScript required.
"""

import os
import sys
import shutil
import sqlite3
import subprocess
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from export.to_supabase import push_jobs_to_supabase, PushResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Project root (parent of src/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Step directories
SCRAPE_DIR = PROJECT_ROOT / "1- Scrapped Data"
PREPROCESS_DIR = PROJECT_ROOT / "2- Preprocessed"
ENRICHMENT_DIR = PROJECT_ROOT / "3- Enrichment + Standardization"
DEDUPLICATE_DIR = PROJECT_ROOT / "4- Deduplicate"

# Default work directory
DEFAULT_WORKDIR = Path("/tmp/joblab_run")


def get_run_id() -> str:
    """Generate UTC timestamp-based run ID"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def get_env_config() -> Dict[str, Any]:
    """Load configuration from environment variables"""
    return {
        "s3_bucket": os.environ.get("S3_BUCKET"),
        "s3_prefix": os.environ.get("S3_PREFIX", "joblab-supabase"),
        "aws_region": os.environ.get("AWS_REGION", "us-east-1"),
        "dry_run": os.environ.get("DRY_RUN", "false").lower() == "true",
        "clear_supabase": os.environ.get("CLEAR_SUPABASE", "false").lower() == "true",
        "supabase_url": os.environ.get("SUPABASE_URL"),
        "supabase_service_role_key": os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
        "scrape_countries": os.environ.get("SCRAPE_COUNTRIES"),
        "scrape_max_jobs": int(os.environ.get("SCRAPE_MAX_JOBS", "50")),
        "days_back": int(os.environ.get("DAYS_BACK", "7")),
    }


class PipelineOrchestrator:
    """Orchestrates the weekly job pipeline execution"""
    
    def __init__(self, run_id: str, workdir: Path, config: Dict[str, Any]):
        self.run_id = run_id
        self.workdir = workdir / run_id
        self.config = config
        self.stats = {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "jobs_scraped": 0,
            "jobs_processed": 0,
            "jobs_enriched": 0,
            "duplicates_detected": 0,
            "s3_snapshot_path": None,
            "supabase_upserted": 0,
            "supabase_skipped": 0,
            "duplicates_removed": 0,
            "details_removed": 0,
            "chunks_removed": 0,
            "errors": [],
        }
        
    def setup_workdir(self):
        """Create working directory structure"""
        logger.info(f"Setting up work directory: {self.workdir}")
        self.workdir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories matching original structure
        (self.workdir / "1- Scrapped Data").mkdir(exist_ok=True)
        (self.workdir / "2- Preprocessed").mkdir(exist_ok=True)
        (self.workdir / "3- Enrichment + Standardization").mkdir(exist_ok=True)
        
        logger.info("Work directory structure created")
    
    def ensure_clean_stage_dbs(self):
        """Remove any leftover databases from previous runs so each run starts fresh.
        The scraper will create a new jobs.db; preprocess/enrich will copy from prior stage."""
        for stale_db in [
            SCRAPE_DIR / "jobs.db",
            PREPROCESS_DIR / "jobs_processed.db",
            ENRICHMENT_DIR / "jobs_enriched.db",
        ]:
            if stale_db.exists():
                stale_db.unlink()
                logger.info(f"Removed stale DB: {stale_db}")
        logger.info("All stage databases cleared — starting fresh")

    def run_step(self, step_name: str, script_path: Path, args: list = None) -> bool:
        """Run a pipeline step via subprocess"""
        logger.info(f"{'='*60}")
        logger.info(f"STEP: {step_name}")
        logger.info(f"{'='*60}")
        
        if not script_path.exists():
            logger.error(f"Script not found: {script_path}")
            self.stats["errors"].append(f"{step_name}: Script not found")
            return False
        
        cmd = [sys.executable, str(script_path)]
        if args:
            cmd.extend(args)
        
        logger.info(f"Running: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=str(script_path.parent),
                capture_output=False,  # Stream output to console
                text=True,
                env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
            )
            
            if result.returncode != 0:
                logger.error(f"{step_name} failed with exit code {result.returncode}")
                self.stats["errors"].append(f"{step_name}: Exit code {result.returncode}")
                return False
                
            logger.info(f"{step_name} completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"{step_name} failed with exception: {e}")
            self.stats["errors"].append(f"{step_name}: {str(e)}")
            return False
    
    def step1_scrape(self, skip: bool = False) -> bool:
        """Run scraping step"""
        output_db = SCRAPE_DIR / "jobs.db"
        
        if skip:
            logger.info("Skipping scrape step (--skip-scrape)")
            if not output_db.exists():
                logger.error(f"Cannot skip scrape: {output_db} does not exist")
                return False
            return True
        
        # Use scraper defaults (no country/job arguments)
        # Scraper will use DEFAULT_COUNTRIES_INDEED and DEFAULT_COUNTRIES_LINKEDIN
        args = []
        
        script = SCRAPE_DIR / "job_scraper.py"
        success = self.run_step("Scraping", script, args)
        
        if success and output_db.exists():
            # Count scraped jobs
            conn = sqlite3.connect(output_db)
            cursor = conn.execute("SELECT COUNT(*) FROM jobs")
            self.stats["jobs_scraped"] = cursor.fetchone()[0]
            conn.close()
            logger.info(f"Jobs scraped: {self.stats['jobs_scraped']}")
        
        return success
    
    def step2_preprocess(self) -> bool:
        """Run preprocessing step"""
        script = PREPROCESS_DIR / "preprocess.py"
        success = self.run_step("Preprocessing", script)
        
        output_db = PREPROCESS_DIR / "jobs_processed.db"
        if success and output_db.exists():
            conn = sqlite3.connect(output_db)
            cursor = conn.execute("SELECT COUNT(*) FROM jobs")
            self.stats["jobs_processed"] = cursor.fetchone()[0]
            cursor = conn.execute("SELECT SUM(has_url_duplicate) FROM jobs")
            self.stats["duplicates_detected"] = cursor.fetchone()[0] or 0
            conn.close()
            logger.info(f"Jobs processed: {self.stats['jobs_processed']}")
            logger.info(f"Duplicates detected: {self.stats['duplicates_detected']}")
        
        return success
    
    def step3_enrich(self) -> bool:
        """Run enrichment/standardization step"""
        script = ENRICHMENT_DIR / "taxonomy_standardization.py"
        success = self.run_step("Enrichment", script)
        
        output_db = ENRICHMENT_DIR / "jobs_enriched.db"
        if success and output_db.exists():
            conn = sqlite3.connect(output_db)
            cursor = conn.execute("SELECT COUNT(*) FROM jobs")
            self.stats["jobs_enriched"] = cursor.fetchone()[0]
            conn.close()
            logger.info(f"Jobs enriched: {self.stats['jobs_enriched']}")
        
        return success
    
    def step4_atomic_snapshot(self, local_mode: bool = False) -> bool:
        """Create atomic snapshot and upload to S3"""
        logger.info(f"{'='*60}")
        logger.info("STEP: Atomic Snapshot Generation")
        logger.info(f"{'='*60}")
        
        source_db = ENRICHMENT_DIR / "jobs_enriched.db"
        
        if not source_db.exists():
            logger.error(f"Source database not found: {source_db}")
            return False
        
        # Define output paths
        tmp_db = self.workdir / "jobs_enriched.tmp.db"
        final_db = self.workdir / "jobs_enriched.db"
        
        try:
            # Copy to tmp location
            logger.info(f"Copying {source_db} to {tmp_db}")
            shutil.copy2(source_db, tmp_db)
            
            # Run integrity check
            logger.info("Running SQLite integrity check...")
            result = subprocess.run(
                ["sqlite3", str(tmp_db), "PRAGMA integrity_check;"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0 or "ok" not in result.stdout.lower():
                logger.error(f"Integrity check failed: {result.stdout} {result.stderr}")
                self.stats["errors"].append("Integrity check failed")
                return False
            
            logger.info("Integrity check passed: OK")
            
            # Atomic rename
            logger.info(f"Renaming {tmp_db} to {final_db}")
            tmp_db.rename(final_db)
            
            # Set environment variable for Supabase seeding
            os.environ["JOB_DB_PATH"] = str(final_db)
            
            if local_mode:
                logger.info("Local mode: Skipping S3 upload")
                self.stats["s3_snapshot_path"] = str(final_db)
                return True
            
            # Upload to S3
            if not self.config["s3_bucket"]:
                logger.warning("S3_BUCKET not set, skipping S3 upload")
                return True
            
            return self._upload_to_s3(final_db)
            
        except Exception as e:
            logger.error(f"Snapshot generation failed: {e}")
            self.stats["errors"].append(f"Snapshot: {str(e)}")
            return False
    
    def _upload_to_s3(self, local_path: Path) -> bool:
        """Upload snapshot to S3 with versioning"""
        import boto3
        from botocore.exceptions import ClientError
        
        bucket = self.config["s3_bucket"]
        prefix = self.config["s3_prefix"]
        
        s3_client = boto3.client('s3', region_name=self.config["aws_region"])
        
        # Versioned path: s3://bucket/prefix/snapshots/run_id/jobs_enriched.db
        versioned_key = f"{prefix}/snapshots/{self.run_id}/jobs_enriched.db"
        
        # Latest path: s3://bucket/prefix/latest/jobs_enriched.db
        latest_key = f"{prefix}/latest/jobs_enriched.db"
        
        try:
            # Upload versioned snapshot
            logger.info(f"Uploading to s3://{bucket}/{versioned_key}")
            s3_client.upload_file(str(local_path), bucket, versioned_key)
            logger.info("Versioned snapshot uploaded successfully")
            
            # Upload to latest (only after versioned succeeds)
            logger.info(f"Updating s3://{bucket}/{latest_key}")
            s3_client.upload_file(str(local_path), bucket, latest_key)
            logger.info("Latest snapshot updated successfully")
            
            self.stats["s3_snapshot_path"] = f"s3://{bucket}/{versioned_key}"
            
            # Also upload a manifest file
            manifest = {
                "run_id": self.run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "snapshot_path": versioned_key,
                "jobs_count": self.stats["jobs_enriched"],
            }
            manifest_key = f"{prefix}/snapshots/{self.run_id}/manifest.json"
            
            import json
            s3_client.put_object(
                Bucket=bucket,
                Key=manifest_key,
                Body=json.dumps(manifest, indent=2),
                ContentType='application/json'
            )
            logger.info(f"Manifest uploaded to s3://{bucket}/{manifest_key}")
            
            return True
            
        except ClientError as e:
            logger.error(f"S3 upload failed: {e}")
            self.stats["errors"].append(f"S3 upload: {str(e)}")
            return False
    
    def step5_supabase_push(self, dry_run: bool = False) -> bool:
        """Push data to Supabase using REST API"""
        logger.info(f"{'='*60}")
        logger.info("STEP: Supabase Push (REST API)")
        logger.info(f"{'='*60}")
        
        if dry_run:
            logger.info("DRY RUN: Skipping Supabase push")
            return True
        
        if not self.config["supabase_url"]:
            logger.warning("Supabase URL not set, skipping Supabase push")
            return True
        
        # Set environment variables for to_supabase module
        os.environ["SUPABASE_URL"] = self.config["supabase_url"]
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = self.config["supabase_service_role_key"] or ""
        os.environ["CLEAR_SUPABASE"] = "true" if self.config["clear_supabase"] else "false"
        
        # Get database path
        db_path = Path(os.environ.get("JOB_DB_PATH", str(ENRICHMENT_DIR / "jobs_enriched.db")))
        
        logger.info(f"JOB_DB_PATH: {db_path}")
        logger.info(f"CLEAR_SUPABASE: {os.environ['CLEAR_SUPABASE']}")
        
        try:
            # Call Python Supabase push
            result: PushResult = push_jobs_to_supabase(db_path)
            
            # Update stats
            self.stats["supabase_upserted"] = result.jobs_inserted
            self.stats["supabase_skipped"] = result.jobs_skipped
            
            # Store pushed job_ids for downstream embedding step
            self._pushed_job_ids = result.pushed_job_ids
            
            if not result.success:
                for error in result.errors:
                    logger.error(f"Supabase push error: {error}")
                    self.stats["errors"].append(f"Supabase push: {error}")
                return False
            
            logger.info(f"Supabase push completed: {result.jobs_inserted} upserted, {result.jobs_skipped} skipped")
            return True
            
        except Exception as e:
            logger.error(f"Supabase push failed: {e}")
            self.stats["errors"].append(f"Supabase push: {str(e)}")
            return False
    
    def step6_deduplicate(self, dry_run: bool = False) -> bool:
        """Remove duplicate jobs from Supabase based on URL"""
        logger.info(f"{'='*60}")
        logger.info("STEP: Deduplication")
        logger.info(f"{'='*60}")
        
        if dry_run:
            logger.info("DRY RUN: Skipping deduplication")
            return True
        
        if not self.config["supabase_url"]:
            logger.warning("Supabase URL not set, skipping deduplication")
            return True
        
        # Import deduplication logic
        sys.path.insert(0, str(DEDUPLICATE_DIR))
        try:
            from deduplicate_supabase import deduplicate
            
            # Set environment variables
            os.environ["SUPABASE_URL"] = self.config["supabase_url"]
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = self.config["supabase_service_role_key"] or ""
            
            # Run deduplication for jobs table
            logger.info("Deduplicating jobs table...")
            jobs_removed = deduplicate("jobs")
            self.stats["duplicates_removed"] = jobs_removed
            
            # Run deduplication for job_details table
            logger.info("Deduplicating job_details table...")
            details_removed = deduplicate("job_details")
            self.stats["details_removed"] = details_removed
            
            # Run deduplication for job_chunks table
            logger.info("Deduplicating job_chunks table...")
            chunks_removed = deduplicate("job_chunks")
            self.stats["chunks_removed"] = chunks_removed
            
            total_removed = jobs_removed + details_removed + chunks_removed
            logger.info(
                f"Deduplication completed: {jobs_removed} jobs, {details_removed} details, "
                f"{chunks_removed} chunks removed (total: {total_removed})"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Deduplication failed: {e}")
            self.stats["errors"].append(f"Deduplication: {str(e)}")
            return False

    def step7_embed_new_jobs(self, dry_run: bool = False) -> bool:
        """Embed new job descriptions into job_chunks for semantic search."""
        logger.info(f"{'='*60}")
        logger.info("STEP: Embed New Jobs (Semantic Search)")
        logger.info(f"{'='*60}")

        if dry_run:
            logger.info("DRY RUN: Skipping embedding step")
            return True

        if not self.config["supabase_url"]:
            logger.warning("Supabase URL not set, skipping embedding step")
            return True

        try:
            # Import backfill module from same directory
            from orchestrate.backfill_embeddings import (
                backfill,
            )

            # Ensure env vars are set for the backfill module
            os.environ["SUPABASE_URL"] = self.config["supabase_url"]
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = self.config["supabase_service_role_key"] or ""

            # Pass pushed job_ids so backfill only embeds the new jobs
            pushed_ids = getattr(self, "_pushed_job_ids", None) or []
            if pushed_ids:
                logger.info("Embedding %d newly pushed job descriptions...", len(pushed_ids))
            else:
                logger.info("No pushed job_ids available — falling back to full scan...")

            backfill(
                batch_size=50,
                dry_run=False,
                job_ids=pushed_ids if pushed_ids else None,
            )

            logger.info("Embedding step completed successfully")
            return True

        except Exception as e:
            logger.error(f"Embedding step failed: {e}")
            self.stats["errors"].append(f"Embedding: {str(e)}")
            # Non-fatal: don't fail the whole pipeline for embedding issues
            logger.warning("Embedding failure is non-fatal — pipeline continues")
            return True
    
    def print_summary(self):
        """Print run summary"""
        self.stats["completed_at"] = datetime.now(timezone.utc).isoformat()
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("PIPELINE RUN SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Run ID:              {self.stats['run_id']}")
        logger.info(f"Started:             {self.stats['started_at']}")
        logger.info(f"Completed:           {self.stats['completed_at']}")
        logger.info("")
        logger.info(f"Jobs Scraped:        {self.stats['jobs_scraped']}")
        logger.info(f"Jobs Processed:      {self.stats['jobs_processed']}")
        logger.info(f"Jobs Enriched:       {self.stats['jobs_enriched']}")
        logger.info(f"Duplicates:          {self.stats['duplicates_detected']}")
        logger.info("")
        logger.info(f"S3 Snapshot:         {self.stats['s3_snapshot_path'] or 'Not uploaded'}")
        logger.info(f"Supabase Upserted:   {self.stats['supabase_upserted']}")
        logger.info(f"Supabase Skipped:    {self.stats['supabase_skipped']}")
        logger.info(
            f"Duplicates Removed:  {self.stats['duplicates_removed']} jobs, "
            f"{self.stats['details_removed']} details, "
            f"{self.stats['chunks_removed']} chunks"
        )
        logger.info("")
        
        if self.stats["errors"]:
            logger.warning("Errors encountered:")
            for error in self.stats["errors"]:
                logger.warning(f"  - {error}")
        else:
            logger.info("Status: SUCCESS ✓")
        
        logger.info("=" * 70)
        
        return len(self.stats["errors"]) == 0


def main():
    parser = argparse.ArgumentParser(
        description="Weekly Job Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Supabase push (still generates DB and uploads to S3)"
    )
    
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip scraping step (use existing jobs.db)"
    )
    
    parser.add_argument(
        "--local",
        action="store_true",
        help="Local mode: skip S3 upload"
    )
    
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Override run ID (default: auto-generated UTC timestamp)"
    )
    
    parser.add_argument(
        "--workdir",
        type=str,
        default=None,
        help=f"Override work directory (default: {DEFAULT_WORKDIR})"
    )
    
    args = parser.parse_args()
    
    # Override from environment
    if os.environ.get("DRY_RUN", "").lower() == "true":
        args.dry_run = True
    
    # Generate run ID
    run_id = args.run_id or get_run_id()
    
    # Set work directory
    workdir = Path(args.workdir) if args.workdir else DEFAULT_WORKDIR
    
    # Load config
    config = get_env_config()
    
    logger.info("=" * 70)
    logger.info("WEEKLY JOB PIPELINE ORCHESTRATOR")
    logger.info("=" * 70)
    logger.info(f"Run ID:      {run_id}")
    logger.info(f"Work Dir:    {workdir / run_id}")
    logger.info(f"Project:     {PROJECT_ROOT}")
    logger.info(f"Dry Run:     {args.dry_run}")
    logger.info(f"Skip Scrape: {args.skip_scrape}")
    logger.info(f"Local Mode:  {args.local}")
    logger.info(f"S3 Bucket:   {config['s3_bucket'] or 'Not set'}")
    logger.info(f"Supabase:    {'Set' if config['supabase_url'] else 'Not set'}")
    logger.info("=" * 70)
    
    # Create orchestrator
    orchestrator = PipelineOrchestrator(run_id, workdir, config)
    
    try:
        # Setup
        orchestrator.setup_workdir()
        
        # Clean all stage databases so each run is independent
        if not args.skip_scrape:
            orchestrator.ensure_clean_stage_dbs()
        
        # Run pipeline steps
        if not orchestrator.step1_scrape(skip=args.skip_scrape):
            logger.error("Pipeline failed at scraping step")
            orchestrator.print_summary()
            sys.exit(1)
        
        if not orchestrator.step2_preprocess():
            logger.error("Pipeline failed at preprocessing step")
            orchestrator.print_summary()
            sys.exit(1)
        
        if not orchestrator.step3_enrich():
            logger.error("Pipeline failed at enrichment step")
            orchestrator.print_summary()
            sys.exit(1)
        
        if not orchestrator.step4_atomic_snapshot(local_mode=args.local):
            logger.error("Pipeline failed at snapshot step")
            orchestrator.print_summary()
            sys.exit(1)
        
        if not orchestrator.step5_supabase_push(dry_run=args.dry_run):
            logger.error("Pipeline failed at Supabase push step")
            orchestrator.print_summary()
            sys.exit(1)
        
        if not orchestrator.step6_deduplicate(dry_run=args.dry_run):
            logger.error("Pipeline failed at deduplication step")
            orchestrator.print_summary()
            sys.exit(1)

        # Step 7 — Embed new job descriptions for semantic search
        orchestrator.step7_embed_new_jobs(dry_run=args.dry_run)
        
        # Print summary
        success = orchestrator.print_summary()
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user")
        orchestrator.print_summary()
        sys.exit(130)
    except Exception as e:
        logger.error(f"Pipeline failed with unexpected error: {e}")
        import traceback
        traceback.print_exc()
        orchestrator.print_summary()
        sys.exit(1)


if __name__ == "__main__":
    main()
