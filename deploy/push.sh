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
  # the server drains on SIGTERM (finishes the in-flight iteration, requeues the job, resumes from
  # cache on start); stop_grace_period in compose.yml gives it the time. Tell the operator what is running.
  running=$(curl -sS http://127.0.0.1:8765/api/jobs -H "Authorization: Bearer $(grep ^SA_TOKEN= /opt/silicon-agent/.env | cut -d= -f2-)" 2>/dev/null | python3 -c "import sys,json; print(\",\".join(j[\"id\"] for j in json.load(sys.stdin) if j[\"status\"] in (\"running\",\"queued\")))" 2>/dev/null || true)
  [ -n "$running" ] && echo "note: $running in flight -- will finish the current iteration, then resume after restart"
  docker compose --env-file /opt/silicon-agent/.env -f deploy/compose.yml up -d --force-recreate
  sleep 3
  docker compose --env-file /opt/silicon-agent/.env -f deploy/compose.yml ps
  curl -fsS http://127.0.0.1:8765/health && echo
  # every read-only endpoint the chat, the worktable and the agent tools depend on must answer
  tok=$(grep ^SA_TOKEN= /opt/silicon-agent/.env | cut -d= -f2-)
  for p in /api/jobs "/api/jobs?thread=" /api/threads /api/schema /api/specs /api/memory /api/bench "/api/pareto?design=fir&backend=ecp5" /api/calibration / /work; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $tok" "http://127.0.0.1:8765$p")
    [ "$code" = 200 ] || { echo "smoke: GET $p -> HTTP $code" >&2; fail=1; }
  done
  [ -z "${fail:-}" ] && echo "smoke: ok" || exit 1'
