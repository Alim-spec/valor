#!/usr/bin/env bash
# Runs ON YOUR MAC:  ./deploy/release.sh
# tests → push to GitHub → the server pulls the same commit and deploys it.
# Secrets live only in /opt/valor/.env on the server.
set -Eeuo pipefail

HOST=${VALOR_HOST:-deploy@185.4.180.145}
cd "$(dirname "$0")/.."

[ -z "$(git status --porcelain)" ] || { echo "!! Uncommitted changes — commit first"; exit 1; }

echo "== Tests"
.venv/bin/pytest -q

echo "== Push to GitHub"
git push -q origin main
git log --oneline -1

echo "== Deploy on $HOST"
ssh "$HOST" /opt/valor/deploy/deploy.sh
