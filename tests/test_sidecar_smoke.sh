#!/usr/bin/env bash
# tests/test_sidecar_smoke.sh
set -euo pipefail
docker run --rm -d --name klayout-smoke \
    -p 18765:8765 \
    klayout-mcp:0.30.5-arm64
trap 'docker rm -f klayout-smoke >/dev/null 2>&1 || true' EXIT

# Wait up to 5 min for the plugin to come up
for i in $(seq 1 60); do
    if curl -fsS http://localhost:18765/mcp >/dev/null 2>&1; then
        echo "PASS: /mcp reachable from host (port-forwarded)"
        exit 0
    fi
    sleep 5
done
echo "FAIL: /mcp not reachable after 5min"
exit 1
