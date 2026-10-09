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
# the harness's own web workbench (sessions, tool records, approvals, provider settings) rides on the daemon's
# local-web capability: the daemon listens for the browser on a loopback websocket and the `serve` launcher
# (started below) serves the pages on 127.0.0.1:4178 inside this container; a relay on :4177 exposes it to the compose
# network. The job server proxies it to the
# sluice route /agh/ (SSO-gated), rewriting Host/Origin to the loopback origin both sides insist on.
AGH_WEB_ORIGIN="${AGNES_WEB_ORIGIN:-http://127.0.0.1:4178}"
(
  for _ in $(seq 1 180); do
    [ -S "$AGH_HOME/data/daemon/agnesd.sock" ] && break
    sleep 1
  done
  sleep 5
  cd /opt/silicon-agent/app
  # the launcher binds the loopback only (its origin contract); the relay exposes it on the compose network
  AGH_WEB_RELAY_LISTEN=0.0.0.0:4177 node /opt/silicon-agent/app/deploy/agh-web-relay.mjs &
  while true; do
    AGNES_WEB_ORIGIN="$AGH_WEB_ORIGIN" node "$AGH_DIST/agnes.mjs" serve --profile "$AGNES_PROFILE" --data-dir "$AGH_HOME/data" --cwd /opt/silicon-agent/app \
      || echo "agh: web serve exited ($?), restarting in 10 s" >&2
    sleep 10
  done
) &
# workspace = the app checkout: `file:./agh-plugin` resolves there, and sessions run in a read-only tree
cd /opt/silicon-agent/app
exec node "$AGH_DIST/daemon.mjs" --profile "$AGNES_PROFILE" --workspace /opt/silicon-agent/app --data-dir "$AGH_HOME/data" \
  --web-addr 127.0.0.1:0 --web-origin "$AGH_WEB_ORIGIN"
