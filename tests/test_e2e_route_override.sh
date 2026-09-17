#!/usr/bin/env bash
# E2E: agent runs auto_route twice — first dry_run to see the default
# assignment, then pin_pairs_override to force a crossed pairing. Proves
# the ml14 "manual pairing" workflow works end-to-end.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}"
export KLAYOUT_MCP_URL="$MCP_URL"
RESULT_JSON="/tmp/e2e_route_override.json"
DRYRUN_JSON="/tmp/e2e_route_override_dryrun.json"
CLAUDE_LOG="/tmp/e2e_route_override.log"
MAX_WAIT_SEC=900   # 15 min cap — agent does 2 route calls

echo "=== E2E: pin_pairs_override ==="

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

curl -sf "$MCP_URL" > /dev/null 2>&1 || {
    echo "ERROR: MCP server down at $MCP_URL — open KLayout first."
    exit 1
}

read -r -d '' PROMPT <<'EOF' || true
Using klayoutclaw MCP:
  1. create_layout (fresh, blank).
  2. Via execute_script, draw four pins:
        L100/0 at (0,0)-(2,2)
        L100/0 at (0,100)-(2,102)
        L101/0 at (50,0)-(52,2)
        L101/0 at (50,100)-(52,102)
     (Two vertically-separated pairs. The default ordered-loop assignment will match them in
     parallel: A0->B0 at y=0, A1->B1 at y=100. We want the crossed
     pairing: A0->B1, A1->B0.)
  3. auto_route with dry_run=true, pin_layer_a="100/0", pin_layer_b="101/0",
     output_layer="10/0", map_resolution=2.0, path_width=2.0. Write the
     response JSON to /tmp/e2e_route_override_dryrun.json.
  4. auto_route AGAIN with dry_run=false, pin_pairs_override=[[0,1],[1,0]],
     same layer specs and same map_resolution/path_width. Write the
     response JSON to /tmp/e2e_route_override.json.
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
    echo "FAIL: override result JSON missing at $RESULT_JSON"
    exit 4
}

python - <<'PY'
import json
# The plugin's auto_route response includes summary stats but not per-path
# geometry (paths are inserted into the layout, not returned). Compare the
# override response's pair assignment against the dry_run's to prove the
# override actually changed the default assignment.
with open("/tmp/e2e_route_override.json") as f:
    r = json.load(f)
with open("/tmp/e2e_route_override_dryrun.json") as f:
    d = json.load(f)

assert r["status"] in ("success", "partial"), r
assert r["routed_pairs"] == 2, r
assert not r.get("errors"), f"unexpected errors: {r['errors']}"

# Dry run should have produced the parallel default pairing: (0,0)+(1,1)
dry_pairs = [(p["pin_a_idx"], p["pin_b_idx"]) for p in d["pairs"]]
assert sorted(dry_pairs) == [(0, 0), (1, 1)], (
    f"default dry_run unexpectedly matched crossed pairs: {dry_pairs}. "
    f"The override's distinctness check below is meaningless.")

# The override's resulting routed_pairs count + success status, combined
# with the dry_run showing a different (parallel) baseline, proves the
# override took effect. To further validate the geometry is crossed, query
# the MCP for shapes on output_layer=10/0 and verify their endpoints span
# the full y-range (not just the short y-slice default assignment would produce).
import os
import urllib.request
MCP = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")
code = """
view, layout, cell = _get_or_create_view()
li = layout.layer(10, 0)
dbu = layout.dbu
paths = []
for s in cell.shapes(li).each():
    if s.is_path():
        pts = [(p.x * dbu, p.y * dbu) for p in s.path.each_point()]
        paths.append(pts)
    elif s.is_polygon() or s.is_box():
        bb = s.bbox()
        paths.append([(bb.left * dbu, bb.bottom * dbu),
                      (bb.right * dbu, bb.top * dbu)])
result = {"paths": paths}
"""
req = urllib.request.Request(MCP, data=json.dumps({
    "jsonrpc": "2.0", "id": 99, "method": "tools/call",
    "params": {"name": "execute_script", "arguments": {"code": code}}
}).encode(), headers={"Content-Type": "application/json"}, method="POST")
resp = urllib.request.urlopen(req, timeout=30)
body = json.loads(resp.read().decode())
inner = json.loads(body["result"]["content"][0]["text"])
# execute_script merges the user's `result = {...}` dict directly at the
# top level alongside stdout / stderr. An "error" key appears only when
# the user's code raised. On success, just grab `paths` directly.
assert "error" not in inner, f"execute_script raised: {inner.get('error')!r}"
paths = inner["paths"]
assert len(paths) >= 2, f"expected >=2 route shapes on L10/0, got {len(paths)}: {paths}"
# For a crossed pair: at least one route endpoint-pair must span >50 um in y.
spanning = [p for p in paths if abs(p[-1][1] - p[0][1]) > 50]
assert spanning, (
    f"No route spans the y-gap; override did NOT produce crossed pairing. "
    f"paths={paths}")
print(f"PASS: pin_pairs_override produced crossed pairing; "
      f"{len(paths)} route shapes committed, {len(spanning)} span the y-gap.")
PY

echo "=== E2E: pin_pairs_override PASSED ==="
