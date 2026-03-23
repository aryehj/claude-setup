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
    ufw iptables \
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

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
DOCKERFILE

cat > "$TMPDIR_BUILD/entrypoint.sh" <<'ENTRYPOINT'
#!/usr/bin/env bash
# Apply firewall rules inside the container (best-effort — requires CAP_NET_ADMIN)
# Strategy: deny all in/out, then whitelist by port.
# Port coverage:
#   53   DNS          — name resolution
#   80   HTTP         — apt mirrors, some package manager redirects
#   443  HTTPS        — Anthropic API, GitHub, npm, PyPI, uv/astral.sh, search
#   22   SSH          — git over SSH (GitHub, GitLab, etc.)
#   9418 git://       — git native protocol
#
# Note: GitHub also publishes IP CIDRs (added below) which lets us be explicit
# about git+HTTPS even if we ever tighten port 443 further.

setup_firewall() {
  command -v ufw &>/dev/null || return 0

  ufw --force reset   2>/dev/null
  ufw default deny incoming
  ufw default deny outgoing

  # loopback — always allow
  ufw allow in  on lo
  ufw allow out on lo

  # DNS
  ufw allow out 53/udp  comment 'DNS'
  ufw allow out 53/tcp  comment 'DNS-TCP'

  # HTTP — apt, some redirects
  ufw allow out 80/tcp  comment 'HTTP'

  # HTTPS — Anthropic API, npm, PyPI, uv, GitHub HTTPS, search engines
  ufw allow out 443/tcp comment 'HTTPS'

  # SSH out — git@github.com and friends
  ufw allow out 22/tcp  comment 'SSH-git'

  # git:// protocol
  ufw allow out 9418/tcp comment 'git-protocol'

  # ── GitHub published IP ranges (https://api.github.com/meta) ──────────────
  # These cover GitHub Actions, API, web, git, packages — kept explicit so we
  # can tighten HTTPS in the future without breaking git ops.
  for cidr in \
    192.30.252.0/22 \
    185.199.108.0/22 \
    140.82.112.0/20 \
    143.55.64.0/20 \
    20.201.28.151/32 \
    20.205.243.166/32 \
    20.87.225.212/32 \
    20.248.137.48/32 \
    20.207.73.82/32 \
    20.27.177.113/32 \
    20.200.245.247/32 \
    20.233.54.53/32; do
    ufw allow out to "$cidr" comment 'GitHub'
  done

  # ── local LLM server (host machine) ─────────────────────────────────────────
  # Uncomment to allow the container to reach a local LLM server running on the
  # host (Ollama, LM Studio, llama.cpp, LocalAI, etc.).
  #
  # The host is reachable via the default gateway (Apple Containers uses the
  # Apple Virtualization Framework virtual network, typically 192.168.64.1).
  #
  # IMPORTANT: your LLM server must also bind to 0.0.0.0, not 127.0.0.1:
  #   Ollama:     OLLAMA_HOST=0.0.0.0 ollama serve
  #   LM Studio:  Settings → Local Server → "serve on local network"
  #   llama.cpp:  --host 0.0.0.0
  #   LocalAI:    --address 0.0.0.0:8080
  #
  # HOST_IP=$(ip route | awk '/default/ { print $3; exit }')
  # LOCAL_LLM_PORT="${LOCAL_LLM_PORT:-11434}"   # 11434=Ollama, 1234=LM Studio, 8080=llama.cpp/LocalAI
  # ufw allow out to "$HOST_IP" port "$LOCAL_LLM_PORT" proto tcp comment 'local LLM'

  ufw --force enable 2>/dev/null || true
  echo "[firewall] ufw enabled — deny all, allow DNS/HTTP/HTTPS/SSH/git + GitHub CIDRs"
}

setup_firewall

exec "$@"
ENTRYPOINT

# ── build image ────────────────────────────────────────────────────────────────
IMAGE_TAG="claude-dev:latest"
echo "==> Building dev image ($IMAGE_TAG)…"
container build -t "$IMAGE_TAG" "$TMPDIR_BUILD"

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
