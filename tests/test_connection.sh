#!/usr/bin/env bash
# TEST 1: End-to-end connection test with Claude CLI
# Installs plugin, launches KLayout, verifies MCP connection via Claude
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}"
export KLAYOUT_MCP_URL="$MCP_URL"

echo "============================================"
echo "KlayoutClaw E2E Connection Test"
echo "============================================"

# Step 1: Install plugin
echo ""
echo "Step 1: Installing plugin..."
python "$PROJECT_DIR/install.py"

# Step 2: Launch KLayout
echo ""
echo "Step 2: Launching KLayout..."
if pgrep -x "klayout" > /dev/null 2>&1; then
    echo "  KLayout already running."
else
    open /Applications/klayout.app
    echo "  KLayout launched."
fi

# Step 3: Wait for and authenticate the MCP server
echo ""
echo "Step 3: Running authenticated protocol-level test..."
python "$PROJECT_DIR/tests/test_connection.py" --wait-seconds 60

# Step 4: Test with Claude CLI (optional, requires tmux)
echo ""
echo "Step 4: Claude CLI test (manual)"
echo "  To test with Claude CLI, run:"
echo "    claude mcp add klayoutclaw --type http --url $MCP_URL"
echo "    claude 'Call the get_layout_info tool and tell me what you see'"
echo ""
echo "============================================"
echo "Connection test PASSED!"
echo "============================================"
