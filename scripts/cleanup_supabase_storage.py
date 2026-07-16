#!/usr/bin/env python3
"""
Safe Supabase Storage Cleanup
=============================

This utility targets the storage-pressure patterns found in the JobLab
Supabase database:

1. Orphan `job_details` rows whose `job_id` no longer exists in `jobs`.
2. Orphan `job_chunks` rows whose `job_id` no longer exists in `jobs`.
3. Old `job_chunks` rows outside the live semantic/CV search window.

Safety model:
- Defaults to DRY RUN; no database changes unless --apply is passed.
- Any mutating run requires: --apply --confirm DELETE_SUPABASE_DATA
- Mutating runs archive candidate rows to local compressed JSONL before delete.
- URL-duplicate cleanup is intentionally NOT performed here because deleting
  `jobs` by URL can orphan `job_details` / `job_chunks` unless merged carefully.

Examples:
    # Read-only report only
    python scripts/cleanup_supabase_storage.py --days 30

    # Archive candidates locally, but do not delete anything
    python scripts/cleanup_supabase_storage.py --days 30 --archive-only

    # Archive then delete orphan details/chunks and old chunks
    python scripts/cleanup_supabase_storage.py --days 30 --apply --confirm DELETE_SUPABASE_DATA
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


CONFIRMATION_TEXT = "DELETE_SUPABASE_DATA"


@dataclass(frozen=True)
class CleanupTarget:
    name: str
    table_name: str
    description: str
    count_sql: str
    select_sql: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_connection_string() -> str:
    env_path = repo_root() / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    conn_str = (
        os.getenv("POSTGRES_URL_NON_POOLING")
        or os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRES_PRISMA_URL")
    )
    if not conn_str:
        raise RuntimeError(
            "No PostgreSQL connection string found. Expected one of: "
            "POSTGRES_URL_NON_POOLING, POSTGRES_URL, POSTGRES_PRISMA_URL"
        )
    return conn_str


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    # pgvector is commonly returned as a string by psycopg2. If a custom adapter
    # returns another unsupported object, preserve it as text rather than failing
    # midway through an archive.
    return str(value)


def build_targets(days: int, *, skip_orphans: bool, skip_old_chunks: bool) -> list[CleanupTarget]:
    targets: list[CleanupTarget] = []

    if not skip_orphans:
        targets.append(
            CleanupTarget(
                name="orphan_job_details",
                table_name="job_details",
                description="job_details rows with no matching jobs row",
                count_sql="""
                    SELECT count(*)::bigint
                    FROM public.job_details d
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.jobs j WHERE j.job_id = d.job_id
                    );
                """,
                select_sql="""
                    SELECT d.*
                    FROM public.job_details d
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.jobs j WHERE j.job_id = d.job_id
                    )
                    ORDER BY d.id;
                """,
            )
        )
        targets.append(
            CleanupTarget(
                name="orphan_job_chunks",
                table_name="job_chunks",
                description="job_chunks rows with no matching jobs row",
                count_sql="""
                    SELECT count(*)::bigint
                    FROM public.job_chunks c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.jobs j WHERE j.job_id = c.job_id
                    );
                """,
                select_sql="""
                    SELECT c.*
                    FROM public.job_chunks c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.jobs j WHERE j.job_id = c.job_id
                    )
                    ORDER BY c.id;
                """,
            )
        )

    if not skip_old_chunks:
        targets.append(
            CleanupTarget(
                name=f"old_job_chunks_older_than_{days}d",
                table_name="job_chunks",
                description=(
                    "job_chunks linked to jobs whose parseable posted_date is "
                    f"older than {days} days"
                ),
                count_sql=f"""
                    SELECT count(*)::bigint
                    FROM public.job_chunks c
                    JOIN public.jobs j ON j.job_id = c.job_id
                    WHERE j.posted_date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}'
                      AND NULLIF(j.posted_date, '')::date < current_date - interval '{days} days';
                """,
                select_sql=f"""
                    SELECT c.*
                    FROM public.job_chunks c
                    JOIN public.jobs j ON j.job_id = c.job_id
                    WHERE j.posted_date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}'
                      AND NULLIF(j.posted_date, '')::date < current_date - interval '{days} days'
                    ORDER BY c.id;
                """,
            )
        )

    return targets


def fetch_scalar(cur: psycopg2.extensions.cursor, sql: str) -> int:
    cur.execute(sql)
    value = cur.fetchone()[0]
    return int(value or 0)


def print_database_summary(cur: psycopg2.extensions.cursor, days: int) -> None:
    print("\n## Database summary")
    cur.execute(
        """
        SELECT 'jobs' AS table_name, count(*)::bigint AS rows FROM public.jobs
        UNION ALL SELECT 'job_details', count(*)::bigint FROM public.job_details
        UNION ALL SELECT 'job_chunks', count(*)::bigint FROM public.job_chunks;
        """
    )
    for row in cur.fetchall():
        print(f"- {row[0]}: {row[1]:,} rows")

    cur.execute(
        """
        SELECT
          (SELECT pg_size_pretty(pg_total_relation_size('public.jobs'))) AS jobs_size,
          (SELECT pg_size_pretty(pg_total_relation_size('public.job_details'))) AS job_details_size,
          (SELECT pg_size_pretty(pg_total_relation_size('public.job_chunks'))) AS job_chunks_size,
          (SELECT pg_size_pretty(pg_relation_size('public.job_chunks_embedding_idx'))) AS job_chunks_embedding_idx_size;
        """
    )
    sizes = cur.fetchone()
    print(f"- jobs size: {sizes[0]}")
    print(f"- job_details size: {sizes[1]}")
    print(f"- job_chunks size: {sizes[2]}")
    print(f"- job_chunks_embedding_idx size: {sizes[3]}")

    cur.execute(
        f"""
        SELECT
          count(*) FILTER (
            WHERE j.posted_date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}'
              AND NULLIF(j.posted_date, '')::date >= current_date - interval '{days} days'
          )::bigint AS chunks_inside_window,
          count(*) FILTER (
            WHERE j.posted_date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}'
              AND NULLIF(j.posted_date, '')::date < current_date - interval '{days} days'
          )::bigint AS chunks_outside_window,
          count(*) FILTER (WHERE j.job_id IS NULL)::bigint AS chunks_without_jobs
        FROM public.job_chunks c
        LEFT JOIN public.jobs j ON j.job_id = c.job_id;
        """
    )
    recency = cur.fetchone()
    print(f"- job_chunks inside {days}d live window: {recency[0]:,}")
    print(f"- job_chunks outside {days}d live window: {recency[1]:,}")
    print(f"- job_chunks without jobs row: {recency[2]:,}")


def archive_target(
    conn: psycopg2.extensions.connection,
    target: CleanupTarget,
    archive_dir: Path,
    batch_size: int,
) -> tuple[Path, list[int], int]:
    """Archive candidate rows and return (path, ids, row_count)."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{target.name}.jsonl.gz"
    ids: list[int] = []
    row_count = 0

    with conn.cursor(name=f"archive_{target.name}", cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.itersize = batch_size
        cur.execute(target.select_sql)
        with gzip.open(archive_path, "wt", encoding="utf-8") as fh:
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    data = dict(row)
                    row_id = data.get("id")
                    if row_id is not None:
                        ids.append(int(row_id))
                    fh.write(json.dumps(data, default=json_default, ensure_ascii=False) + "\n")
                    row_count += 1

    return archive_path, ids, row_count


def delete_ids(
    conn: psycopg2.extensions.connection,
    table_name: str,
    ids: Iterable[int],
    batch_size: int,
) -> int:
    ids_list = list(ids)
    deleted = 0
    with conn.cursor() as cur:
        for start in range(0, len(ids_list), batch_size):
            batch = ids_list[start : start + batch_size]
            cur.execute(
                f"DELETE FROM public.{table_name} WHERE id = ANY(%s);",
                (batch,),
            )
            deleted += cur.rowcount
    return deleted


def write_manifest(archive_dir: Path, manifest: dict[str, Any]) -> Path:
    path = archive_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=json_default), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely archive/delete Supabase storage cleanup candidates.")
    parser.add_argument("--days", type=int, default=30, help="Live window for job_chunks; older chunks are cleanup candidates. Default: 30")
    parser.add_argument("--batch-size", type=int, default=1000, help="Archive/delete batch size. Default: 1000")
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=repo_root() / "archives" / "supabase_cleanup",
        help="Base directory for compressed JSONL archives.",
    )
    parser.add_argument("--archive-only", action="store_true", help="Archive candidates locally but do not delete from Supabase.")
    parser.add_argument("--apply", action="store_true", help="Archive then delete candidates from Supabase.")
    parser.add_argument("--confirm", default="", help=f"Required with --apply. Must equal {CONFIRMATION_TEXT!r}.")
    parser.add_argument("--skip-orphans", action="store_true", help="Do not target orphan job_details/job_chunks.")
    parser.add_argument("--skip-old-chunks", action="store_true", help="Do not target old job_chunks outside --days window.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.days < 1:
        raise SystemExit("--days must be >= 1")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
    if args.apply and args.confirm != CONFIRMATION_TEXT:
        raise SystemExit(
            f"Refusing to mutate database. Re-run with: --apply --confirm {CONFIRMATION_TEXT}"
        )
    if args.apply and args.archive_only:
        raise SystemExit("Choose either --apply or --archive-only, not both.")

    mode = "APPLY" if args.apply else "ARCHIVE_ONLY" if args.archive_only else "DRY_RUN"
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_dir = args.archive_dir / run_id
    targets = build_targets(args.days, skip_orphans=args.skip_orphans, skip_old_chunks=args.skip_old_chunks)
    if not targets:
        raise SystemExit("No cleanup targets selected.")

    print("=" * 72)
    print("SAFE SUPABASE STORAGE CLEANUP")
    print("=" * 72)
    print(f"Mode:       {mode}")
    print(f"Days:       {args.days}")
    print(f"Archive:    {archive_dir}")
    print(f"Batch size: {args.batch_size}")

    conn_str = load_connection_string()

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "days": args.days,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "targets": [],
    }

    with psycopg2.connect(conn_str, connect_timeout=20) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY;" if mode == "DRY_RUN" else "BEGIN;")
            print_database_summary(cur, args.days)

            print("\n## Cleanup candidates")
            total_candidates = 0
            counts: dict[str, int] = {}
            for target in targets:
                count = fetch_scalar(cur, target.count_sql)
                counts[target.name] = count
                total_candidates += count
                print(f"- {target.name}: {count:,} rows ({target.description})")

            if mode == "DRY_RUN":
                cur.execute("ROLLBACK;")
                print("\nDRY RUN complete: no archive written and no database rows deleted.")
                print("To archive candidates without deleting, run with --archive-only.")
                print(f"To archive and delete, run with --apply --confirm {CONFIRMATION_TEXT}.")
                return 0

        # Server-side cursors require transaction scope. Archive candidates before
        # deleting anything. If archive fails, no DELETE is attempted.
        print("\n## Archiving candidates")
        archived_ids_by_target: dict[str, list[int]] = {}
        for target in targets:
            expected = counts[target.name]
            archive_path, ids, archived_count = archive_target(conn, target, archive_dir, args.batch_size)
            if archived_count != expected:
                conn.rollback()
                raise RuntimeError(
                    f"Archive count mismatch for {target.name}: expected {expected}, archived {archived_count}. "
                    "Rolled back without deleting."
                )
            archived_ids_by_target[target.name] = ids
            manifest["targets"].append(
                {
                    "name": target.name,
                    "table_name": target.table_name,
                    "description": target.description,
                    "rows_archived": archived_count,
                    "archive_path": str(archive_path),
                }
            )
            print(f"- {target.name}: archived {archived_count:,} rows -> {archive_path}")

        manifest_path = write_manifest(archive_dir, manifest)
        print(f"- manifest: {manifest_path}")

        if mode == "ARCHIVE_ONLY":
            conn.rollback()
            print("\nARCHIVE ONLY complete: archive files written; no database rows deleted.")
            return 0

        print("\n## Deleting archived candidates")
        total_deleted = 0
        for target in targets:
            ids = archived_ids_by_target[target.name]
            deleted = delete_ids(conn, target.table_name, ids, args.batch_size)
            total_deleted += deleted
            print(f"- {target.name}: deleted {deleted:,} rows from public.{target.table_name}")

        conn.commit()
        print(f"\nAPPLY complete: deleted {total_deleted:,} archived rows.")
        print("\nImportant: PostgreSQL may not immediately return storage to Supabase quota.")
        print("After verifying the app, run maintenance from Supabase SQL Editor if needed:")
        print("  REINDEX INDEX CONCURRENTLY public.job_chunks_embedding_idx;")
        print("  VACUUM (ANALYZE) public.job_chunks;")
        print("  VACUUM (ANALYZE) public.job_details;")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)