#!/usr/bin/env bash
set -euo pipefail

cd "${JOBLAB_APP_DIR:-/opt/joblab/app}"
docker compose exec -T postgres psql --username=joblab --dbname=joblab \
  < deployment/postgres/grant-api-roles.sql
docker compose restart postgrest
printf 'Read-only and writer API grants applied.\n'
