#!/usr/bin/env bash
# Runs ON YOUR MAC: tests → copy code to the server → deploy there.
#   ./deploy/release.sh
# Uses the same SSH key as the leasing server. Secrets (.env) are never copied from here.
set -Eeuo pipefail

HOST=${VALOR_HOST:-deploy@185.4.180.145}
DIR=/opt/valor
cd "$(dirname "$0")/.."

echo "== Tests"
.venv/bin/pytest -q

echo "== Copy code to $HOST:$DIR"
ssh "$HOST" "sudo mkdir -p $DIR && sudo chown deploy:deploy $DIR"
rsync -az --delete \
  --exclude .git --exclude .venv --exclude .env --exclude staticfiles --exclude '__pycache__' \
  --exclude .pytest_cache --exclude .claude \
  ./ "$HOST:$DIR/"

echo "== Deploy"
ssh "$HOST" "$DIR/deploy/deploy.sh"
