#!/bin/sh
# Run from cron on the host, for example every night at 3:
#   0 3 * * * /path/to/production/backup.sh >> /path/to/production/backups/backup.log 2>&1
# GOOGLE_TOKEN_KEY must never end up next to these files.
set -eu

cd "$(dirname "$0")"
mkdir -p backups
chmod 700 backups

file="backups/tsc-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T tsc-db sh -c 'PGPASSWORD="$DB_PASSWORD" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc' > "$file.tmp"
mv "$file.tmp" "$file"

docker compose exec -T tsc python manage.py clearsessions
find backups -name 'tsc-*.dump' -mtime +14 -delete
