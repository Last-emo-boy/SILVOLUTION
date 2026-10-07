#!/usr/bin/env bash
# Vivado wrapper for the silicon-agent server when it runs *outside* the Vivado
# container: execs vivado inside the vivado-files container, mapping the host
# workspace path (/srv/fpga/...) to the container path (/workspace/...).
#
# Not used when the server itself runs inside the container (deploy/compose.yml).
set -euo pipefail
args=()
for a in "$@"; do args+=("${a//\/srv\/fpga/\/workspace}"); done
exec docker exec -w /workspace vivado-files bash -c \
  'source /opt/amd/2025.2/Vivado/settings64.sh; export LD_PRELOAD=/lib/x86_64-linux-gnu/libudev.so.1; export HOME=/tmp; exec vivado "$@"' -- "${args[@]}"
