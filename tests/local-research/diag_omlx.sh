#!/usr/bin/env bash
# diag_omlx.sh — diagnose the omlx 404 from inside the runner container.
# Mirrors bootstrap.sh's run-time environment exactly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${OMLX_API_KEY:-}" ]; then
    echo "error: OMLX_API_KEY is not set." >&2
    exit 1
fi

docker context use colima-research >/dev/null 2>&1 || true

BRIDGE_IP=$(docker network inspect bridge \
    -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo "172.17.0.1")

# Mount the diag script into /tmp (avoids needing to rebuild the image).
docker run --rm \
    --network research-net \
    --add-host host.docker.internal:host-gateway \
    -v "$SCRIPT_DIR/diag_omlx.py:/tmp/diag_omlx.py:ro" \
    -e OMLX_API_KEY="$OMLX_API_KEY" \
    -e HTTP_PROXY="http://${BRIDGE_IP}:8888" \
    -e HTTPS_PROXY="http://${BRIDGE_IP}:8888" \
    -e NO_PROXY="research-searxng,host.docker.internal,localhost,127.0.0.1" \
    research-runner:latest \
    python /tmp/diag_omlx.py
