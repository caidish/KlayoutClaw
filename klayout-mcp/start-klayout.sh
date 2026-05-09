#!/usr/bin/env bash
# klayout-mcp/start-klayout.sh
set -euo pipefail
: "${PYTHON_PATH:=/opt/venv/bin/python}"
: "${KLAYOUT_MCP_BIND:=0.0.0.0}"
export PYTHON_PATH KLAYOUT_MCP_BIND
mkdir -p /tmp
# NOTE: do NOT pass -j to klayout — KLayout auto-loads .lym macros from
# ~/.klayout/pymacros (where install.py copies them at image-build time).
# `-j` only adds a search path and does NOT auto-execute .lym files, and
# combining it with `xvfb-run -s "-screen ..."` causes klayout to fail to spawn
# in this Ubuntu 24.04 xvfb-run build (verified at runtime).
exec xvfb-run -a klayout -e -nc
