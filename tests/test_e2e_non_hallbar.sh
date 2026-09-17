#!/usr/bin/env bash
# E2E: the agent must drive evaluate_design on a non-Hall-bar layer map.
# Proves de-overfitting from Phase 1 works at the user→result level.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}"
export KLAYOUT_MCP_URL="$MCP_URL"
GDS_FILE="/tmp/e2e_non_hallbar.gds"
RESULT_JSON="/tmp/e2e_non_hallbar_result.json"
CLAUDE_LOG="/tmp/e2e_non_hb.log"
MAX_WAIT_SEC=720  # 12 min cap

echo "=== E2E: non-Hall-bar evaluate_design ==="

# Locate conda — system has one of these
CONDA_SH=""
for p in "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/miniconda3"; do
    if [ -f "$p/etc/profile.d/conda.sh" ]; then
        CONDA_SH="$p/etc/profile.d/conda.sh"
        break
    fi
done
if [ -z "$CONDA_SH" ]; then
    echo "ERROR: no conda install found in anaconda3/miniforge3/miniconda3"
    exit 1
fi
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate instrMCPdev

# 1. MCP must be up.
curl -sf "$MCP_URL" > /dev/null 2>&1 || {
    echo "ERROR: MCP server down at $MCP_URL — open KLayout first."
    exit 1
}

# 2. Produce fixture GDS.
python "$PROJECT_DIR/tests/fixtures/non_hallbar_layout.py" "$GDS_FILE"

# 3. Hand Claude a non-Hall-bar task.
read -r -d '' PROMPT <<'EOF' || true
You are being given a synthetic device layout with layers:
  30/0 = device_body (60x60 um)
  31/0 = peripheral_a (outer ring)
  32/0 = peripheral_b (inner ring)
  33/0 = connector
  34/0 = pad
This is NOT a Hall bar. Do NOT assume graphene/graphite layer numbers.

Using the klayoutclaw MCP tools:
  1. create_layout (fresh) then execute_script to read the GDS file from
     /tmp/e2e_non_hallbar.gds into the current view. Use pya Layout.read()
     then rebind cell view if needed.
  2. Call evaluate_design with this layer_map and these checks:
        layer_map = {"device_body": [30, 0],
                     "peripheral_a": [31, 0],
                     "peripheral_b": [32, 0],
                     "connector": [33, 0],
                     "pad": [34, 0]}
        checks = [
          {"name": "component_overlap", "weight": 0.5,
           "args": {"component": "device_body",
                    "region": ["peripheral_a", "peripheral_b"],
                    "region_op": "intersection"}},
          {"name": "bulk_containment", "weight": 0.5,
           "args": {"component": "device_body",
                    "materials": ["peripheral_a", "peripheral_b"]}}
        ]
  3. Write the evaluate_design response (exact JSON) to
     /tmp/e2e_non_hallbar_result.json.
  4. Print "E2E_DONE" to stdout on success.
If any tool raises a schema error complaining about missing defaults,
treat that as a test failure and print "E2E_FAIL: <msg>" instead.
EOF

# Run claude directly — no tmux. Redirect all output to the log so we can
# grep it for the success / failure markers. macOS lacks `timeout`, so we
# use a background+poll pattern to enforce the wall-clock cap portably.
: > "$CLAUDE_LOG"   # truncate
claude \
    --mcp-config "$PROJECT_DIR/mcp_config.json" \
    --dangerously-skip-permissions \
    --print "$PROMPT" > "$CLAUDE_LOG" 2>&1 &
CLAUDE_PID=$!
TIMED_OUT=0
for ((elapsed=0; elapsed<MAX_WAIT_SEC; elapsed+=5)); do
    if ! kill -0 "$CLAUDE_PID" 2>/dev/null; then
        break
    fi
    sleep 5
done
if kill -0 "$CLAUDE_PID" 2>/dev/null; then
    kill -9 "$CLAUDE_PID" 2>/dev/null || true
    TIMED_OUT=1
fi
set +e
wait "$CLAUDE_PID"
CLAUDE_RC=$?
set -e

if [ "$TIMED_OUT" -eq 1 ]; then
    echo "FAIL: claude timed out after ${MAX_WAIT_SEC}s"
    tail -40 "$CLAUDE_LOG"
    exit 3
elif [ "$CLAUDE_RC" -ne 0 ]; then
    echo "FAIL: claude exited rc=$CLAUDE_RC"
    tail -40 "$CLAUDE_LOG"
    exit 2
fi

grep -q "E2E_FAIL" "$CLAUDE_LOG" && {
    echo "FAIL: agent reported E2E_FAIL"
    tail -40 "$CLAUDE_LOG"
    exit 2
}
grep -q "E2E_DONE" "$CLAUDE_LOG" || {
    echo "FAIL: agent did not emit E2E_DONE marker"
    tail -40 "$CLAUDE_LOG"
    exit 3
}
test -f "$RESULT_JSON" || {
    echo "FAIL: result.json missing at $RESULT_JSON"
    tail -40 "$CLAUDE_LOG"
    exit 4
}

# 5. Structural verification.
python - <<'PY'
import json, sys
with open("/tmp/e2e_non_hallbar_result.json") as f:
    r = json.load(f)
assert r["status"] == "ok", r
assert isinstance(r.get("overall"), (int, float)) and r["overall"] > 0, r
# bulk_containment must have produced a numeric score (not 0.0 silently)
checks = {c["name"]: c for c in r["checks"]}
assert "bulk_containment" in checks, r
assert checks["bulk_containment"]["score"] > 0.0, (
    "bulk_containment silently 0; overfit default may remain")
print("PASS: non-Hall-bar evaluate_design works; overall =", r["overall"])
PY

echo "=== E2E: non-Hall-bar evaluate_design PASSED ==="
