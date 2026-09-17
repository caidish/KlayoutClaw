#!/usr/bin/env bash
# E2E: agent runs material_overlap_report on a two-material fixture.
# Proves the new primitive (Phase 2) is reachable through the full
# user -> MCP -> KLayout -> result loop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}"
export KLAYOUT_MCP_URL="$MCP_URL"
RESULT_JSON="/tmp/e2e_overlap_report.json"
CLAUDE_LOG="/tmp/e2e_overlap.log"
MAX_WAIT_SEC=720

echo "=== E2E: material_overlap_report ==="

# Locate conda
CONDA_SH=""
for p in "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/miniconda3"; do
    if [ -f "$p/etc/profile.d/conda.sh" ]; then
        CONDA_SH="$p/etc/profile.d/conda.sh"
        break
    fi
done
if [ -z "$CONDA_SH" ]; then
    echo "ERROR: no conda install found"
    exit 1
fi
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate instrMCPdev

# MCP must be up
curl -sf "$MCP_URL" > /dev/null 2>&1 || {
    echo "ERROR: MCP server down at $MCP_URL — open KLayout first."
    exit 1
}

# Prompt — agent draws two overlapping rectangles on L11/L13 and asks
# evaluate_design for the material_overlap_report. Layer numbers here are
# incidental to the test (synthetic fresh layout, no benchmark
# connection), but the layer_map keys are device-agnostic A/B.
read -r -d '' PROMPT <<'EOF' || true
Using klayoutclaw MCP:
  1. create_layout (fresh, blank).
  2. Via execute_script, draw two rectangles:
        L11/0 at (0,0)-(10,10)
        L13/0 at (6,6)-(16,16)
  3. Call evaluate_design with one check:
        {"name": "material_overlap_report",
         "args": {"materials": ["A", "B"]},
         "weight": 1.0}
     using layer_map {"A": [11, 0], "B": [13, 0]}.
  4. Write the evaluate_design response JSON to
     /tmp/e2e_overlap_report.json.
  5. Print "E2E_DONE" on success, "E2E_FAIL: <msg>" on any error.
EOF

: > "$CLAUDE_LOG"
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
    exit 4
}

# Structural verification
python - <<'PY'
import json
with open("/tmp/e2e_overlap_report.json") as f:
    r = json.load(f)
assert r["status"] == "ok", r
assert r["checks"][0]["name"] == "material_overlap_report"
report = r["checks"][0].get("report")
assert isinstance(report, dict), f"report field missing or wrong type: {report!r}"
for key in ("A_only", "B_only", "A_and_B"):
    assert key in report, f"report missing {key!r}; got {sorted(report.keys())}"
# Known geometry: A-only = B-only = 84 um^2, A_and_B = 16 um^2
ab = report["A_and_B"]["area_um2"]
assert 15 <= ab <= 17, f"A_and_B area unexpected: {ab}"
a_only = report["A_only"]["area_um2"]
assert 83 <= a_only <= 85, f"A_only area unexpected: {a_only}"
print(f"PASS: material_overlap_report end-to-end; A_and_B area = {ab}")
PY

echo "=== E2E: material_overlap_report PASSED ==="
