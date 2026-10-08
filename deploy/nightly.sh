#!/usr/bin/env bash
# Nightly regression on the EDA host: runs the open-source benchmark cases through the job server, stores the
# report under /srv/sa/bench, and leaves a one-line status in /srv/sa/bench/LAST for the chat/worktable to show.
#   crontab (root on netcup):  17 3 * * *  /opt/silicon-agent/app/deploy/nightly.sh >> /srv/sa/bench/nightly.log 2>&1
set -uo pipefail
cd /opt/silicon-agent/app
export SA_TOKEN="$(grep ^SA_TOKEN= /opt/silicon-agent/.env | cut -d= -f2-)"
export SA_SERVER=http://127.0.0.1:8765
mkdir -p /srv/sa/bench
echo "== nightly $(date -Is)"
if python3 -m silicon_agent.bench run --tag nightly --only ecp5,ice40,gowin --max-iter 6 --out /srv/sa/bench; then
  echo "nightly $(date -Is): OK" > /srv/sa/bench/LAST
else
  echo "nightly $(date -Is): REGRESSION (see $(ls -t /srv/sa/bench/nightly-*.json | head -1))" > /srv/sa/bench/LAST
fi
