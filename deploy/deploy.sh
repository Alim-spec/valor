#!/usr/bin/env bash
# Runs ON THE SERVER: /opt/valor/deploy/deploy.sh
# git pull (GitHub Alim-spec/valor, read-only deploy key) → build → migrations →
# demo data (first run only) → web → health check.
set -Eeuo pipefail

# All code is inside functions: bash reads them before running, so a git pull that changes
# this file cannot swap commands mid-run.

cd /opt/valor
COMPOSE=(docker compose -f docker-compose.prod.yml)

pull() {
  echo "== 0. Code from GitHub"
  git pull --ff-only
  git log --oneline -1
  exec env DEPLOY_PULLED=1 "$0" "$@"
}

main() {

[ -f .env ] || { echo "!! /opt/valor/.env is missing (copy deploy/env.prod.example and fill it)"; exit 1; }
docker network inspect edge >/dev/null 2>&1 || docker network create edge >/dev/null

echo "== 1. Build"
"${COMPOSE[@]}" build valor-web

echo "== 2. Database and migrations"
"${COMPOSE[@]}" up -d valor-db
"${COMPOSE[@]}" run --rm --no-deps valor-web python manage.py migrate --noinput

echo "== 3. Demo data (only if the database is empty)"
"${COMPOSE[@]}" run --rm --no-deps valor-web python manage.py seed_demo

echo "== 4. Web"
"${COMPOSE[@]}" up -d valor-web
for i in $(seq 1 45); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$("${COMPOSE[@]}" ps -q valor-web)")
  [ "$state" = healthy ] && break
  if [ "$state" = unhealthy ] || [ "$i" = 45 ]; then
    echo "!! valor-web did not become healthy ($state)"; "${COMPOSE[@]}" logs --tail 50 valor-web; exit 1
  fi
  sleep 2
done

echo "== 5. State"
"${COMPOSE[@]}" ps
docker image prune -f >/dev/null
echo "== Done"
}

if [ -z "${DEPLOY_PULLED:-}" ]; then
  pull "$@"
else
  main "$@"
fi
exit
