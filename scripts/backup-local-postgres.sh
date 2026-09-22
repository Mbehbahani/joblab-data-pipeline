#!/usr/bin/env bash
set -euo pipefail

backup_dir="${JOBLAB_BACKUP_DIR:-}"
if [[ -z "$backup_dir" || ! -d "$backup_dir" || ! -w "$backup_dir" ]]; then
  echo "JOBLAB_BACKUP_DIR must name an existing, writable external backup directory." >&2
  exit 2
fi
case "$(realpath "$backup_dir")/" in
  /opt/joblab/*)
    echo "Refusing backup destination inside /opt/joblab; use a separate disk or off-host mount." >&2
    exit 2
    ;;
esac

cd /opt/joblab/app
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$backup_dir/joblab-$stamp.dump"
temporary="$final.tmp"
trap 'rm -f "$temporary"' EXIT

docker compose exec -T postgres pg_dump --username=joblab --dbname=joblab --format=custom > "$temporary"
docker compose exec -T postgres pg_restore --list < "$temporary" > /dev/null
mv -- "$temporary" "$final"
find "$backup_dir" -maxdepth 1 -type f -name 'joblab-*.dump' -mtime +14 -delete
trap - EXIT
printf 'Backup created and archive-validated: %s\n' "$final"