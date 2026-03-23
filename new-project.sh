#!/usr/bin/env bash
# new-project.sh — spin up a Claude Code dev container for a project
#
# Usage:
#   new-project.sh [project-dir] [container-name]
#
# Defaults:
#   project-dir    = current directory
#   container-name = basename of project-dir

set -euo pipefail

# ── args ──────────────────────────────────────────────────────────────────────
PROJECT_DIR="${1:-$(pwd)}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"          # absolute, resolved
CONTAINER_NAME="${2:-$(basename "$PROJECT_DIR")}"
IMAGE="${CLAUDE_CONTAINER_IMAGE:-ghcr.io/astral-sh/uv:bookworm}"   # Debian-based, change as needed
CLAUDE_DIR="$HOME/.claude"

# ── pre-flight ─────────────────────────────────────────────────────────────────
if ! command -v container &>/dev/null; then
  echo "error: 'container' CLI not found. Install Apple Containers first." >&2
  exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "error: project dir '$PROJECT_DIR' does not exist." >&2
  exit 1
fi

# ── check for existing container ──────────────────────────────────────────────
if container inspect "$CONTAINER_NAME" &>/dev/null; then
  echo "Container '$CONTAINER_NAME' already exists — starting it."
  container start "$CONTAINER_NAME"
  container exec -it "$CONTAINER_NAME" /bin/bash
  exit 0
fi

# ── Dockerfile for the dev environment ────────────────────────────────────────
TMPDIR_BUILD="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BUILD"' EXIT

cat > "$TMPDIR_BUILD/Dockerfile" <<'DOCKERFILE'
FROM debian:bookworm-slim

# ── system packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl wget git ca-certificates gnupg \
    build-essential python3 python3-pip \
    jq ripgrep fd-find unzip \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js (LTS) ──────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── uv ─────────────────────────────────────────────────────────────────────────
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx

# ── Claude Code CLI ────────────────────────────────────────────────────────────
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /workspace
CMD ["/bin/bash"]
DOCKERFILE

# ── build image (skip if cached) ───────────────────────────────────────────────
IMAGE_TAG="claude-dev:latest"
if container image inspect "$IMAGE_TAG" &>/dev/null; then
  echo "==> Image $IMAGE_TAG already exists — skipping build."
else
  echo "==> Building dev image ($IMAGE_TAG)…"
  container build -t "$IMAGE_TAG" "$TMPDIR_BUILD"
fi

# ── run container ──────────────────────────────────────────────────────────────
echo "==> Creating container '$CONTAINER_NAME'…"
echo "    project : $PROJECT_DIR  →  /workspace"
echo "    claude  : $CLAUDE_DIR   →  /root/.claude"

container run \
  --name "$CONTAINER_NAME" \
  -it \
  -v "$PROJECT_DIR:/workspace" \
  -v "$CLAUDE_DIR:/root/.claude" \
  -w /workspace \
  "$IMAGE_TAG" \
  /bin/bash
