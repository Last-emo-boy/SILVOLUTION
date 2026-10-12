#!/usr/bin/env bash
# Entry point of the agh container (open-source compose): run the Agnes Harness daemon in the foreground with
# its home on the shared data volume, so the job server (another container on the same volume) reaches its unix
# socket; provision the DeepSeek account and the silicon-agent plugin once the daemon listens. The DeepSeek key
# is handed to the harness's credential store once and never written anywhere else.
set -euo pipefail
: "${AGH_HOME:=/data/agh}"
: "${AGNES_PROFILE:=local-dev}"
: "${AGH_DIST:=/opt/agnes-harness/packages/cli/dist/local}"
: "${AGH_REPO_DIR:=/opt/agnes-harness}"
: "${AGH_WORKSPACE:=/data/agh-workspace}"
export AGH_HOME AGNES_PROFILE
mkdir -p "$AGH_HOME/profiles/$AGNES_PROFILE" "$AGH_WORKSPACE"
# the credential store insists on private directories
chmod 700 "$AGH_HOME" "$AGH_HOME/profiles" "$AGH_HOME/profiles/$AGNES_PROFILE"
if [ ! -f "$AGH_HOME/profiles/$AGNES_PROFILE/profile.yaml" ]; then
  cat > "$AGH_HOME/profiles/$AGNES_PROFILE/profile.yaml" <<'EOF'
name: local-dev
computerUse:
  enabled: false
approvals:
  mode: off
# silicon: chat sessions (read-only host tools); author: generator proposals and studio designers; standard: the CLI
presets: { default: silicon, allowed: [silicon, author, standard] }
EOF
fi
# the job-server bearer token for the plugin, readable by the worker process only
if [ -n "${SA_TOKEN:-}" ]; then
  umask 077; printf '%s' "$SA_TOKEN" > "$AGH_HOME/sa-token"; umask 022
fi
# a stale owner record from a previous container start: the daemon refuses to start over a dead one otherwise
rm -f "$AGH_HOME/data/daemon/owner.json" "$AGH_HOME/data/daemon/agnesd.sock" 2>/dev/null || true
# provisioning runs once the daemon listens; the SDK is loaded from the source checkout through tsx
(
  for _ in $(seq 1 120); do
    [ -S "$AGH_HOME/data/daemon/agnesd.sock" ] && break
    sleep 1
  done
  (cd /app && AGH_REPO="$AGH_REPO_DIR" node --import "$AGH_REPO_DIR/node_modules/tsx/dist/loader.mjs" deploy/agh-provision.mjs "$AGH_DIST") \
    || echo "agh: provisioning failed (is DEEPSEEK_API_KEY set?)" >&2
) &
# workspace = /app: `file:./agh-plugin` resolves there; sessions run in a read-only tree
cd /app
exec node "$AGH_DIST/daemon.mjs" --profile "$AGNES_PROFILE" --workspace /app --data-dir "$AGH_HOME/data"
