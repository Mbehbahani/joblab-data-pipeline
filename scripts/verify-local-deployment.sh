#!/usr/bin/env bash
set -euo pipefail

compose_dir="${JOBLAB_APP_DIR:-/opt/joblab/app}"
scraper_env="${JOBLAB_SCRAPER_ENV:-/opt/joblab/scraper.env}"
api_url="${LOCAL_POSTGREST_URL:-http://127.0.0.1:3000}"

if [[ ! -r "$scraper_env" ]]; then
  echo "Cannot read scraper environment file: $scraper_env" >&2
  exit 2
fi
set -a
# shellcheck disable=SC1090
source "$scraper_env"
set +a
: "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY is required}"

cd "$compose_dir"
docker compose exec -T postgres pg_isready --username=joblab --dbname=joblab
vector_enabled="$(docker compose exec -T postgres psql -U joblab -d joblab -Atqc \
  "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")"
if [[ "$vector_enabled" != "t" ]]; then
  echo "pgvector extension is not enabled." >&2
  exit 1
fi

jobs_count="$(docker compose exec -T postgres psql -U joblab -d joblab -Atqc \
  "SELECT count(*) FROM public.jobs")"
curl --fail --silent --show-error \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  "$api_url/rest/v1/jobs?select=job_id&limit=1" > /dev/null

printf 'PostgreSQL and pgvector: healthy\n'
printf 'REST API authentication: healthy\n'
printf 'Measured public.jobs rows: %s\n' "$jobs_count"