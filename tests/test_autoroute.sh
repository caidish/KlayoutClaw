#!/usr/bin/env bash
# TEST 3: End-to-end autoroute test
# Creates unrouted Hall bar, runs auto_route via MCP, evaluates result
# Requires KLayout + MCP server to be running
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MCP_URL="http://127.0.0.1:8765/mcp"
GDS_UNROUTED="/tmp/test_hallbar_unrouted.gds"
GDS_ROUTED="/tmp/test_hallbar_routed.gds"
PNG_ROUTED="/tmp/test_hallbar_routed.png"

# Activate conda environment
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev

echo "============================================"
echo "KlayoutClaw Autoroute Test (TEST 3)"
echo "============================================"

# Step 1: Verify MCP server is running
echo ""
echo "Step 1: Checking MCP server..."
if ! curl -sf "$MCP_URL" > /dev/null 2>&1; then
    echo "  ERROR: MCP server not running at $MCP_URL"
    echo "  Start KLayout with the plugin first."
    exit 1
fi
echo "  MCP server is ready."

# Step 2: Create unrouted Hall bar
echo ""
echo "Step 2: Creating unrouted Hall bar..."
python "$PROJECT_DIR/tests/create_hallbar_unrouted.py" "$GDS_UNROUTED"
echo "  Created: $GDS_UNROUTED"

# Step 3: MCP calls — initialize, auto_route, save_layout
echo ""
echo "Step 3: Running auto_route via MCP..."
python -c "
import json, sys, urllib.request

MCP_URL = '$MCP_URL'

def mcp_request(method, params=None, req_id=1, session_id=None, timeout=30):
    payload = {'jsonrpc': '2.0', 'id': req_id, 'method': method}
    if params:
        payload['params'] = params
    headers = {'Content-Type': 'application/json'}
    if session_id:
        headers['Mcp-Session-Id'] = session_id
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method='POST',
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    body = r.read().decode()
    data = json.loads(body)
    return r, data

def mcp_notify(method, session_id):
    payload = {'jsonrpc': '2.0', 'method': method}
    headers = {'Content-Type': 'application/json', 'Mcp-Session-Id': session_id}
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method='POST',
    )
    urllib.request.urlopen(req, timeout=5)

# Initialize
print('  Initializing MCP session...')
r, data = mcp_request('initialize', {
    'protocolVersion': '2025-03-26',
    'capabilities': {},
    'clientInfo': {'name': 'test_autoroute', 'version': '0.1'},
})
session_id = r.headers.get('Mcp-Session-Id')
if not session_id:
    print('  ERROR: No session ID returned')
    sys.exit(1)
print(f'  Session ID: {session_id}')
mcp_notify('notifications/initialized', session_id)

# Load unrouted GDS into KLayout via execute_script
print('  Loading unrouted GDS into KLayout...')
load_script = '''
import pya
app = pya.Application.instance()
mw = app.main_window()
mw.load_layout(\"$GDS_UNROUTED\", 0)
print(\"Layout loaded\")
'''
r, data = mcp_request('tools/call', {
    'name': 'execute_script',
    'arguments': {'code': load_script},
}, req_id=2, session_id=session_id, timeout=30)
if 'error' in data:
    print(f'  ERROR loading GDS: {data[\"error\"]}')
    sys.exit(1)
print('  Layout loaded into KLayout.')

# Call auto_route
print('  Calling auto_route (this may take a while)...')
r, data = mcp_request('tools/call', {
    'name': 'auto_route',
    'arguments': {
        'pin_layer_a': '102/0',
        'pin_layer_b': '111/0',
        'obstacle_layers': ['1/0', '3/0'],
        'output_layer': '10/0',
        'path_width': 10.0,
        'obs_safe_distance': 15.0,
        'path_safe_distance': 10.0,
        'map_resolution': 5.0,
    },
}, req_id=3, session_id=session_id, timeout=180)

if 'error' in data:
    print(f'  ERROR: auto_route failed: {data[\"error\"]}')
    sys.exit(1)

result = data.get('result', {})
content = result.get('content', [])
for item in content:
    text = item.get('text', '')
    if text:
        print(f'  auto_route result: {text[:500]}')
print('  auto_route completed.')

# Save routed layout
print('  Saving routed layout...')
r, data = mcp_request('tools/call', {
    'name': 'save_layout',
    'arguments': {'filename': '$GDS_ROUTED'},
}, req_id=4, session_id=session_id, timeout=30)

if 'error' in data:
    print(f'  ERROR saving layout: {data[\"error\"]}')
    sys.exit(1)
print('  Saved: $GDS_ROUTED')
"

# Step 4: Structural evaluation
echo ""
echo "Step 4: Structural evaluation..."
python "$PROJECT_DIR/tests/evaluate_routing.py" "$GDS_ROUTED"

# Step 5: Generate PNG for visual check
echo ""
echo "Step 5: Generating PNG..."
python "$PROJECT_DIR/tools/gds_to_image.py" "$GDS_ROUTED" "$PNG_ROUTED"
echo "  PNG saved: $PNG_ROUTED"

echo ""
echo "============================================"
echo "Autoroute test completed!"
echo "  Routed GDS: $GDS_ROUTED"
echo "  Screenshot: $PNG_ROUTED"
echo "============================================"
