#!/usr/bin/env python3
"""
Reclaim Supabase disk after a cleanup run
=========================================

Companion to `cleanup_supabase_storage.py`. Deleting rows does NOT shrink the
Postgres files - it only marks the space reusable - so the "Database size"
number on the Supabase usage page (pg_database_size) stays where it was.

VACUUM FULL rewrites each table with only its live rows and rebuilds its
indexes, which is what actually returns the space. It takes an exclusive lock
on each table for the duration (tens of seconds to a few minutes here), so run
it between weekly pipeline runs, not while one is in progress.

Example:
    python scripts/vacuum_supabase.py

Reference run (2026-09-11, after a --days 30 cleanup):
    job_chunks   385 MB -> 90 MB  (44s)
    job_details  100 MB -> 100 MB (23s)
    jobs          20 MB -> 19 MB  (2s)
    database     517 MB -> 220 MB
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

TABLES = ("public.job_chunks", "public.job_details", "public.jobs")


def load_connection_string() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
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


def main() -> int:
    conn = psycopg2.connect(load_connection_string(), connect_timeout=20)
    conn.autocommit = True  # VACUUM cannot run inside a transaction block
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 0")

    def db_size() -> str:
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        return cur.fetchone()[0]

    print("before:", db_size())
    for table in TABLES:
        t0 = time.time()
        cur.execute(f"VACUUM (FULL, ANALYZE) {table}")
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))", (table,))
        print(f"{table}: VACUUM FULL done in {time.time() - t0:.0f}s -> total size {cur.fetchone()[0]}")
    print("after:", db_size())
    print("\nNote: the Supabase usage/billing page samples this value on a schedule and can lag")
    print("by up to ~24h. Reports -> Database refreshes sooner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
