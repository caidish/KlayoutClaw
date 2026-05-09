#!/usr/bin/env bash
# tests/test_sidecar_cross_container.sh — the real BLOCKER 1 gate
set -euo pipefail
docker network create klayout-test-net >/dev/null 2>&1 || true
trap 'docker network rm klayout-test-net >/dev/null 2>&1 || true' EXIT

docker run --rm -d --name klayout-x --network klayout-test-net klayout-mcp:0.30.5-arm64
trap 'docker rm -f klayout-x >/dev/null 2>&1 || true; docker network rm klayout-test-net >/dev/null 2>&1 || true' EXIT

# Wait for /mcp inside klayout-x
for i in $(seq 1 60); do
    if docker exec klayout-x curl -fsS http://localhost:8765/mcp >/dev/null 2>&1; then
        break
    fi
    sleep 5
done

# THE TEST: from a separate container on the same network, can we reach klayout-x:8765?
if docker run --rm --network klayout-test-net curlimages/curl:latest \
    -fsS http://klayout-x:8765/mcp >/dev/null; then
    echo "PASS: klayout-x:8765 reachable from peer container (0.0.0.0 bind works)"
    exit 0
fi
echo "FAIL: cross-container reach failed — plugin still bound to loopback?"
exit 1
