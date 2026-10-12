#!/usr/bin/env bash
set -euo pipefail

PG_VERSION="${PG_VERSION:-17}"

read -r -s -p "Password per il ruolo Postgres 'timesheet': " PGPW
echo
if [ -z "$PGPW" ]; then
  echo "Password vuota: il container non partirebbe. Annullato." >&2
  exit 1
fi

docker run -d \
  --name tsc-postgres \
  --restart unless-stopped \
  -e POSTGRES_DB=timesheet \
  -e POSTGRES_USER=timesheet \
  -e POSTGRES_PASSWORD="$PGPW" \
  -p 5432:5432 \
  -v tsc-pg-data:/var/lib/postgresql/data \
  "postgres:${PG_VERSION}"

echo "Attendo che Postgres accetti connessioni..."
for i in $(seq 1 30); do
  if docker exec tsc-postgres pg_isready -U timesheet -d timesheet >/dev/null 2>&1; then
    echo "OK: Postgres pronto."
    docker ps --filter name=tsc-postgres --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    exit 0
  fi
  sleep 1
done

echo "Timeout dopo 30s. Log del container:" >&2
docker logs --tail 30 tsc-postgres >&2
exit 1
