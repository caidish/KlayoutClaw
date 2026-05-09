#!/usr/bin/env bash
# klayout-mcp/start-klayout.sh
set -euo pipefail
: "${PYTHON_PATH:=/opt/venv/bin/python}"
: "${KLAYOUT_MCP_BIND:=0.0.0.0}"
export PYTHON_PATH KLAYOUT_MCP_BIND
mkdir -p /tmp

# Don't `exec xvfb-run` directly — `xvfb-run` is a shell script that
# forks Xvfb and then exec's the wrapped command, which has signal /
# reaping issues when it ends up as PID 1 in a container (verified
# at runtime: Xvfb starts but the wrapped klayout child never spawns).
# Keep bash as PID 1 so it reaps Xvfb cleanly, then run xvfb-run as a
# child. `wait` keeps PID 1 alive while klayout runs.
xvfb-run -a klayout -e -nc &
KL_PID=$!

# Forward TERM/INT to xvfb-run for graceful shutdown
trap 'kill -TERM $KL_PID 2>/dev/null; wait $KL_PID' TERM INT
wait $KL_PID
