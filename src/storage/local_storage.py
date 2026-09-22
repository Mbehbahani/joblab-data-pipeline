#!/usr/bin/env python3
"""
Local Filesystem Storage Adapter
================================
Replaces S3-based storage for self-hosted deployment.
Provides the same interface as S3JobTracker for deduplication tracking
and snapshot management.
"""

import os
import json
import sqlite3
import shutil
import logging
import time
from pathlib import Path
from typing import Set, Dict, Any, Optional, List
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class SnapshotManifest:
    """Manifest for a snapshot version"""
    run_id: str
    timestamp: str
    snapshot_path: str
    jobs_count: int
    description: str = ""


class LocalJobTracker:
    """
    Tracks yesterday's NEW job_ids using local filesystem.
    Drop-in replacement for S3JobTracker with identical interface.
    """
    
    def __init__(self, data_dir: str = "/opt/joblab/data"):
        self.data_dir = Path(data_dir)
        self.dedup_dir = self.data_dir / "dedup"
        self.dedup_dir.mkdir(parents=True, exist_ok=True)
        self.tracking_file = self.dedup_dir / "pushed_job_ids.json"
    
    def load_previous_job_ids(self) -> Set[str]:
        """Load yesterday's NEW job_ids from local file"""
        try:
            if self.tracking_file.exists():
                with open(self.tracking_file, 'r') as f:
                    data = json.load(f)
                job_ids = set(data.get("job_ids", []))
                logger.info(f"   ✓ Loaded {len(job_ids)} job_ids from yesterday's push")
                return job_ids
            else:
                logger.info("   ℹ️  No previous tracking file (first run or cleared)")
                return set()
        except Exception as e:
            logger.warning(f"   ⚠️  Could not load previous job_ids: {e}")
            return set()
    
    def save_new_job_ids(self, job_ids: Set[str]) -> bool:
        """Save today's NEW job_ids for tomorrow's comparison"""
        try:
            data = {
                "job_ids": list(job_ids),
                "count": len(job_ids),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "note": "Contains only NEW jobs from this run (not all historical jobs)"
            }
            # Atomic write: write to temp file then rename
            tmp_file = self.tracking_file.with_suffix('.tmp')
            with open(tmp_file, 'w') as f:
                json.dump(data, f, indent=2)
            tmp_file.rename(self.tracking_file)
            logger.info(f"   ✓ Saved {len(job_ids)} NEW job_ids for tomorrow's comparison")
            return True
        except Exception as e:
            logger.warning(f"   ⚠️  Could not save job_ids: {e}")
            return False
    
    def clear_tracking(self) -> bool:
        """Clear previous job tracking data"""
        try:
            if self.tracking_file.exists():
                self.tracking_file.unlink()
            logger.info("   ✓ Cleared previous job tracking data")
            return True
        except Exception as e:
            logger.warning(f"   ⚠️  Could not clear tracking: {e}")
            return False


class LocalSnapshotManager:
    """
    Manages versioned snapshots on local filesystem.
    Replaces S3 versioned snapshot storage.
    """
    
    def __init__(self, data_dir: str = "/opt/joblab/data"):
        self.data_dir = Path(data_dir)
        self.snapshots_dir = self.data_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.latest_db = self.snapshots_dir / "latest.db"
    
    def save_snapshot(self, source_db: Path, run_id: str, jobs_count: int) -> bool:
        """
        Save a versioned snapshot of the enriched database.
        Creates run_id directory with jobs_enriched.db and manifest.json.
        Updates 'latest' symlink to point to this run.
        """
        try:
            run_dir = self.snapshots_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy database to run directory
            dest_db = run_dir / "jobs_enriched.db"
            logger.info(f"Copying {source_db} to {dest_db}")
            shutil.copy2(source_db, dest_db)
            
            # Run integrity check
            connection = sqlite3.connect(dest_db)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()

            if integrity.lower() != "ok":
                logger.error("Integrity check failed: %s", integrity)
                return False
            
            logger.info("Integrity check passed: OK")
            
            # Create manifest
            manifest = SnapshotManifest(
                run_id=run_id,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                snapshot_path=str(dest_db),
                jobs_count=jobs_count,
                description=f"Snapshot from run {run_id}"
            )
            
            manifest_file = run_dir / "manifest.json"
            with open(manifest_file, 'w') as f:
                json.dump(asdict(manifest), f, indent=2)
            
            logger.info(f"Manifest saved to {manifest_file}")
            
            latest_tmp = self.snapshots_dir / "latest.tmp.db"
            shutil.copy2(dest_db, latest_tmp)
            latest_tmp.replace(self.latest_db)
            
            logger.info("Updated latest snapshot copy: %s", self.latest_db)
            return True
            
        except Exception as e:
            logger.error(f"Snapshot save failed: {e}")
            return False
    
    def get_latest_snapshot(self) -> Optional[Path]:
        """Get path to latest snapshot database"""
        return self.latest_db if self.latest_db.exists() else None
    
    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List all available snapshots with metadata"""
        snapshots = []
        for run_dir in sorted(self.snapshots_dir.iterdir()):
            if run_dir.is_dir() and run_dir.name != "latest":
                manifest_file = run_dir / "manifest.json"
                if manifest_file.exists():
                    with open(manifest_file, 'r') as f:
                        manifest = json.load(f)
                    snapshots.append(manifest)
        return snapshots
    
    def prune_old_snapshots(self, keep_last: int = 30) -> int:
        """
        Remove old snapshots, keeping only the most recent N.
        Returns number of snapshots removed.
        """
        snapshots = self.list_snapshots()
        if len(snapshots) <= keep_last:
            return 0
        
        # Sort by timestamp (oldest first)
        snapshots.sort(key=lambda x: x.get("timestamp", ""))
        to_remove = snapshots[:-keep_last]
        
        removed = 0
        for snap in to_remove:
            run_dir = self.snapshots_dir / snap["run_id"]
            if run_dir.exists():
                shutil.rmtree(run_dir)
                removed += 1
                logger.info(f"Pruned old snapshot: {snap['run_id']}")
        
        return removed


def get_storage_adapter(data_dir: str = "/opt/joblab/data"):
    """
    Factory function to get the appropriate storage adapter.
    For self-hosted, returns local filesystem adapters.
    """
    return {
        "job_tracker": LocalJobTracker(data_dir),
        "snapshot_manager": LocalSnapshotManager(data_dir),
    }