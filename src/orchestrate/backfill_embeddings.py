#!/usr/bin/env python3
"""
Backfill Embeddings Script
===========================
Embeds job descriptions into the job_chunks table using
Bedrock Titan Text Embeddings V2 (512 dims).

Modes:
- **Pipeline mode** (called by run_weekly.py with job_ids):
  Only embeds the specific job_ids that were just pushed to Supabase.
  Fast — skips the expensive "fetch ALL job_details" step.

- **Standalone / backfill mode** (no job_ids):
  Fetches all job_details rows from Supabase, skips any job_id
  already in job_chunks, and embeds the remainders.

Logic:
1. Receive job_ids list (pipeline) or fetch all from Supabase (standalone).
2. Skip any job_id that already exists in job_chunks.
3. Chunk each job_description (~500-800 tokens approx).
4. Call Bedrock Titan embeddings for each chunk.
5. Insert into job_chunks table (with retry on transient errors).
6. Log progress throughout.

Environment Variables:
- SUPABASE_URL: Your Supabase project URL
- SUPABASE_SERVICE_ROLE_KEY: Service role key
- AWS_REGION: AWS region (default: us-east-1)
- BEDROCK_EMBED_MODEL_ID: Model ID (default: amazon.titan-embed-text-v2:0)
- EMBED_DIMENSION: Embedding dimension (default: 512)
- BATCH_SIZE: Number of rows to process per batch (default: 50)
- DRY_RUN: Skip actual inserts if 'true' (default: false)

Usage:
    python backfill_embeddings.py
    python backfill_embeddings.py --dry-run
    python backfill_embeddings.py --batch-size 25
"""

import os
import sys
import json
import time
import logging
import argparse
from typing import Any

import boto3
import requests
from botocore.exceptions import ClientError

# ── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────

EMBED_MODEL_ID = os.environ.get("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBED_DIMENSION = int(os.environ.get("EMBED_DIMENSION", "512"))
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DEFAULT_BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))

# Chunking parameters (approximate token counts via char heuristic)
CHUNK_MIN_CHARS = 200   # ~50 tokens
CHUNK_TARGET_CHARS = 2000  # ~500 tokens
CHUNK_MAX_CHARS = 3200  # ~800 tokens


def _headers() -> dict[str, str]:
    """Auth headers for Supabase REST (service_role)."""
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _base_url() -> str:
    return f"{SUPABASE_URL}/rest/v1"


# ── Bedrock Embedding Client ───────────────────────────────────────────────

_bedrock_client = None


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            service_name="bedrock-runtime",
            region_name=AWS_REGION,
        )
    return _bedrock_client


MAX_RETRIES = 5
BASE_BACKOFF = 2.0  # seconds


def embed_text(text: str) -> list[float]:
    """Generate embedding vector with exponential backoff on throttle."""
    client = _get_bedrock_client()
    body = {
        "inputText": text.strip(),
        "dimensions": EMBED_DIMENSION,
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = client.invoke_model(
                body=json.dumps(body),
                modelId=EMBED_MODEL_ID,
                accept="application/json",
                contentType="application/json",
            )
            response_body = json.loads(response["body"].read())
            return response_body["embedding"]
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ThrottlingException" and attempt < MAX_RETRIES - 1:
                wait = BASE_BACKOFF * (2 ** attempt)
                logger.warning(
                    "Throttled (attempt %d/%d), backing off %.1fs...",
                    attempt + 1, MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue
            logger.error("Bedrock embedding failed: %s", exc)
            raise


# ── Text Chunking ──────────────────────────────────────────────────────────


def chunk_text(text: str) -> list[str]:
    """
    Split text into chunks of approximately 500-800 tokens.
    Uses paragraph boundaries first, then sentence boundaries,
    then hard character limits as fallback.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    # If text is short enough, return as single chunk
    if len(text) <= CHUNK_MAX_CHARS:
        if len(text) >= CHUNK_MIN_CHARS:
            return [text]
        return []  # Too short to be meaningful

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= CHUNK_MAX_CHARS:
            if len(remaining) >= CHUNK_MIN_CHARS:
                chunks.append(remaining.strip())
            break

        # Try to break at paragraph boundary
        split_point = remaining.rfind("\n\n", CHUNK_MIN_CHARS, CHUNK_TARGET_CHARS)

        # Try sentence boundary
        if split_point == -1:
            for sep in [". ", ".\n", "! ", "? "]:
                split_point = remaining.rfind(sep, CHUNK_MIN_CHARS, CHUNK_TARGET_CHARS)
                if split_point != -1:
                    split_point += len(sep)
                    break

        # Hard break at target
        if split_point == -1:
            split_point = CHUNK_TARGET_CHARS

        chunk = remaining[:split_point].strip()
        if len(chunk) >= CHUNK_MIN_CHARS:
            chunks.append(chunk)
        remaining = remaining[split_point:].strip()

    return chunks


# ── Supabase Helpers ───────────────────────────────────────────────────────


def fetch_all_job_details() -> list[dict[str, Any]]:
    """Fetch all job_details rows (paginated). Used in standalone/backfill mode."""
    all_rows: list[dict[str, Any]] = []
    page_size = 1000
    offset = 0

    while True:
        url = f"{_base_url()}/job_details"
        params = {
            "select": "job_id,job_description",
            "limit": str(page_size),
            "offset": str(offset),
        }
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        all_rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    return all_rows


def fetch_job_details_by_ids(job_ids: list[str]) -> list[dict[str, Any]]:
    """
    Fetch job_details rows for a specific set of job_ids.
    Used in pipeline mode to avoid fetching ALL rows.
    Fetches in batches of 50 to avoid URL length limits.
    """
    all_rows: list[dict[str, Any]] = []
    batch_size = 50  # PostgREST URL length safety

    for i in range(0, len(job_ids), batch_size):
        batch_ids = job_ids[i : i + batch_size]
        # PostgREST 'in' filter: job_id=in.("id1","id2",...)
        quoted = ",".join(f'"{jid}"' for jid in batch_ids)
        url = f"{_base_url()}/job_details"
        params = {
            "select": "job_id,job_description",
            "job_id": f"in.({quoted})",
        }
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        page = resp.json()
        if page:
            all_rows.extend(page)

    return all_rows


def fetch_existing_chunk_job_ids() -> set[str]:
    """Fetch all job_ids already present in job_chunks."""
    job_ids: set[str] = set()
    page_size = 1000
    offset = 0

    while True:
        url = f"{_base_url()}/job_chunks"
        params = {
            "select": "job_id",
            "limit": str(page_size),
            "offset": str(offset),
        }
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        for row in page:
            job_ids.add(row["job_id"])
        if len(page) < page_size:
            break
        offset += page_size

    return job_ids


def insert_chunk(job_id: str, chunk_text: str, embedding: list[float], chunk_index: int) -> bool:
    """Insert a single chunk into job_chunks with retry on transient errors."""
    url = f"{_base_url()}/job_chunks"
    payload = {
        "job_id": job_id,
        "chunk_text": chunk_text,
        "chunk_index": chunk_index,
        "embedding": embedding,
    }

    for attempt in range(MAX_RETRIES):
        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return True

        # Retry on transient server errors (502, 503, 500, 429)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
            wait = BASE_BACKOFF * (2 ** attempt)
            logger.warning(
                "Transient %d inserting chunk job_id=%s chunk=%d (attempt %d/%d), retrying in %.1fs...",
                resp.status_code, job_id, chunk_index, attempt + 1, MAX_RETRIES, wait,
            )
            time.sleep(wait)
            continue

        # Non-retryable error or last attempt
        # Truncate HTML error bodies to avoid log spam
        error_text = resp.text
        if len(error_text) > 200:
            error_text = error_text[:200] + "... [truncated]"
        logger.error(
            "Failed to insert chunk for job_id=%s chunk=%d: %s %s",
            job_id, chunk_index, resp.status_code, error_text,
        )
        return False

    return False


# ── Main Backfill Logic ────────────────────────────────────────────────────


def backfill(batch_size: int = DEFAULT_BATCH_SIZE, dry_run: bool = False, job_ids: list[str] | None = None):
    """
    Run the embedding process.

    Args:
        batch_size: Number of jobs to process per batch.
        dry_run: If True, skip actual embedding and insertion.
        job_ids: Optional list of specific job_ids to embed.
                 If provided (pipeline mode), only these jobs are fetched
                 from job_details and embedded — much faster than scanning all.
                 If None (standalone/backfill mode), fetches ALL job_details.
    """
    overall_start = time.time()

    logger.info("=" * 70)
    logger.info("BACKFILL EMBEDDINGS")
    logger.info("=" * 70)
    logger.info("Model:      %s", EMBED_MODEL_ID)
    logger.info("Dimension:  %d", EMBED_DIMENSION)
    logger.info("Region:     %s", AWS_REGION)
    logger.info("Batch size: %d", batch_size)
    logger.info("Dry run:    %s", dry_run)
    if job_ids is not None:
        logger.info("Mode:       Pipeline (embedding %d specific job_ids)", len(job_ids))
    else:
        logger.info("Mode:       Standalone (scan all job_details)")
    logger.info("=" * 70)

    # Validate config
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    # Step 1 — Fetch job_details (mode-dependent)
    if job_ids is not None and len(job_ids) > 0:
        # Pipeline mode: fetch only the specific job_ids we just pushed
        logger.info("Fetching %d specific job_details from Supabase...", len(job_ids))
        job_details = fetch_job_details_by_ids(job_ids)
        logger.info("Found %d job_details rows (of %d requested)", len(job_details), len(job_ids))
    else:
        # Standalone/backfill mode: fetch everything
        logger.info("Fetching job_details from Supabase...")
        job_details = fetch_all_job_details()
        logger.info("Found %d job_details rows", len(job_details))

    if not job_details:
        logger.info("No job_details to process. Exiting.")
        return

    # Step 2 — Fetch existing chunk job_ids
    logger.info("Fetching existing job_chunks job_ids...")
    existing_ids = fetch_existing_chunk_job_ids()
    logger.info("Found %d job_ids already in job_chunks", len(existing_ids))

    # Step 3 — Filter to new jobs only
    new_jobs = [j for j in job_details if j["job_id"] not in existing_ids]
    logger.info("Jobs to embed: %d (skipping %d already embedded)", len(new_jobs), len(job_details) - len(new_jobs))

    if not new_jobs:
        logger.info("All jobs already embedded. Nothing to do.")
        return

    # Step 4 — Process in batches
    total_chunks_inserted = 0
    total_chunks_failed = 0
    total_jobs_processed = 0
    total_jobs_skipped_no_desc = 0

    for batch_start in range(0, len(new_jobs), batch_size):
        batch = new_jobs[batch_start : batch_start + batch_size]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (len(new_jobs) + batch_size - 1) // batch_size

        logger.info(
            "Processing batch %d/%d (%d jobs)...",
            batch_num, total_batches, len(batch),
        )
        batch_start_time = time.time()

        for job in batch:
            job_id = job["job_id"]
            description = job.get("job_description", "")

            if not description or not description.strip():
                total_jobs_skipped_no_desc += 1
                continue

            # Chunk the description
            chunks = chunk_text(description)
            if not chunks:
                total_jobs_skipped_no_desc += 1
                continue

            for idx, chunk in enumerate(chunks):
                if dry_run:
                    logger.info(
                        "[DRY RUN] Would embed job_id=%s chunk=%d len=%d",
                        job_id, idx, len(chunk),
                    )
                    total_chunks_inserted += 1
                    continue

                try:
                    embedding = embed_text(chunk)
                    success = insert_chunk(job_id, chunk, embedding, idx)
                    if success:
                        total_chunks_inserted += 1
                    else:
                        total_chunks_failed += 1
                except Exception as exc:
                    logger.error(
                        "Failed to embed job_id=%s chunk=%d: %s",
                        job_id, idx, exc,
                    )
                    total_chunks_failed += 1

                # Rate limiting — avoid throttling Bedrock
                time.sleep(0.5)

            total_jobs_processed += 1

        batch_elapsed = round(time.time() - batch_start_time, 2)
        logger.info(
            "Batch %d/%d completed in %.2fs",
            batch_num, total_batches, batch_elapsed,
        )

    # Summary
    total_elapsed = round(time.time() - overall_start, 2)
    logger.info("")
    logger.info("=" * 70)
    logger.info("BACKFILL SUMMARY")
    logger.info("=" * 70)
    logger.info("Total jobs processed:          %d", total_jobs_processed)
    logger.info("Total jobs skipped (no desc):  %d", total_jobs_skipped_no_desc)
    logger.info("Total chunks inserted:         %d", total_chunks_inserted)
    logger.info("Total chunks failed:           %d", total_chunks_failed)
    logger.info("Total time:                    %.2fs", total_elapsed)
    logger.info("=" * 70)


# ── CLI ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Backfill job_chunks embeddings from job_details",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip actual embedding and insertion (log only)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Jobs per batch (default: {DEFAULT_BATCH_SIZE})",
    )

    args = parser.parse_args()

    # Allow env override
    if os.environ.get("DRY_RUN", "").lower() == "true":
        args.dry_run = True

    backfill(batch_size=args.batch_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
