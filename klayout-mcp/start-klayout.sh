#!/usr/bin/env bash
# klayout-mcp/start-klayout.sh
set -euo pipefail
: "${KLAYOUT_PLUGIN_DIR:=/opt/KlayoutClaw/plugin}"
: "${PYTHON_PATH:=/opt/venv/bin/python}"
: "${KLAYOUT_MCP_BIND:=0.0.0.0}"
export PYTHON_PATH KLAYOUT_MCP_BIND
mkdir -p /tmp
exec xvfb-run -a -s "-screen 0 1920x1080x24" \
     klayout -e -j "$KLAYOUT_PLUGIN_DIR" -nc
