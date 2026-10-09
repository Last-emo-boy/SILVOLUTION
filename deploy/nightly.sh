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
status=OK
python3 -m silicon_agent.bench run --tag nightly --only ecp5,ice40,gowin,sky130 --max-iter 6 --out /srv/sa/bench || status=REGRESSION
# the same four cases driven through the chat: measures the harness agent (interpret -> run -> report) end to end
python3 -m silicon_agent.bench run --tag agent --agent --only fir-ecp5-120-6dsp,conv3-ice40-60,matmul-gowin-120,cordic-ecp5-200 --max-iter 6 --out /srv/sa/bench || status=REGRESSION
# planner A/B: the same three cases under the stronger model (default planner is deepseek-flash); compared in /bench
python3 -m silicon_agent.bench run --tag planner-pro --planner deepseek-v4-pro --only fir-ecp5-120-6dsp,conv3-ice40-60,cordic-ecp5-200 --max-iter 6 --out /srv/sa/bench || true
if [ "$status" = OK ]; then
  echo "nightly $(date -Is): OK" > /srv/sa/bench/LAST
else
  echo "nightly $(date -Is): REGRESSION (see $(ls -t /srv/sa/bench/*.json | head -1))" > /srv/sa/bench/LAST
fi
