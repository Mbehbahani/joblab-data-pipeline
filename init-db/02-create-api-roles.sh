#!/bin/sh
set -eu

: "${POSTGREST_DB_PASSWORD:?POSTGREST_DB_PASSWORD is required}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=api_password="$POSTGREST_DB_PASSWORD" <<'SQL'
CREATE ROLE joblab_authenticator LOGIN NOINHERIT PASSWORD :'api_password';
CREATE ROLE joblab_readonly NOLOGIN;
CREATE ROLE joblab_writer NOLOGIN;
GRANT joblab_readonly, joblab_writer TO joblab_authenticator;
SQL