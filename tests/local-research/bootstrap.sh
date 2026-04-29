#!/usr/bin/env bash
# bootstrap.sh — launch the research-runner container against the running research infra.
#
# Usage:
#   ./tests/local-research/bootstrap.sh "my query"
#   ./tests/local-research/bootstrap.sh --smoke
#
# Requires: colima "research" profile running with research-searxng and research-net.
# If not running, start it with: ./research.py --backend=omlx
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="$SCRIPT_DIR/Dockerfile"
IMAGE="research-runner:latest"
CONTAINER_SEARXNG="research-searxng"
RESEARCH_NET="research-net"
SQUID_PORT=8888

# ── 1. Switch to research Colima docker context ────────────────────────────────
if ! docker context use colima-research >/dev/null 2>&1; then
    echo "warning: could not switch to colima-research docker context; assuming current context is correct" >&2
fi

# ── 2. Verify research-searxng is running and research-net exists ─────────────
if ! docker inspect "$CONTAINER_SEARXNG" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
    echo "error: $CONTAINER_SEARXNG is not running." >&2
    echo "       Start the research environment first: ./research.py --backend=omlx" >&2
    exit 1
fi

if ! docker network inspect "$RESEARCH_NET" >/dev/null 2>&1; then
    echo "error: docker network '$RESEARCH_NET' does not exist." >&2
    echo "       Start the research environment first: ./research.py --backend=omlx" >&2
    exit 1
fi

# ── 3. Compute bridge IP (where Squid listens) — needed for build + run ───────
BRIDGE_IP=$(docker network inspect bridge \
    -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)
if [ -z "$BRIDGE_IP" ]; then
    echo "warning: could not determine bridge gateway IP; falling back to 172.17.0.1" >&2
    BRIDGE_IP="172.17.0.1"
fi

# ── 4. Build research-runner image if missing or Dockerfile is newer ──────────
NEEDS_BUILD=0
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    NEEDS_BUILD=1
else
    IMAGE_CREATED=$(docker image inspect "$IMAGE" --format '{{.Created}}')
    IMAGE_TS=$(date -d "$IMAGE_CREATED" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "${IMAGE_CREATED%%.*}" +%s 2>/dev/null || echo 0)
    DOCKERFILE_TS=$(stat -c %Y "$DOCKERFILE" 2>/dev/null || stat -f %m "$DOCKERFILE" 2>/dev/null || echo 999999999999)
    # Also check lib/ — COPY lib/ lib/ bakes it into the image.
    LIB_TS=$(find "$SCRIPT_DIR/lib" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)
    LIB_TS=${LIB_TS:-0}
    NEWEST_TS=$(( DOCKERFILE_TS > LIB_TS ? DOCKERFILE_TS : LIB_TS ))
    if [ "$NEWEST_TS" -gt "$IMAGE_TS" ]; then
        echo "==> Source newer than image — rebuilding"
        NEEDS_BUILD=1
    fi
fi

if [ "$NEEDS_BUILD" -eq 1 ]; then
    echo "==> Building $IMAGE"
    docker build \
        --build-arg "HTTP_PROXY=http://${BRIDGE_IP}:${SQUID_PORT}" \
        --build-arg "HTTPS_PROXY=http://${BRIDGE_IP}:${SQUID_PORT}" \
        -t "$IMAGE" "$SCRIPT_DIR"
fi

# ── 5. Ensure session dir exists ──────────────────────────────────────────────
SESSION_HOST_DIR="${HOME}/.research/sessions"
mkdir -p "$SESSION_HOST_DIR"

# ── 6. Determine entrypoint ──────────────────────────────────────────────────
if [ "${1:-}" = "--smoke" ]; then
    CMD=("python" "-m" "lib.smoke")
    shift
    TTY_FLAGS="-it"
else
    CMD=("python" "-m" "lib.cli" "$@")
    TTY_FLAGS="-it"
fi

# ── 7. Run the container ──────────────────────────────────────────────────────
exec docker run --rm $TTY_FLAGS \
    --network "$RESEARCH_NET" \
    --add-host "host.docker.internal:host-gateway" \
    -v "${SESSION_HOST_DIR}:/sessions" \
    -e "HTTP_PROXY=http://${BRIDGE_IP}:${SQUID_PORT}" \
    -e "HTTPS_PROXY=http://${BRIDGE_IP}:${SQUID_PORT}" \
    -e "NO_PROXY=${CONTAINER_SEARXNG},host.docker.internal,localhost,127.0.0.1" \
    -e "OMLX_BASE_URL=${OMLX_BASE_URL:-http://host.docker.internal:8000/v1}" \
    -e "OMLX_API_KEY=${OMLX_API_KEY:-}" \
    -e "EMBED_MODEL=${EMBED_MODEL:-}" \
    -e "EXPAND_MODEL=${EXPAND_MODEL:-}" \
    -e "NOTES_MODEL=${NOTES_MODEL:-}" \
    -e "SYNTH_MODEL=${SYNTH_MODEL:-}" \
    "$IMAGE" \
    "${CMD[@]}"
