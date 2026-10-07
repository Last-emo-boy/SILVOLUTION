#!/usr/bin/env bash
# One-shot toolchain install for silicon-agent. Downloads the matching
# oss-cad-suite build (yosys, nextpnr-ecp5, verilator, iverilog, cocotb, python)
# into tools/oss-cad-suite. No system packages, no sudo.
#
#   ./setup.sh            install for this machine
#   ./setup.sh --check    only verify an existing install
#
# Supported: macOS arm64/x64, Linux x64/arm64. Windows: run under WSL2.
set -euo pipefail
cd "$(dirname "$0")"

RELEASE="${OSS_CAD_SUITE_RELEASE:-2026-10-06}"
DEST=tools/oss-cad-suite

check() {
  # shellcheck disable=SC1091
  source "$DEST/environment"
  echo "yosys:     $(yosys -V | head -1)"
  echo "nextpnr:   $(nextpnr-ecp5 --version 2>&1 | head -1)"
  echo "verilator: $(verilator --version)"
  echo "iverilog:  $(iverilog -V 2>/dev/null | head -1)"
  echo "cocotb:    $(python3 -c 'import cocotb; print(cocotb.__version__)')"
  echo "python:    $(python3 --version)"
}

if [ "${1:-}" = "--check" ]; then
  check; exit 0
fi

if [ -d "$DEST" ]; then
  echo "toolchain already present at $DEST (delete it to reinstall)"; check; exit 0
fi

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)          plat=darwin-arm64 ;;
  Darwin-x86_64)         plat=darwin-x64 ;;
  Linux-x86_64)          plat=linux-x64 ;;
  Linux-aarch64|Linux-arm64) plat=linux-arm64 ;;
  MINGW*|MSYS*|CYGWIN*)  echo "On Windows, run setup.sh inside WSL2 (the native build lacks cocotb)." >&2; exit 1 ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

tag="${RELEASE//-/}"
url="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${RELEASE}/oss-cad-suite-${plat}-${tag}.tgz"
echo "downloading $url"
mkdir -p tools
curl -L --progress-bar -o tools/oss-cad-suite.tgz "$url"
if [ "$(uname -s)" = Darwin ]; then xattr -d com.apple.quarantine tools/oss-cad-suite.tgz 2>/dev/null || true; fi
tar xzf tools/oss-cad-suite.tgz -C tools
rm tools/oss-cad-suite.tgz
echo "installed to $DEST"
check
echo
echo "LLM key: either export DEEPSEEK_API_KEY, or on macOS:"
echo "  security add-generic-password -U -s deepseek -a \$USER -w 'sk-...'"
