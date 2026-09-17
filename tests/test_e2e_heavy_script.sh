#!/usr/bin/env bash
# E2E: 900 shapes + back-to-back execute_script queries must not surface
# IndexError or TypeError from the server. Regression guard for the OC
# ml14 failure mode that collapsed that run to score 0.169.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}"
export KLAYOUT_MCP_URL="$MCP_URL"
RESULT_JSON="/tmp/e2e_heavy.json"
CLAUDE_LOG="/tmp/e2e_heavy.log"
MAX_WAIT_SEC=900

echo "=== E2E: heavy-state execute_script ==="

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
Using klayoutclaw MCP — you MUST NOT surface any IndexError or TypeError
from execute_script even under heavy state:
  1. create_layout blank.
  2. Via execute_script, insert 300 boxes each on L20/0, L21/0, L22/0 at
     random positions in a 10000x10000 um canvas (900 shapes total).
     Use `random.seed(42)` for determinism.
  3. Call evaluate_design with one check:
        {"name": "adjacency", "weight": 1.0,
         "args": {"component_a": "L20", "component_b": "L21"}}
     using layer_map {"L20":[20,0], "L21":[21,0], "L22":[22,0]}.
  4. Via execute_script, iterate all shapes on L20 / L21 / L22 in
     separate queries and count them. Do this FIVE TIMES in succession
     (15 separate execute_script calls total after the seed).
  5. Write the final evaluate_design response JSON to /tmp/e2e_heavy.json.
  6. Print "E2E_DONE" on success. If any execute_script call returns an
     error or the counts drift, print "E2E_FAIL: <msg>".
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

# Any IndexError / TypeError surfaced in the conversation log is a regression
if grep -qE "IndexError|TypeError" "$CLAUDE_LOG"; then
    echo "FAIL: IndexError or TypeError surfaced in execute_script"
    grep -nE "IndexError|TypeError" "$CLAUDE_LOG" | head -20
    exit 2
fi

grep -q "E2E_FAIL" "$CLAUDE_LOG" && { echo "FAIL: agent E2E_FAIL"; tail -40 "$CLAUDE_LOG"; exit 2; }
grep -q "E2E_DONE" "$CLAUDE_LOG" || { echo "FAIL: no E2E_DONE"; tail -40 "$CLAUDE_LOG"; exit 3; }
test -f "$RESULT_JSON" || { echo "FAIL: missing result.json"; exit 4; }

python - <<'PY'
import json
with open("/tmp/e2e_heavy.json") as f:
    r = json.load(f)
# Loose verification — the heavy-state E2E is about execute_script not
# crashing, not about a specific score. We just need evaluate_design to
# have completed successfully.
assert r.get("status") == "ok" or r.get("overall") is not None, r
print(f"PASS: heavy-state execute_script stayed healthy; evaluate_design status={r.get('status')}")
PY

echo "=== E2E: heavy-state execute_script PASSED ==="
