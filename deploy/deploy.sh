#!/usr/bin/env bash
# Manual deploy (counterpart to .github/workflows/deploy.yml): rsync the working
# tree to the server, rebuild + restart, health-check the TLS edge.
#
# Usage:
#   DEPLOY_HOST=203.0.113.7 ./deploy/deploy.sh      # host from env
#   ./deploy/deploy.sh 203.0.113.7                  # host as an arg
#   ./deploy/deploy.sh 203.0.113.7 --dry-run        # preview rsync, change nothing
#
# Optional overrides: DEPLOY_USER (default root), REMOTE_DIR (default /opt/codeclash).
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/opt/codeclash}"

# Target host: a non-flag positional arg wins, else $DEPLOY_HOST.
HOST="${DEPLOY_HOST:-}"
DRY=""
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY="--dry-run" ;;
    -*) echo "error: unknown flag: $arg" >&2; exit 2 ;;
    *)  HOST="$arg" ;;
  esac
done

if [ -z "$HOST" ]; then
  echo "error: no target host. Pass it as an arg or set DEPLOY_HOST." >&2
  echo "  e.g.  DEPLOY_HOST=203.0.113.7 $0     or     $0 203.0.113.7" >&2
  exit 2
fi

# Operate from the repo root so the rsync source tree is correct.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TARGET="${DEPLOY_USER}@${HOST}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10"

echo ">> Syncing $REPO_ROOT -> ${TARGET}:${REMOTE_DIR} ${DRY:+(dry-run)}"
# Never ship .env (server secrets) or TLS certs; --delete mirrors the tree.
rsync -az --delete --itemize-changes $DRY \
  -e "ssh $SSH_OPTS" \
  --exclude '.git' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude '.venv' --exclude 'venv' --exclude 'node_modules' \
  --exclude '.env' \
  --exclude '.claude' \
  --exclude 'deploy/nginx/certs' \
  --exclude 'deploy/letsencrypt' \
  ./ "${TARGET}:${REMOTE_DIR}/"

if [ -n "$DRY" ]; then
  echo ">> Dry-run only; nothing changed on the server."
  exit 0
fi

echo ">> Rebuilding + restarting the stack on ${HOST} (judge image is cached; app rebuilds if changed)…"
# shellcheck disable=SC2029  # we intentionally expand REMOTE_DIR locally
ssh $SSH_OPTS "$TARGET" \
  "cd ${REMOTE_DIR} && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"

echo ">> Health check (TLS edge, on the box; -k because the cert is for the public domain, not 127.0.0.1)…"
ssh $SSH_OPTS "$TARGET" 'for i in $(seq 1 20); do
  curl -fsk https://127.0.0.1/healthz && { echo; echo ">> healthy"; exit 0; }
  sleep 2
done; echo ">> health check never returned ok" >&2; exit 1'

echo ">> Deploy complete."
