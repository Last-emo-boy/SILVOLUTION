#!/usr/bin/env bash
# Entry point of the AGH container: run the Agnes Harness daemon in the foreground with the home on the
# shared volume, so the silicon-agent job server (another container on the same volume) reaches its unix
# socket. First start provisions the DeepSeek account from the environment; the key is handed to AGH's
# credential store once and never written anywhere else.
set -euo pipefail
: "${AGH_HOME:=/srv/sa/agh}"
: "${AGNES_PROFILE:=local-dev}"
: "${AGH_DIST:=/opt/agnes-harness/packages/cli/dist/local}"
: "${AGH_WORKSPACE:=/srv/sa/agh-workspace}"
export AGH_HOME AGNES_PROFILE
mkdir -p "$AGH_HOME/profiles/$AGNES_PROFILE" "$AGH_WORKSPACE"
chmod 700 "$AGH_HOME" "$AGH_HOME/profiles" "$AGH_HOME/profiles/$AGNES_PROFILE"
if [ ! -f "$AGH_HOME/profiles/$AGNES_PROFILE/profile.yaml" ]; then
  cat > "$AGH_HOME/profiles/$AGNES_PROFILE/profile.yaml" <<'EOF'
name: local-dev
computerUse:
  enabled: false
approvals:
  mode: off
# the forked harness ships a `silicon` preset (read-only host tools); standard stays allowed for the CLI
presets: { default: silicon, allowed: [silicon, author, standard] }
EOF
fi
# older profiles: allow the author recipe the fork ships now
sed -i 's/allowed: \[silicon, standard\]/allowed: [silicon, author, standard]/' "$AGH_HOME/profiles/$AGNES_PROFILE/profile.yaml"
# the job-server bearer token for the plugin, readable by the worker process only
if [ -n "${SA_TOKEN:-}" ]; then
  umask 077; printf '%s' "$SA_TOKEN" > "$AGH_HOME/sa-token"; umask 022
fi
# stale owner record from a previous container: the daemon refuses to start over a dead one otherwise
rm -f "$AGH_HOME/data/daemon/owner.json" "$AGH_HOME/data/daemon/agnesd.sock" 2>/dev/null || true
# provisioning runs once the daemon listens
(
  for _ in $(seq 1 120); do
    [ -S "$AGH_HOME/data/daemon/agnesd.sock" ] && break
    sleep 1
  done
  # the SDK is loaded from the AGH source checkout (TypeScript), hence tsx; the daemon resolves the
  # plugin's file: source against its workspace, so provisioning runs from the app checkout
  (cd /opt/silicon-agent/app && AGH_REPO=/opt/agnes-harness node --import /opt/agnes-harness/node_modules/tsx/dist/loader.mjs deploy/agh-provision.mjs "$AGH_DIST") \
    || echo "agh: provisioning failed" >&2
) &
# workspace = the app checkout: `file:./agh-plugin` resolves there, and sessions run in a read-only tree
cd /opt/silicon-agent/app
exec node "$AGH_DIST/daemon.mjs" --profile "$AGNES_PROFILE" --workspace /opt/silicon-agent/app --data-dir "$AGH_HOME/data"
