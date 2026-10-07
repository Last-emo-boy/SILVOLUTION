#!/usr/bin/env bash
# Run the autonomous design loop. Usage: ./run.sh specs/matmul_int8.json [max_iter]
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d tools/oss-cad-suite ]; then
  echo "toolchain missing -- run ./setup.sh first" >&2
  exit 1
fi
source tools/oss-cad-suite/environment
exec python3 -m silicon_agent.loop "$@"
