# Self-hosted Ubuntu deployment plan (JobLab: scrape + dashboard, no AI)

Status: **plan only, not implemented.** Companion frontend repo: `job-analytics-frontend2`.

## Scope

- Move the scraper (this repo) and the dashboard (`job-analytics-frontend2`) off AWS onto a single
  Ubuntu host.
- Drop the AI layer entirely: `lambda_backend`, Bedrock, semantic chat/CV-match are **not** deployed.
- Keep the embeddings column populated in case AI search comes back later, but nothing queries it.
  How embeddings get generated (Bedrock vs. local model) is out of scope for this plan — leave that
  step as-is for now.
- Access via Cloudflare Tunnel (`cloudflared`) → gives a domain + HTTPS without opening inbound ports.
- Dual-run with the existing AWS Fargate + Vercel deployment during verification, then decommission AWS.

## Key decision: plain Postgres/pgvector instead of full self-hosted Supabase

The full Supabase self-host stack (Postgres + Kong + GoTrue + PostgREST + Realtime + Storage + Studio)
exists to serve a multi-tenant app with auth and a REST/Realtime API layer. Nothing in this pipeline
needs any of that:

- The scraper only ever talks to Postgres directly (`psycopg2` / `POSTGRES_URL_NON_POOLING`) or via the
  Supabase REST client for upserts — both can point at a plain Postgres instance.
- The frontend's Supabase client calls can be swapped for direct Postgres queries (a thin API route)
  or, if REST-style access is still wanted, `PostgREST` alone (no Kong/GoTrue/Realtime) is enough — a
  single extra lightweight container, not the whole stack.

**Recommendation:** run a single `postgres:16` (or `pgvector/pgvector:pg16`) container with the
`vector` extension enabled. This is the same Postgres the cloud Supabase project already runs on top
of — the pipeline's SQL, schema, and embedding column all carry over unchanged. This uses meaningfully
less RAM than the 6-container Supabase stack and removes GoTrue/Realtime/Storage that this project
never uses.

If a REST layer still turns out to be convenient for the frontend, add `PostgREST` alone as a second
container later — it's cheap and decoupled from this decision.

## Key decision: local storage instead of S3

Today S3 is used for two things (`src/export/to_supabase.py`, `src/orchestrate/run_weekly.py`):

1. **Dedup tracking** — a small JSON/text object of previously-pushed job IDs, read/written each run.
2. **Snapshot storage** — a versioned copy of `jobs_enriched.db` (SQLite) per run, plus a manifest.

Both move to a local, Docker-managed volume — no object storage needed for a single-host deployment:

- `dedup/pushed_ids.json` (or equivalent) on a bind-mounted host path, e.g. `/opt/joblab/data/dedup/`.
- `snapshots/<run_id>/jobs_enriched.db` + `snapshots/<run_id>/manifest.json` on
  `/opt/joblab/data/snapshots/`, with a `latest/` symlink or copy mirroring today's "latest" S3 key.
- Retention: a simple cron/cleanup step (e.g. keep last 30 run snapshots) replaces S3 lifecycle rules,
  since local disk isn't infinite the way a bucket is.

This removes the `boto3` S3 client calls in both files in favor of a small local-filesystem storage
adapter with the same interface (`read`, `write`, `delete`, `list_versions`) — same call sites, new
backend. `backfill_embeddings.py`'s Bedrock client is a separate concern (embeddings model), addressed
below.

## Key decision: how the daily schedule works

There is no AWS EventBridge on a local host, so the equivalent of `cron(0 2 * * ? *)` becomes a
**systemd timer** (preferred over plain cron: gives logging via `journalctl`, retry/`OnFailure=`
hooks, and `systemctl status` visibility):

- `joblab-scraper.service` — a oneshot unit that runs
  `docker run --rm --env-file /opt/joblab/scraper.env joblab-pipeline` (the same image already built
  by this repo's `Dockerfile`, same `CMD` entrypoint `src/orchestrate/run_weekly.py`).
- `joblab-scraper.timer` — `OnCalendar=*-*-* 02:00:00` (matching today's 02:00 UTC EventBridge cron),
  `Persistent=true` so a missed run (host down at 02:00) fires on next boot instead of silently
  skipping a day.
- `systemctl enable --now joblab-scraper.timer` registers it; `systemctl list-timers` shows next run
  time; `journalctl -u joblab-scraper.service` shows run logs — replacing CloudWatch Logs.

This is the direct local equivalent of "EventBridge Scheduler triggers a Fargate task once a day" —
same trigger semantics, no cloud scheduler needed.

## Embeddings model note

`backfill_embeddings.py` currently calls Bedrock (Titan) for embeddings. Out of scope for now — leave
it calling Bedrock as-is; this is not a blocker for the rest of the migration and can be revisited
later if/when it matters.

## Phases

### Phase 0 — Host prep
1. Confirm Ubuntu version, RAM/disk. A single Postgres+pgvector container plus the scraper and
   frontend containers is materially lighter than the 6-service Supabase stack — plan for it, but
   headroom still matters for Postgres itself as data grows.
2. Install Docker + Docker Compose.
3. Install `cloudflared`, authenticate, create a named tunnel, pick a hostname for the dashboard
   (e.g. `jobs.yourdomain.com`). Decide separately whether a DB-admin surface (e.g. `pgAdmin` or
   `PostgREST`) is tunneled at all, or stays loopback/SSH-tunnel-only.
4. Directory layout, e.g.:
   ```
   /opt/joblab/
     postgres/          # Postgres data volume
     data/dedup/         # dedup tracking (was S3)
     data/snapshots/      # versioned run snapshots (was S3)
     scraper.env
     frontend.env
   ```

### Phase 1 — Local Postgres + pgvector
1. Run `pgvector/pgvector:pg16` (or `postgres:16` + manually installed `vector` extension) via Docker
   Compose, with a bind-mounted data directory for durability.
2. `CREATE EXTENSION IF NOT EXISTS vector;` on first boot.
3. `pg_dump` the cloud Supabase Postgres (`db.qsjxxswsrykrrrnrpdaz.supabase.co`) schema + data, restore
   into the new local instance. Verify row counts match (jobs table, taxonomy tables, embeddings
   column) before treating it as source of truth.
4. Generate a new local-only Postgres password/connection string — cloud credentials don't transfer
   and shouldn't be reused.

### Phase 2 — Scraper (this repo)
1. New branch `self-hosted` (not a new folder — see prior decision). Existing `main` keeps documenting
   the AWS/Terraform/Fargate deployment as-is.
2. Point `SUPABASE_URL`-equivalent config at the local Postgres connection string instead (or keep the
   same env var names pointed at a local `PostgREST` if that's added later — decide at implementation
   time based on how much of `to_supabase.py`'s REST-upsert logic needs preserving verbatim vs.
   rewriting as direct SQL).
3. Replace the `boto3` S3 calls in `to_supabase.py` (dedup tracking) and `run_weekly.py` (snapshots)
   with the local-filesystem adapter described above.
4. Add `joblab-scraper.service` + `joblab-scraper.timer` systemd units (Phase 0/"schedule" section)
   in place of EventBridge; `docker build` produces the same image already defined by the existing
   `Dockerfile` — no changes needed there.

### Phase 3 — Frontend (`job-analytics-frontend2`)
1. New branch `self-hosted` there too.
2. Point Supabase client config at the local Postgres/PostgREST endpoint (exact shape depends on the
   Phase 2 REST-vs-direct-SQL decision — the frontend currently expects a Supabase-style REST API, so
   if going fully "plain Postgres, no REST layer," this repo needs a thin internal API route added).
3. Remove/hide the AI chat panel, `AiInsightPanel`, `CVMatcher`; leave `LLM_BACKEND_URL` unset so those
   routes are simply never called.
4. Run under `systemd` (or `pm2`) behind the same Cloudflare Tunnel hostname.

### Phase 4 — Verification (dual-run window)
1. Compare daily job counts between AWS pipeline and local pipeline for several days.
2. Compare dashboard output (charts/filters/KPIs) against the same underlying data.
3. Confirm `joblab-scraper.timer` survives a host reboot (`systemctl is-enabled`).
4. Set up a local backup routine (scheduled `pg_dump` to a separate disk/off-host location) — there is
   no managed-Supabase safety net once this is the source of truth.


## Open items to resolve during implementation (not decided by this plan)

- Whether the frontend needs a REST layer (`PostgREST`) or a thin custom API route over direct SQL.
- Exact local-disk retention policy for snapshots (how many runs to keep before pruning).
- Backup destination for local Postgres (external drive, off-host rsync, etc.).
