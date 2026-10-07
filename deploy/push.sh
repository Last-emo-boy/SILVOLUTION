#!/usr/bin/env bash
# Deploy the silicon-agent server to the EDA host (run from the laptop).
#   deploy/push.sh [ssh-host]      default host: netcup
# Syncs the app code to /opt/silicon-agent/app, keeps tools/ and data where they are,
# and restarts the container. First run needs /opt/silicon-agent/.env on the host
# (SA_TOKEN=..., DEEPSEEK_API_KEY=...), outside the synced tree.
set -euo pipefail
HOST="${1:-netcup}"
cd "$(dirname "$0")/.."
rsync -az --delete \
  --exclude tools/ --exclude cache/ --exclude runs/ --exclude results/ --exclude '__pycache__/' --exclude .git/ \
  ./ "$HOST:/opt/silicon-agent/app/"
ssh "$HOST" 'set -e
  mkdir -p /srv/sa/home && chown -R 10001:10001 /srv/sa
  cd /opt/silicon-agent/app
  if [ ! -f /opt/silicon-agent/.env ]; then
    echo "missing /opt/silicon-agent/.env on the host: SA_TOKEN=... DEEPSEEK_API_KEY=..." >&2; exit 1
  fi
  docker compose --env-file /opt/silicon-agent/.env -f deploy/compose.yml up -d --force-recreate
  sleep 3
  docker compose --env-file /opt/silicon-agent/.env -f deploy/compose.yml ps
  curl -fsS http://127.0.0.1:8765/health && echo'
