#!/usr/bin/env bash
# TEST 2: End-to-end Hall bar creation and evaluation test
# Requires KLayout + MCP to be running (run test_connection.sh first)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="http://127.0.0.1:8765/mcp"
GDS_FILE="$PROJECT_DIR/test_hallbar.gds"
PNG_FILE="$PROJECT_DIR/test_hallbar.png"
TMUX_SESSION="hallbar_test"

echo "============================================"
echo "KlayoutClaw Hall Bar Test (TEST 2)"
echo "============================================"

# Step 1: Verify MCP server is running
echo ""
echo "Step 1: Checking MCP server..."
if ! curl -sf "$MCP_URL" > /dev/null 2>&1; then
    echo "  ERROR: MCP server not running at $MCP_URL"
    echo "  Run test_connection.sh first or start KLayout with the plugin."
    exit 1
fi
echo "  MCP server is ready."

# Step 2: Launch Claude in tmux to create the Hall bar
echo ""
echo "Step 2: Creating Hall bar via Claude + MCP..."

HALLBAR_PROMPT="Using the klayoutclaw MCP tools, create a graphene Hall bar device with these specs:
- Layer 1/0 (Mesa): Graphene channel (W=25um, L=100um) with 6 side probes (3 per side, W=10um, L=20um)
- Layer 2/0 (Metal contacts): Contact pads at each probe end + current terminals
- Layer 3/0 (Bonding pads): Large gold pads (100x100um) connected to contacts via traces
Save the layout as ${GDS_FILE}"

# Kill existing session if any
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true

# Start Claude in tmux
tmux new-session -d -s "$TMUX_SESSION" \
    "claude --mcp-config '$PROJECT_DIR/mcp_config.json' --print '$HALLBAR_PROMPT' 2>&1 | tee /tmp/hallbar_claude.log"

echo "  Claude session started in tmux ($TMUX_SESSION)"
echo "  Waiting for GDS file to be created..."

# Wait for GDS file (max 5 minutes)
MAX_WAIT=300
WAITED=0
while [ ! -f "$GDS_FILE" ]; do
    sleep 5
    WAITED=$((WAITED + 5))
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "  ERROR: GDS file not created after ${MAX_WAIT}s"
        echo "  Check tmux session: tmux attach -t $TMUX_SESSION"
        exit 1
    fi
    echo "  Waiting... (${WAITED}s)"
done
echo "  GDS file created: $GDS_FILE"

# Step 3: Convert GDS to PNG
echo ""
echo "Step 3: Converting GDS to PNG..."
python "$PROJECT_DIR/tools/gds_to_image.py" "$GDS_FILE" "$PNG_FILE"

# Step 4: Structural evaluation
echo ""
echo "Step 4: Structural evaluation..."
python "$PROJECT_DIR/tests/evaluate_gds.py" "$GDS_FILE"

# Step 5: Visual evaluation (manual prompts)
echo ""
echo "Step 5: Visual evaluation"
echo "  PNG file: $PNG_FILE"
echo ""
echo "  To evaluate with Claude:"
echo "    claude 'Look at the image $PNG_FILE. Does this look like a valid Hall bar?"
echo "    Check: channel shape, probe symmetry, pad connectivity'"
echo ""
echo "  To evaluate with Codex:"
echo "    codex 'Analyze the Hall bar layout in $PNG_FILE."
echo "    Verify: rectangular channel, 6 symmetric probes, bonding pads connected'"

# Cleanup tmux
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true

echo ""
echo "============================================"
echo "Hall bar test completed!"
echo "============================================"
