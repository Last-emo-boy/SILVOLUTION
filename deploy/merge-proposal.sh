#!/usr/bin/env bash
# Merge a gated proposal into the checkout (run from the laptop):
#   deploy/merge-proposal.sh <proposal-id> [ssh-host]
# Fetches the diff the agent proposed from the host, applies it to the local checkout as a commit (author: the
# agent), records the merge on the server, and redeploys. Refuses a proposal whose gate did not pass.
set -euo pipefail
PID="${1:?proposal id}"; HOST="${2:-netcup}"
cd "$(dirname "$0")/.."
TOK=$(security find-generic-password -s silicon-agent-token -w 2>/dev/null || true)
status=$(curl -sS -H "Authorization: Bearer $TOK" "http://127.0.0.1:8765/api/proposals/$PID" | python3 -c "import sys,json; p=json.load(sys.stdin); print(p['status']); open('/tmp/sa-proposal.diff','w').write(p['diff']); open('/tmp/sa-proposal.title','w').write(p['title'] or 'generator change')")
[ "$status" = passed ] || { echo "proposal $PID is '$status', not passed" >&2; exit 1; }
git apply --check /tmp/sa-proposal.diff
git apply /tmp/sa-proposal.diff
git add -A
git commit -qm "$(cat /tmp/sa-proposal.title)

Proposed by the silicon-agent author session (proposal $PID), gated: emit/elaborate, behavioral sims, formal
equivalence of unchanged IRs, mutation score; merged by the operator.

Co-Authored-By: silicon-agent author <agent@silicon-agent>"
curl -sS -X POST -H "Authorization: Bearer $TOK" "http://127.0.0.1:8765/api/proposals/$PID/merge" >/dev/null
deploy/push.sh "$HOST"
