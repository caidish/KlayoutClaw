#!/usr/bin/env bash
# E2E: agent draws two crossing route rectangles, calls evaluate_design
# with contact_isolation, verifies crossing_pairs is populated with the
# correct tuple shape [i, j, area_um2].
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}"
export KLAYOUT_MCP_URL="$MCP_URL"
RESULT_JSON="/tmp/e2e_crossings.json"
CLAUDE_LOG="/tmp/e2e_crossings.log"
MAX_WAIT_SEC=720

echo "=== E2E: crossing_pairs ==="

CONDA_SH=""
for p in "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/miniconda3"; do
    if [ -f "$p/etc/profile.d/conda.sh" ]; then
        CONDA_SH="$p/etc/profile.d/conda.sh"
        break
    fi
done
if [ -z "$CONDA_SH" ]; then echo "ERROR: no conda"; exit 1; fi
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate instrMCPdev

curl -sf "$MCP_URL" > /dev/null 2>&1 || {
    echo "ERROR: MCP server down"; exit 1;
}

read -r -d '' PROMPT <<'EOF' || true
Using klayoutclaw MCP:
  1. create_layout (fresh, blank).
  2. Via execute_script, draw two intentionally-crossing routes on L3/0:
        (0,49)-(100,51)   — horizontal wire, width 2
        (49,0)-(51,100)   — vertical wire, width 2
     Plus two pads on L2/0 placed FAR from the crossing so the junction
     filter does NOT suppress the mid-body overlap:
        (200,200)-(210,210)
        (300,300)-(310,310)
  3. Call evaluate_design with one check:
        {"name": "contact_isolation",
         "args": {"component": "contact_route"},
         "weight": 1.0}
     using layer_map {"contact_route": [3,0], "bonding_pad": [2,0]}.
  4. Write the evaluate_design response JSON to /tmp/e2e_crossings.json.
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
    if ! kill -0 "$CLAUDE_PID" 2>/dev/null; then break; fi
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

if [ "$TIMED_OUT" -eq 1 ]; then echo "FAIL: timed out"; tail -40 "$CLAUDE_LOG"; exit 3; fi
if [ "$CLAUDE_RC" -ne 0 ]; then echo "FAIL: rc=$CLAUDE_RC"; tail -40 "$CLAUDE_LOG"; exit 2; fi

grep -q "E2E_FAIL" "$CLAUDE_LOG" && { echo "FAIL: agent E2E_FAIL"; tail -40 "$CLAUDE_LOG"; exit 2; }
grep -q "E2E_DONE" "$CLAUDE_LOG" || { echo "FAIL: no E2E_DONE"; tail -40 "$CLAUDE_LOG"; exit 3; }
test -f "$RESULT_JSON" || { echo "FAIL: missing result.json"; exit 4; }

python - <<'PY'
import json
with open("/tmp/e2e_crossings.json") as f:
    r = json.load(f)
assert r["status"] == "ok", r
check = r["checks"][0]
assert check["name"] == "contact_isolation", check
assert check["score"] < 1.0, (
    f"Mid-body crossing should drop the score below 1.0; got {check['score']}. "
    f"Detection broken.")
cp = check.get("crossing_pairs", [])
assert len(cp) > 0, (
    f"crossing_pairs empty despite detected crossing — regression. check={check!r}")
for tup in cp:
    assert isinstance(tup, list) and len(tup) == 3, tup
print(f"PASS — {len(cp)} crossing(s) reported via crossing_pairs side-data.")
PY

echo "=== E2E: crossing_pairs PASSED ==="
