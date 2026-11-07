#!/usr/bin/env bash
# Daily database dump of Valor (cron on the server, see deploy/crontab.example) → /opt/backups/valor, 14 days.
set -Eeuo pipefail
cd /opt/valor
DEST=/opt/backups/valor
mkdir -p "$DEST"; chmod 700 "$DEST"; umask 077
get_env() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
STAMP=$(date +%Y-%m-%d_%H%M)
docker compose -f docker-compose.prod.yml exec -T valor-db \
  pg_dump -U "$(get_env POSTGRES_USER)" -d "$(get_env POSTGRES_DB)" -Fc > "$DEST/valor-db-$STAMP.dump"
find "$DEST" -maxdepth 1 -name 'valor-db-*.dump' -mtime +14 -delete
echo "$(date '+%F %T') valor backup: valor-db-$STAMP.dump ($(du -h "$DEST/valor-db-$STAMP.dump" | cut -f1))"
