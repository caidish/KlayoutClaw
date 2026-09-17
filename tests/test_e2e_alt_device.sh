#!/usr/bin/env bash
# E2E: alt-device full pipeline. Exercises auto_route dry_run, real
# routing, route_inspect, and evaluate_design together on a 2-contact
# transistor laid out on non-Hall-bar layers (50-54). Proves Phases 1-3
# de-overfit + new primitives stack correctly end-to-end.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}"
export KLAYOUT_MCP_URL="$MCP_URL"
RESULT_JSON="/tmp/e2e_alt_device.json"
CLAUDE_LOG="/tmp/e2e_alt.log"
MAX_WAIT_SEC=900

echo "=== E2E: alt-device full pipeline ==="

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
Device: a toy 2-contact transistor on a non-Hall-bar layer map.
Layers:
  50/0 = channel
  51/0 = source_contact (pin_a)
  52/0 = drain_contact (pin_b)
  53/0 = route
  54/0 = pad

Using klayoutclaw MCP:
  1. create_layout blank.
  2. Via execute_script, draw:
        channel at (-20,-5)-(20,5) on L50/0
        source_contact at (-15,-3)-(-13,3) on L51/0
        drain_contact at (13,-3)-(15,3) on L52/0
        pad_a at (-50,-5)-(-45,5) on L54/0
        pad_b at (45,-5)-(50,5) on L54/0
  3. auto_route with dry_run=true, pin_layer_a="51/0", pin_layer_b="54/0",
     output_layer="53/0", path_width=1.0, map_resolution=1.0.
     Record the pairs array.
  4. auto_route with dry_run=false, same parameters. Then again with
     pin_layer_a="52/0" (drain -> pads).
  5. route_inspect with route_layer="53/0", contact_layers=["51/0","52/0"],
     pad_layer="54/0". Confirm at least 2 routes are mapped.
  6. evaluate_design with:
        layer_map = {"channel":[50,0],"source":[51,0],"drain":[52,0],
                     "route":[53,0],"pad":[54,0]}
        checks = [
          {"name":"contact_isolation","args":{"component":"route"},
           "weight":0.4},
          {"name":"connectivity",
           "args":{"contact_component":"source",
                   "pad_component":"pad",
                   "route_component":"route"},
           "weight":0.3},
          {"name":"material_overlap_report",
           "args":{"materials":["channel","source","drain"]},
           "weight":0.3}
        ]
  7. Write the evaluate_design response JSON to /tmp/e2e_alt_device.json.
  8. Print "E2E_DONE" on success, "E2E_FAIL: <msg>" on any error.
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
with open("/tmp/e2e_alt_device.json") as f:
    r = json.load(f)
assert r["status"] == "ok", r
names = {c["name"] for c in r["checks"]}
assert {"contact_isolation","connectivity","material_overlap_report"} <= names, (
    f"Missing checks: got {sorted(names)}")
mor = [c for c in r["checks"] if c["name"]=="material_overlap_report"][0]
assert "report" in mor, f"material_overlap_report missing 'report' side-data: {mor}"
print(f"PASS: alt-device pipeline OK; overall={r.get('overall')}")
PY

echo "=== E2E: alt-device full pipeline PASSED ==="
