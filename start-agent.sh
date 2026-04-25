#!/usr/bin/env bash
# start-agent.sh — spin up a Colima-backed agent container for a project.
#
# Sibling to start-claude.sh. Uses one shared Colima VM (profile:
# claude-agent), one shared docker container (claude-agent), with both the
# Claude Code and OpenCode CLIs installed. Enforces a VM-level egress
# allowlist via tinyproxy + iptables DOCKER-USER rules, and routes local
# inference to Ollama or omlx running on the macOS host.
#
# Usage:
#   start-agent.sh [--rebuild] [--reload-allowlist]
#                  [--backend=ollama|omlx]
#                  [--disable-search]
#                  [--memory=VALUE] [--cpus=N]
#                  [--git-name=NAME] [--git-email=EMAIL]
#                  [--plan-model=MODEL] [--exec-model=MODEL] [--small-model=MODEL]
#                  [project-dir]

set -euo pipefail

# ── args ──────────────────────────────────────────────────────────────────────
REBUILD=false
RELOAD_ALLOWLIST=false
RESEED_GLOBAL_CLAUDEMD=false
CLI_MEMORY=""
CLI_CPUS=""
CLI_GIT_NAME=""
CLI_GIT_EMAIL=""
CLI_BACKEND=""
CLI_DISABLE_SEARCH=""
CLI_PLAN_MODEL=""
CLI_EXEC_MODEL=""
CLI_SMALL_MODEL=""
POSITIONAL=()

usage() {
  cat <<'USAGE'
start-agent.sh — Colima-backed Claude Code + OpenCode dev container

USAGE:
  start-agent.sh [options] [project-dir]

OPTIONS:
  --rebuild              Remove image + container and recreate. With
                         confirmation, also delete and recreate the Colima VM.
  --reload-allowlist     Regenerate tinyproxy's filter file from
                         ~/.claude-agent/allowlist.txt and reload tinyproxy.
                         Fast path; does not restart the container.
  --reseed-global-claudemd  Overwrite ~/.claude-containers/shared/CLAUDE.md
                         with the repo template (default is seed-if-missing).
  --memory=VALUE         VM memory (e.g. 8, 8G, 8GB). Default: 8 GiB.
  --cpus=N               VM CPU count. Default: 6.
  --backend=BACKEND      Local inference backend: ollama (default) or omlx.
  --git-name=NAME        Git author/committer name inside the container.
  --git-email=EMAIL      Git author/committer email inside the container.
  --disable-search       Skip SearXNG and Vane containers (search is on by
                         default). Env: CLAUDE_AGENT_DISABLE_SEARCH=1
  --plan-model=MODEL     OpenCode model for plan-mode agent (agent.plan).
                         Bare model ID (e.g. gemma3:27b) or provider/model.
  --exec-model=MODEL     OpenCode model for execution/build agent (agent.build).
  --small-model=MODEL    OpenCode small model for lightweight tasks (small_model).
  -h, --help             Show this help.

ALLOWLIST:
  Edit  ~/.claude-agent/allowlist.txt  on the macOS host to change which
  domains the container can reach. One domain per line; '#' for comments;
  suffix match (github.com covers api.github.com). Apply changes with:
      start-agent.sh --reload-allowlist

ENVIRONMENT:
  CLAUDE_AGENT_MEMORY    Default VM memory (overridden by --memory).
  CLAUDE_AGENT_CPUS      Default VM CPU count (overridden by --cpus).
  CLAUDE_AGENT_BACKEND   Default backend (overridden by --backend).
  GIT_USER_NAME          Default git name (overridden by --git-name).
  GIT_USER_EMAIL         Default git email (overridden by --git-email).
  OMLX_API_KEY           API key for omlx (passed into the container when
                         --backend=omlx).
  CLAUDE_AGENT_DEFAULT_MODEL      Default OpenCode model (set via env var; no CLI flag).
  CLAUDE_AGENT_PLAN_MODEL         OpenCode plan-agent model (overridden by --plan-model).
  CLAUDE_AGENT_EXEC_MODEL         OpenCode exec/build-agent model (overridden by --exec-model).
  CLAUDE_AGENT_SMALL_MODEL        OpenCode small model (overridden by --small-model).
  CLAUDE_AGENT_DISABLE_SEARCH=1  Disable SearXNG and Vane (overridden by --disable-search).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild)           REBUILD=true ;;
    --reload-allowlist)  RELOAD_ALLOWLIST=true ;;
    --reseed-global-claudemd) RESEED_GLOBAL_CLAUDEMD=true ;;
    --memory=*)          CLI_MEMORY="${1#--memory=}" ;;
    --memory)            CLI_MEMORY="${2:?--memory requires a value}"; shift ;;
    --cpus=*)            CLI_CPUS="${1#--cpus=}" ;;
    --cpus)              CLI_CPUS="${2:?--cpus requires a value}"; shift ;;
    --git-name=*)        CLI_GIT_NAME="${1#--git-name=}" ;;
    --git-name)          CLI_GIT_NAME="${2:?--git-name requires a value}"; shift ;;
    --git-email=*)       CLI_GIT_EMAIL="${1#--git-email=}" ;;
    --git-email)         CLI_GIT_EMAIL="${2:?--git-email requires a value}"; shift ;;
    --backend=*)         CLI_BACKEND="${1#--backend=}" ;;
    --backend)           CLI_BACKEND="${2:?--backend requires a value}"; shift ;;
    --enable-local-search) echo "warning: --enable-local-search is deprecated (search is now enabled by default). Use --disable-search to disable." >&2 ;;
    --disable-search)     CLI_DISABLE_SEARCH="true" ;;
    --plan-model=*)      CLI_PLAN_MODEL="${1#--plan-model=}" ;;
    --plan-model)        CLI_PLAN_MODEL="${2:?--plan-model requires a value}"; shift ;;
    --exec-model=*)      CLI_EXEC_MODEL="${1#--exec-model=}" ;;
    --exec-model)        CLI_EXEC_MODEL="${2:?--exec-model requires a value}"; shift ;;
    --small-model=*)     CLI_SMALL_MODEL="${1#--small-model=}" ;;
    --small-model)       CLI_SMALL_MODEL="${2:?--small-model requires a value}"; shift ;;
    -h|--help)           usage; exit 0 ;;
    *)                   POSITIONAL+=("$1") ;;
  esac
  shift
done

PROJECT_DIR="${POSITIONAL[0]:-$(pwd)}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Accept "8", "8G", "8GB" (any case); normalize to an integer GiB that
# `colima start --memory` expects.
normalize_gib() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  raw="${raw// /}"
  raw="${raw%gib}"
  raw="${raw%gb}"
  raw="${raw%g}"
  if [[ ! "$raw" =~ ^[0-9]+$ ]]; then
    echo "error: invalid memory value '$1' — use integer GiB (e.g. 8, 8G, 8GB)" >&2
    exit 1
  fi
  echo "$raw"
}

MEMORY_RAW="${CLI_MEMORY:-${CLAUDE_AGENT_MEMORY:-8}}"
CLAUDE_AGENT_MEMORY_GB="$(normalize_gib "$MEMORY_RAW")"
CLAUDE_AGENT_CPUS="${CLI_CPUS:-${CLAUDE_AGENT_CPUS:-6}}"
if [[ ! "$CLAUDE_AGENT_CPUS" =~ ^[0-9]+$ ]]; then
  echo "error: invalid --cpus value '$CLAUDE_AGENT_CPUS'" >&2
  exit 1
fi

GIT_USER_NAME="${CLI_GIT_NAME:-${GIT_USER_NAME:-Dev}}"
GIT_USER_EMAIL="${CLI_GIT_EMAIL:-${GIT_USER_EMAIL:-dev@localhost}}"

BACKEND="${CLI_BACKEND:-${CLAUDE_AGENT_BACKEND:-ollama}}"
case "$BACKEND" in
  ollama)
    INFERENCE_PORT=11434
    INFERENCE_LABEL="Ollama"
    ;;
  omlx)
    INFERENCE_PORT=8000
    INFERENCE_LABEL="omlx"
    ;;
  *)
    echo "error: unknown --backend value '$BACKEND'. Must be 'ollama' or 'omlx'." >&2
    exit 1
    ;;
esac

if [[ "$BACKEND" == "omlx" ]]; then
  OMLX_API_KEY="${OMLX_API_KEY:-}"
  if [[ -z "$OMLX_API_KEY" ]]; then
    echo "warning: OMLX_API_KEY not set. omlx requests from the container will fail if the server requires auth." >&2
  fi
fi

PLAN_MODEL="${CLI_PLAN_MODEL:-${CLAUDE_AGENT_PLAN_MODEL:-}}"
EXEC_MODEL="${CLI_EXEC_MODEL:-${CLAUDE_AGENT_EXEC_MODEL:-}}"
SMALL_MODEL="${CLI_SMALL_MODEL:-${CLAUDE_AGENT_SMALL_MODEL:-}}"

if [[ "${CLI_DISABLE_SEARCH:-}" == "true" || "${CLAUDE_AGENT_DISABLE_SEARCH:-}" =~ ^(1|true|yes)$ ]]; then
  LOCAL_SEARCH_ENABLED=false
else
  LOCAL_SEARCH_ENABLED=true
fi

# ── constants ────────────────────────────────────────────────────────────────
COLIMA_PROFILE="claude-agent"
CONTAINER_NAME="claude-agent"
IMAGE_TAG="claude-agent:latest"
IMAGE_STAMP="$HOME/.claude-agent-image-built"
DOCKERFILE_PATH="$(cd "$(dirname "$0")" && pwd)/dockerfiles/claude-agent.Dockerfile"
DOCKERFILE_DIR="$(dirname "$DOCKERFILE_PATH")"
CLAUDE_CONFIG_DIR="$HOME/.claude-containers/shared"
CLAUDE_JSON_FILE="$HOME/.claude-containers/claude.json"
OPENCODE_CONFIG_DIR="$HOME/.claude-agent/opencode-config"
OPENCODE_DATA_DIR="$HOME/.claude-agent/opencode-data"
ALLOWLIST_DIR="$HOME/.claude-agent"
ALLOWLIST_FILE="$ALLOWLIST_DIR/allowlist.txt"
TINYPROXY_PORT=8888
SEARXNG_CONTAINER="searxng"
SEARXNG_DIR="$HOME/.claude-agent/searxng"
SEARXNG_SETTINGS_FILE="$SEARXNG_DIR/settings.yml"
VANE_CONTAINER="vane"
VANE_DATA_DIR="$HOME/.claude-agent/vane-data"
VANE_PORT=3000
AGENT_NET_NAME="claude-agent-net"

# ── preflight ────────────────────────────────────────────────────────────────
if ! command -v colima &>/dev/null; then
  echo "error: 'colima' not found. Install with: brew install colima docker" >&2
  exit 1
fi
if ! command -v docker &>/dev/null; then
  echo "error: 'docker' not found. Install with: brew install colima docker" >&2
  exit 1
fi
if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "error: project dir '$PROJECT_DIR' does not exist." >&2
  exit 1
fi
if [[ ! -f "$DOCKERFILE_PATH" ]]; then
  echo "error: Dockerfile not found at $DOCKERFILE_PATH" >&2
  exit 1
fi

# ── seed allowlist (first run only) ──────────────────────────────────────────
mkdir -p "$ALLOWLIST_DIR"
if [[ ! -f "$ALLOWLIST_FILE" ]]; then
  cat > "$ALLOWLIST_FILE" <<'ALLOWLIST'
# start-agent allowlist — edit on the macOS host.
# Apply changes:  start-agent.sh --reload-allowlist
# Suffix match:   'github.com' also matches 'api.github.com'.
# Comments start with '#'.

# === Anthropic / AI coding agents ===
anthropic.com
claude.ai
claude.com
opencode.ai

# === Source control & code hosting ===
# Read-only hosts only. github.com / gitlab.com / bitbucket.org are
# intentionally omitted because the HTTP-proxy allowlist can't distinguish
# reads from writes (PRs, pushes, issue comments). Tarball/raw fetches go
# through codeload/githubusercontent. If you need `gh` or HTTPS push from
# inside the container, add the write host explicitly.
githubusercontent.com
githubassets.com
codeload.github.com
git-scm.com

# === Package registries ===
npmjs.org
npmjs.com
yarnpkg.com
pypi.org
pythonhosted.org
crates.io
rust-lang.org
rubygems.org
packagist.org
pkg.go.dev
proxy.golang.org
hex.pm

# === Container / image registries ===
# Omitted by default (docker.io / quay.io / ghcr.io) — writes (image push)
# aren't distinguishable from reads at the proxy. Add back if you pull
# images from inside the container.

# === OS package repos ===
debian.org
ubuntu.com
deb.nodesource.com
astral.sh

# === Model & dataset hubs ===
# huggingface.co omitted — supports model/dataset uploads. Add back if
# you need HF downloads from inside the container.
ollama.com

# === General web & reference ===
wikipedia.org
wikimedia.org
mozilla.org
developer.mozilla.org
stackoverflow.com
stackexchange.com
archive.org

# === Search engines ===
google.com
duckduckgo.com
bing.com
search.brave.com
api.qwant.com
# api.github.com — targeted entry for SearXNG's GitHub code-search engine.
# Does NOT enable github.com web writes (push, PR, issue comments).
api.github.com

# === Biomedical / life sciences ===
ncbi.nlm.nih.gov
nlm.nih.gov
nih.gov
europepmc.org
biorxiv.org
medrxiv.org
plos.org
biomedcentral.com
elifesciences.org
nature.com
cell.com
science.org
sciencemag.org

# === Physical sciences, engineering, math, CS ===
arxiv.org
semanticscholar.org
openalex.org
dblp.org
acm.org
ieee.org
aps.org
acs.org
rsc.org
iop.org
ams.org
paperswithcode.com

# === Social sciences, humanities, general journals ===
jstor.org
ssrn.com
crossref.org
doi.org
orcid.org
sciencedirect.com
springer.com
link.springer.com
springernature.com
wiley.com
onlinelibrary.wiley.com
tandfonline.com
sagepub.com
cambridge.org
oup.com
muse.jhu.edu

# === Data repositories & open science ===
# Omitted by default — zenodo/figshare/osf/dataverse/datadryad/kaggle all
# support authenticated uploads. Add back per-host if you need reads.

# === Government / statistical / standards ===
nasa.gov
cdc.gov
fda.gov
nsf.gov
census.gov
noaa.gov
usgs.gov
bls.gov
loc.gov
gov.uk
who.int
europa.eu

# === News & periodicals for research context ===
nytimes.com
economist.com
ft.com
reuters.com
apnews.com
bbc.com
bbc.co.uk
washingtonpost.com
theatlantic.com
newyorker.com
wsj.com
ALLOWLIST
  echo "==> Seeded allowlist at $ALLOWLIST_FILE"
fi

# Generate a tinyproxy filter file on the host from the allowlist.
# Each non-comment domain becomes an anchored regex: (^|\.)domain\.tld$
# which matches the domain itself and any subdomain.
generate_filter_file() {
  local out="$1"
  python3 - "$ALLOWLIST_FILE" > "$out" <<'PYEOF'
import re, sys
with open(sys.argv[1]) as f:
    for raw in f:
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        escaped = re.escape(line)
        print(f"(^|\\.){escaped}$")
PYEOF
}

# ── Colima VM bring-up ───────────────────────────────────────────────────────
colima_profile_running() {
  colima status -p "$COLIMA_PROFILE" 2>&1 | grep -q 'Running'
}

destroy_colima_vm() {
  echo "==> Destroying Colima VM '$COLIMA_PROFILE'"
  colima delete -p "$COLIMA_PROFILE" --force 2>/dev/null || true
}

start_colima_vm() {
  echo "==> Starting Colima VM '$COLIMA_PROFILE' ($CLAUDE_AGENT_MEMORY_GB GiB RAM, $CLAUDE_AGENT_CPUS CPUs)"
  colima start -p "$COLIMA_PROFILE" \
    --vm-type vz \
    --runtime docker \
    --cpu "$CLAUDE_AGENT_CPUS" \
    --memory "$CLAUDE_AGENT_MEMORY_GB" \
    --mount-type virtiofs \
    --network-address
}

# Run a command inside the Colima VM.
#
# SSH passes the remote command as a single string (the server re-tokenizes
# it), and additionally re-quotes whatever single string we hand it, so any
# attempt to pre-join with printf %q ends up double-quoted into a single
# literal word on the remote side. Bypass colima's argv handling entirely by
# piping the command line in via stdin — colima ssh hands stdin straight to
# the remote shell with no extra quoting.
vm_ssh() {
  local cmd="" a
  for a in "$@"; do
    cmd+=" $(printf '%q' "$a")"
  done
  vm_sh "$cmd"
}

# Variant that takes a complete shell command line as a single argument and
# runs it in the VM. Use when you want shell pipelines, redirections, or
# heredocs evaluated remotely.
vm_sh() {
  printf '%s\n' "$1" | colima ssh -p "$COLIMA_PROFILE" -- bash
}

# Copy a host-side file into the VM at $2 with mode $3 (default 644).
# colima ssh re-quotes argv, but the command itself has no shell
# metacharacters, and the file payload rides on stdin — so no quoting games.
vm_put_file() {
  local src="$1" dest="$2" mode="${3:-644}"
  colima ssh -p "$COLIMA_PROFILE" -- sudo tee "$dest" >/dev/null < "$src"
  colima ssh -p "$COLIMA_PROFILE" -- sudo chmod "$mode" "$dest"
}

# ── --rebuild: optionally destroy the Colima VM before starting it ───────────
# Container + image removal happens AFTER the VM is up so that `docker`
# actually has something to talk to; deleting the VM itself must happen BEFORE
# `colima start` so the start-up path recreates a clean VM.
if $REBUILD && ! $RELOAD_ALLOWLIST; then
  rm -f "$IMAGE_STAMP"
  echo
  read -r -p "Also delete and recreate the Colima VM '$COLIMA_PROFILE'? This is NOT reversible. [y/N] " confirm
  if [[ "${confirm:-}" =~ ^[Yy]$ ]]; then
    destroy_colima_vm
  else
    echo "==> Keeping Colima VM; only image/container will be rebuilt."
  fi
fi

# ── ensure the VM is running ─────────────────────────────────────────────────
if ! colima_profile_running; then
  start_colima_vm
else
  # Warn if the running VM sizing differs from what was requested.
  running_json=$(colima list -p "$COLIMA_PROFILE" --json 2>/dev/null || true)
  if [[ -n "$running_json" ]]; then
    running_cpu=$(printf '%s' "$running_json" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("cpus",""))' 2>/dev/null || echo "")
    running_mem=$(printf '%s' "$running_json" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());m=d.get("memory","");print(m)' 2>/dev/null || echo "")
    if [[ -n "$running_cpu" && "$running_cpu" != "$CLAUDE_AGENT_CPUS" ]]; then
      echo "warning: running VM has $running_cpu CPUs; requested $CLAUDE_AGENT_CPUS. Use --rebuild to resize." >&2
    fi
    if [[ -n "$running_mem" && "$running_mem" != *"${CLAUDE_AGENT_MEMORY_GB}"* ]]; then
      echo "warning: running VM memory is $running_mem; requested ${CLAUDE_AGENT_MEMORY_GB}GiB. Use --rebuild to resize." >&2
    fi
  fi
fi

# Point the local docker CLI at Colima's socket for this profile.
if ! docker context use "colima-$COLIMA_PROFILE" &>/dev/null; then
  echo "warning: could not switch docker context to colima-$COLIMA_PROFILE; assuming current context talks to the right daemon." >&2
fi

# Now that docker is reachable, honor --rebuild by removing the container and
# image from the VM's docker runtime. (The VM itself was handled earlier.)
if $REBUILD && ! $RELOAD_ALLOWLIST; then
  if docker container inspect "$CONTAINER_NAME" &>/dev/null; then
    echo "==> --rebuild: removing container '$CONTAINER_NAME'"
    docker rm -f "$CONTAINER_NAME" >/dev/null
  fi
  if $LOCAL_SEARCH_ENABLED && docker container inspect "$SEARXNG_CONTAINER" &>/dev/null; then
    echo "==> --rebuild: removing container '$SEARXNG_CONTAINER'"
    docker rm -f "$SEARXNG_CONTAINER" >/dev/null
  fi
  if $LOCAL_SEARCH_ENABLED && docker container inspect "$VANE_CONTAINER" &>/dev/null; then
    echo "==> --rebuild: removing container '$VANE_CONTAINER'"
    docker rm -f "$VANE_CONTAINER" >/dev/null
  fi
  if docker image inspect "$IMAGE_TAG" &>/dev/null; then
    echo "==> --rebuild: removing image '$IMAGE_TAG'"
    docker image rm "$IMAGE_TAG" >/dev/null
  fi
fi

# ── discover bridge IP, host IP, bridge CIDR inside the VM ───────────────────
BRIDGE_IP=$(vm_ssh docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null | tr -d '\r' || true)
if [[ -z "$BRIDGE_IP" ]]; then
  BRIDGE_IP="172.17.0.1"
  echo "warning: could not discover docker bridge gateway; falling back to $BRIDGE_IP" >&2
fi
BRIDGE_CIDR=$(vm_ssh docker network inspect bridge -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null | tr -d '\r' || true)
if [[ -z "$BRIDGE_CIDR" ]]; then
  BRIDGE_CIDR="172.17.0.0/16"
fi

# HOST_IP from inside the VM: default gateway under Colima's vmnet points at
# the macOS host when `--network-address` is set. Run `ip route` remotely and
# parse the output locally so we don't have to fight with quoting awk's $3
# through the SSH argv-join round trip.
HOST_IP=$(vm_ssh ip route show default 2>/dev/null | tr -d '\r' | awk '/^default/ {print $3; exit}' || true)
if [[ -z "$HOST_IP" ]]; then
  for candidate in host.lima.internal host.docker.internal; do
    hosts_line=$(vm_ssh getent hosts "$candidate" 2>/dev/null | tr -d '\r' || true)
    if [[ -n "$hosts_line" ]]; then
      HOST_IP=$(printf '%s\n' "$hosts_line" | awk '{print $1; exit}')
      [[ -n "$HOST_IP" ]] && break
    fi
  done
fi
if [[ -z "$HOST_IP" ]]; then
  echo "warning: could not determine the macOS host IP from inside the VM; local inference ($INFERENCE_LABEL) will not work." >&2
  HOST_IP="127.0.0.1"
fi

echo "==> VM network: bridge=$BRIDGE_IP cidr=$BRIDGE_CIDR host=$HOST_IP"

# ── user-defined network for inter-container DNS (agent + searxng) ────────────
# Docker embedded DNS resolves container names only on user-defined networks,
# not on the default bridge. claude-agent-net provides this without changing
# tinyproxy's Listen address — VM routing lets containers on this network reach
# $BRIDGE_IP:$TINYPROXY_PORT through the default bridge interface.
AGENT_NET_CIDR=""
if $LOCAL_SEARCH_ENABLED; then
  AGENT_NET_CIDR=$(vm_ssh docker network inspect "$AGENT_NET_NAME" -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null | tr -d '\r' || true)
  if [[ -z "$AGENT_NET_CIDR" ]]; then
    vm_ssh docker network create "$AGENT_NET_NAME" >/dev/null
    AGENT_NET_CIDR=$(vm_ssh docker network inspect "$AGENT_NET_NAME" -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null | tr -d '\r' || true)
  fi
  if [[ -z "$AGENT_NET_CIDR" ]]; then
    AGENT_NET_CIDR="172.20.0.0/24"
    echo "warning: could not discover $AGENT_NET_NAME CIDR; falling back to $AGENT_NET_CIDR" >&2
  fi
  echo "==> Agent network: $AGENT_NET_NAME cidr=$AGENT_NET_CIDR"
fi

# ── SearXNG settings.yml seed (first run only, local-search path) ────────────
if $LOCAL_SEARCH_ENABLED; then
  mkdir -p "$SEARXNG_DIR"
  if [[ ! -f "$SEARXNG_SETTINGS_FILE" ]]; then
    SECRET_KEY="$(openssl rand -hex 32)"
    cat > "$SEARXNG_SETTINGS_FILE" <<SXNG
use_default_settings:
  engines:
    keep_only:
      - google
      - bing
      - duckduckgo
      - brave
      - qwant
      - wikipedia
      - arxiv
      - github
      - stack overflow

server:
  secret_key: "$SECRET_KEY"
  base_url: "http://searxng:8080/"
  limiter: false

search:
  formats:
    - html
    - json

outgoing:
  proxies:
    all://: "http://$BRIDGE_IP:$TINYPROXY_PORT"
SXNG
    echo "==> Seeded $SEARXNG_SETTINGS_FILE (secret_key generated, proxy=$BRIDGE_IP:$TINYPROXY_PORT)"
  else
    # Drift check: the seed block only writes when the file is missing, so a
    # past run with a different proxy address (e.g. the old Squid port 3128)
    # can leave a stale `outgoing.proxies.all://` value behind. Detect and
    # rewrite just that one line; leave user customizations elsewhere alone.
    EXPECTED_PROXY="http://$BRIDGE_IP:$TINYPROXY_PORT"
    CURRENT_PROXY=$(awk '
      /^outgoing:/ { in_out = 1; next }
      in_out && /^[^[:space:]]/ { in_out = 0 }
      in_out && /all:\/\/:/ {
        match($0, /"[^"]+"/)
        if (RSTART) print substr($0, RSTART+1, RLENGTH-2)
        exit
      }
    ' "$SEARXNG_SETTINGS_FILE")
    if [[ -n "$CURRENT_PROXY" && "$CURRENT_PROXY" != "$EXPECTED_PROXY" ]]; then
      echo "==> SearXNG proxy drift: $CURRENT_PROXY → $EXPECTED_PROXY"
      sed -i.bak -E "s|(all://:[[:space:]]*)\"[^\"]+\"|\1\"$EXPECTED_PROXY\"|" \
        "$SEARXNG_SETTINGS_FILE"
      rm -f "$SEARXNG_SETTINGS_FILE.bak"
      SEARXNG_CONFIG_CHANGED=true
    fi
  fi
fi

# ── tinyproxy install in VM (idempotent) ─────────────────────────────────────
if ! vm_ssh sh -c 'command -v tinyproxy' >/dev/null 2>&1; then
  echo "==> Installing tinyproxy in Colima VM"
  vm_ssh sudo apt-get update -qq
  vm_ssh sudo apt-get install -y tinyproxy
fi

# Build tinyproxy config and filter on the host, then push into the VM.
TMP_WORK=$(mktemp -d)
trap 'rm -rf "$TMP_WORK"' EXIT

cat > "$TMP_WORK/tinyproxy.conf" <<CONF
User tinyproxy
Group tinyproxy
Port $TINYPROXY_PORT
Listen $BRIDGE_IP
Timeout 600
DefaultErrorFile "/usr/share/tinyproxy/default.html"
StatFile "/usr/share/tinyproxy/stats.html"
LogFile "/var/log/tinyproxy/tinyproxy.log"
LogLevel Info
MaxClients 100
FilterDefaultDeny Yes
Filter "/etc/tinyproxy/filter"
FilterExtended Yes
FilterURLs No
ConnectPort 443
ConnectPort 80
CONF

generate_filter_file "$TMP_WORK/filter"

# Ship both files directly into /etc/tinyproxy/ via `sudo tee` over colima ssh.
vm_put_file "$TMP_WORK/tinyproxy.conf" /etc/tinyproxy/tinyproxy.conf
vm_put_file "$TMP_WORK/filter"         /etc/tinyproxy/filter

# Enable and (re)start/reload tinyproxy. On first run, enable+start; otherwise
# reload to pick up filter changes without interrupting in-flight connections.
if $RELOAD_ALLOWLIST; then
  vm_ssh sudo systemctl reload tinyproxy 2>/dev/null || vm_ssh sudo systemctl restart tinyproxy
else
  vm_ssh sudo systemctl enable --now tinyproxy >/dev/null 2>&1 || true
  vm_ssh sudo systemctl restart tinyproxy
fi

# ── iptables rules in the VM (CLAUDE_AGENT child chain of DOCKER-USER) ───────
# All rules live in a dedicated CLAUDE_AGENT chain, which DOCKER-USER jumps to
# for bridge-sourced traffic. This gives atomic flush-and-repopulate without
# walking line numbers or matching rules by comment text.
cat > "$TMP_WORK/firewall-apply.sh" <<FWEOF
#!/bin/sh
set -e
BRIDGE_IP="$BRIDGE_IP"
BRIDGE_CIDR="$BRIDGE_CIDR"
AGENT_NET_CIDR="$AGENT_NET_CIDR"
HOST_IP="$HOST_IP"
TINYPROXY_PORT="$TINYPROXY_PORT"
INFERENCE_PORT="$INFERENCE_PORT"
LOCAL_SEARCH_ENABLED="$LOCAL_SEARCH_ENABLED"

# Ensure DOCKER-USER exists (docker creates it on fresh daemons, but be safe).
sudo iptables -N DOCKER-USER 2>/dev/null || true
sudo iptables -C FORWARD -j DOCKER-USER 2>/dev/null || sudo iptables -I FORWARD 1 -j DOCKER-USER

# Our own chain. Create if absent, then flush to start clean.
sudo iptables -N CLAUDE_AGENT 2>/dev/null || true
sudo iptables -F CLAUDE_AGENT

# Jump into our chain from DOCKER-USER for default bridge traffic (idempotent).
sudo iptables -C DOCKER-USER -s "\$BRIDGE_CIDR" -j CLAUDE_AGENT 2>/dev/null \
  || sudo iptables -I DOCKER-USER 1 -s "\$BRIDGE_CIDR" -j CLAUDE_AGENT

# Also jump for user-defined agent network traffic. Note: claude-agent-net →
# tinyproxy ($BRIDGE_IP:$TINYPROXY_PORT) does NOT go through FORWARD — $BRIDGE_IP
# is a local address on the VM's docker0 interface, so that traffic hits the INPUT
# chain instead and is not controlled here. This jump is load-bearing for the
# claude-agent → searxng:8080 intra-network path, which DOES traverse FORWARD.
if [ -n "\$AGENT_NET_CIDR" ]; then
  sudo iptables -C DOCKER-USER -s "\$AGENT_NET_CIDR" -j CLAUDE_AGENT 2>/dev/null \
    || sudo iptables -I DOCKER-USER 2 -s "\$AGENT_NET_CIDR" -j CLAUDE_AGENT
fi

# Populate rules in order.
sudo iptables -A CLAUDE_AGENT -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
sudo iptables -A CLAUDE_AGENT -d "\$BRIDGE_IP" -p tcp --dport "\$TINYPROXY_PORT" -j RETURN
sudo iptables -A CLAUDE_AGENT -d "\$HOST_IP"   -p tcp --dport "\$INFERENCE_PORT" -j RETURN
sudo iptables -A CLAUDE_AGENT -d "\$BRIDGE_IP" -p udp --dport 53 -j RETURN
sudo iptables -A CLAUDE_AGENT -d "\$BRIDGE_IP" -p tcp --dport 53 -j RETURN
# Allow inter-container traffic on port 8080 for claude-agent → searxng MCP.
# Both containers are on AGENT_NET_CIDR (claude-agent-net user-defined network).
# SearXNG → tinyproxy at BRIDGE_IP:8888 is already covered by the rule above.
if [ "\$LOCAL_SEARCH_ENABLED" = "true" ] && [ -n "\$AGENT_NET_CIDR" ]; then
  sudo iptables -A CLAUDE_AGENT -s "\$AGENT_NET_CIDR" -d "\$AGENT_NET_CIDR" -p tcp --dport 8080 -j RETURN
fi
sudo iptables -A CLAUDE_AGENT -j REJECT --reject-with icmp-admin-prohibited
FWEOF

echo "==> Applying firewall rules in VM"
colima ssh -p "$COLIMA_PROFILE" -- sudo sh < "$TMP_WORK/firewall-apply.sh"

# ── --reload-allowlist: fast-path exit ───────────────────────────────────────
if $RELOAD_ALLOWLIST; then
  entry_count=$(grep -cv -E '^\s*(#|$)' "$ALLOWLIST_FILE" || echo 0)
  echo "==> Allowlist reloaded ($entry_count entries)"
  rm -rf "$TMP_WORK"
  trap - EXIT
  exit 0
fi

# ── inference backend preflight (non-fatal) ──────────────────────────────────
echo "==> Probing $INFERENCE_LABEL at http://$HOST_IP:$INFERENCE_PORT from inside VM"
case "$BACKEND" in
  ollama)
    if ! vm_ssh curl -sf --max-time 3 "http://$HOST_IP:$INFERENCE_PORT/api/tags" >/dev/null 2>&1; then
      cat >&2 <<WARN
warning: Ollama not reachable at http://$HOST_IP:$INFERENCE_PORT from inside the
Colima VM. Ensure Ollama is running on the macOS host and bound to 0.0.0.0.
On the host, run once:
    launchctl setenv OLLAMA_HOST 0.0.0.0:$INFERENCE_PORT
and restart the Ollama app. Continuing without local inference.
WARN
    fi
    ;;
  omlx)
    OMLX_CURL_ARGS=(-sf --max-time 3)
    if [[ -n "${OMLX_API_KEY:-}" ]]; then
      OMLX_CURL_ARGS+=(-H "Authorization: Bearer $OMLX_API_KEY")
    fi
    if ! vm_ssh curl "${OMLX_CURL_ARGS[@]}" "http://$HOST_IP:$INFERENCE_PORT/v1/models" >/dev/null 2>&1; then
      cat >&2 <<WARN
warning: omlx not reachable at http://$HOST_IP:$INFERENCE_PORT from inside the
Colima VM. Ensure omlx is running on the host with:
    omlx serve --model-dir ~/models
or via: brew services start omlx
Continuing without local inference.
WARN
    fi
    ;;
esac

# ── build the image if missing ───────────────────────────────────────────────
if ! docker image inspect "$IMAGE_TAG" &>/dev/null; then
  echo "==> Building $IMAGE_TAG from $DOCKERFILE_PATH"
  # Build uses --network=host so RUN steps share the VM's network namespace
  # and bypass the DOCKER-USER bridge firewall. Without this, apt/curl/npm
  # would hit "no route to host" because the default-deny rule rejects
  # everything from the bridge CIDR except the tinyproxy allowlist path, and
  # legacy-builder build ARGs don't reliably forward HTTP_PROXY to every tool
  # (apt, for one, only consults lowercase http_proxy). Runtime containers
  # still attach to the bridge and are firewalled normally.
  docker build --network=host \
    -t "$IMAGE_TAG" -f "$DOCKERFILE_PATH" "$DOCKERFILE_DIR"
  date +%s > "$IMAGE_STAMP"
elif [[ -f "$IMAGE_STAMP" ]]; then
  BUILD_TIME=$(cat "$IMAGE_STAMP")
  NOW=$(date +%s)
  AGE_DAYS=$(( (NOW - BUILD_TIME) / 86400 ))
  if (( AGE_DAYS >= 30 )); then
    echo "==> Warning: $IMAGE_TAG is ${AGE_DAYS} days old. Run with --rebuild to refresh."
  fi
fi

# ── SearXNG container lifecycle ──────────────────────────────────────────────
if $LOCAL_SEARCH_ENABLED; then
  echo "==> Starting SearXNG container"
  if ! docker container inspect "$SEARXNG_CONTAINER" &>/dev/null; then
    docker run -d \
      --name "$SEARXNG_CONTAINER" \
      --network "$AGENT_NET_NAME" \
      -v "$SEARXNG_SETTINGS_FILE:/etc/searxng/settings.yml:ro" \
      docker.io/searxng/searxng >/dev/null
    echo "    searxng: created"
  elif [[ "${SEARXNG_CONFIG_CHANGED:-false}" == "true" ]]; then
    docker restart "$SEARXNG_CONTAINER" >/dev/null || true
    echo "    searxng: restarted (settings.yml drift fixed)"
  else
    docker start "$SEARXNG_CONTAINER" >/dev/null || true
    echo "    searxng: started (existing container)"
  fi
else
  if docker container inspect "$SEARXNG_CONTAINER" &>/dev/null; then
    echo "==> Removing SearXNG container (--disable-search set)"
    docker rm -f "$SEARXNG_CONTAINER" >/dev/null
  fi
fi

# ── Vane container lifecycle ─────────────────────────────────────────────────
if $LOCAL_SEARCH_ENABLED; then
  echo "==> Starting Vane container"
  mkdir -p "$VANE_DATA_DIR"
  if ! docker container inspect "$VANE_CONTAINER" &>/dev/null; then
    docker run -d \
      --name "$VANE_CONTAINER" \
      --network "$AGENT_NET_NAME" \
      --add-host=host.docker.internal:host-gateway \
      -p "$VANE_PORT:3000" \
      -e "SEARXNG_API_URL=http://searxng:8080" \
      -v "$VANE_DATA_DIR:/home/vane/data" \
      docker.io/itzcrazykns1337/vane:slim-latest >/dev/null
    echo "    vane: created (http://localhost:$VANE_PORT)"
    echo "    note: configure LLM at http://localhost:$VANE_PORT on first access"
  else
    docker start "$VANE_CONTAINER" >/dev/null || true
    echo "    vane: started (existing container)"
  fi
else
  if docker container inspect "$VANE_CONTAINER" &>/dev/null; then
    echo "==> Removing Vane container (--disable-search set)"
    docker rm -f "$VANE_CONTAINER" >/dev/null
  fi
fi

# ── host-side persistent state dirs ──────────────────────────────────────────
mkdir -p "$CLAUDE_CONFIG_DIR" "$OPENCODE_CONFIG_DIR" "$OPENCODE_DATA_DIR"
[[ -f "$CLAUDE_JSON_FILE" ]] || echo '{}' > "$CLAUDE_JSON_FILE"

# ── seed global container CLAUDE.md ──────────────────────────────────────────
# Claude Code auto-injects ~/.claude/CLAUDE.md into every session; the shared
# mount puts this file in scope for all containers. Seed-if-missing;
# --reseed-global-claudemd forces overwrite to pick up template updates.
GLOBAL_CLAUDEMD_FILE="$CLAUDE_CONFIG_DIR/CLAUDE.md"
GLOBAL_CLAUDEMD_TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/templates/global-claude.md"
if [[ -f "$GLOBAL_CLAUDEMD_TEMPLATE" ]]; then
  if $RESEED_GLOBAL_CLAUDEMD; then
    cp "$GLOBAL_CLAUDEMD_TEMPLATE" "$GLOBAL_CLAUDEMD_FILE"
    echo "==> Reseeded global CLAUDE.md from template"
  elif [[ ! -f "$GLOBAL_CLAUDEMD_FILE" ]]; then
    cp "$GLOBAL_CLAUDEMD_TEMPLATE" "$GLOBAL_CLAUDEMD_FILE"
    echo "==> Seeded global CLAUDE.md"
  else
    echo "==> Global CLAUDE.md already present, skipping"
  fi
else
  echo "==> Warning: $GLOBAL_CLAUDEMD_TEMPLATE not found; skipping global CLAUDE.md seed" >&2
fi

# ── seed OpenCode AGENTS.md from the same template ───────────────────────────
# OpenCode loads its system instructions via the `instructions` field in
# opencode.json (written below). The claude-dev exceptions at the end of the
# template don't apply inside claude-agent, so strip them when seeding here.
GLOBAL_AGENTSMD_FILE="$OPENCODE_CONFIG_DIR/AGENTS.md"
seed_agentsmd() {
  awk '/^## Differences in claude-dev/{exit} {print}' \
    "$GLOBAL_CLAUDEMD_TEMPLATE" > "$GLOBAL_AGENTSMD_FILE"
}
if [[ -f "$GLOBAL_CLAUDEMD_TEMPLATE" ]]; then
  if $RESEED_GLOBAL_CLAUDEMD; then
    seed_agentsmd
    echo "==> Reseeded OpenCode AGENTS.md from template"
  elif [[ ! -f "$GLOBAL_AGENTSMD_FILE" ]]; then
    seed_agentsmd
    echo "==> Seeded OpenCode AGENTS.md"
  else
    echo "==> OpenCode AGENTS.md already present, skipping"
  fi
fi

# ── inject global ~/.claude/settings.json ────────────────────────────────────
GLOBAL_SETTINGS_FILE="$CLAUDE_CONFIG_DIR/settings.json"
if [[ -f "$GLOBAL_SETTINGS_FILE" ]]; then
  python3 - "$GLOBAL_SETTINGS_FILE" << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
changed = False
if data.get('showThinkingSummaries') is not True:
    data['showThinkingSummaries'] = True
    changed = True
if data.get('coauthorTag') != 'none':
    data['coauthorTag'] = 'none'
    changed = True
if 'effortLevel' in data:
    del data['effortLevel']
    changed = True
if changed:
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
PYEOF
else
  echo '{"showThinkingSummaries": true, "coauthorTag": "none"}' > "$GLOBAL_SETTINGS_FILE"
fi

# ── inject OpenCode config (inference provider) ─────────────────────────────
OPENCODE_CONFIG_FILE="$OPENCODE_CONFIG_DIR/opencode.json"
python3 - \
  "$OPENCODE_CONFIG_FILE" \
  "$BACKEND" \
  "http://$HOST_IP:$INFERENCE_PORT/v1" \
  "http://127.0.0.1:$INFERENCE_PORT/v1,http://$HOST_IP:$INFERENCE_PORT/v1" \
  "${OMLX_API_KEY:-}" \
  "${CLAUDE_AGENT_DEFAULT_MODEL:-}" \
  "${PLAN_MODEL:-}" \
  "${EXEC_MODEL:-}" \
  "${SMALL_MODEL:-}" \
  "$LOCAL_SEARCH_ENABLED" \
  << 'PYEOF'
import json, os, sys, urllib.request, urllib.error
path        = sys.argv[1]
backend     = sys.argv[2]
runtime_url = sys.argv[3]   # what the container will hit (HOST_IP)
probe_urls  = [u for u in sys.argv[4].split(',') if u]
api_key     = sys.argv[5] if len(sys.argv) > 5 else ""
default_model_override = sys.argv[6] if len(sys.argv) > 6 else ""
plan_model  = sys.argv[7] if len(sys.argv) > 7 else ""
exec_model  = sys.argv[8] if len(sys.argv) > 8 else ""
small_model = sys.argv[9] if len(sys.argv) > 9 else ""
local_search_enabled = sys.argv[10].lower() in ("true", "1", "yes") if len(sys.argv) > 10 else False

if os.path.exists(path):
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = {}
else:
    data = {}

def discover_models():
    """Try each probe URL in order; return (models_dict, working_url) or ({}, None)."""
    last_err = None
    for base in probe_urls:
        try:
            if backend == "omlx":
                url = base + "/models"
                req = urllib.request.Request(url)
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
            elif backend == "ollama":
                url = base.replace("/v1", "/api/tags")
                req = urllib.request.Request(url)
            else:
                return {}, None
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = json.loads(resp.read())
                if backend == "omlx":
                    return ({m["id"]: {"name": m["id"]} for m in body.get("data", [])}, base)
                if backend == "ollama":
                    return ({m["name"]: {"name": m["name"]} for m in body.get("models", [])}, base)
        except Exception as e:
            last_err = e
            continue
    if last_err is not None:
        print(f"[opencode-config] discovery failed across {probe_urls}: {last_err}", file=sys.stderr)
    return {}, None

data.setdefault('$schema', 'https://opencode.ai/config.json')
providers = data.setdefault('provider', {})

if backend == "ollama":
    provider_key, provider_name = "ollama", "Ollama (host)"
elif backend == "omlx":
    provider_key, provider_name = "omlx", "omlx (host)"
else:
    provider_key = None

if provider_key:
    entry = providers.setdefault(provider_key, {
        "npm": "@ai-sdk/openai-compatible",
        "name": provider_name,
        "options": {},
        "models": {},
    })
    opts = entry.setdefault('options', {})
    opts['baseURL'] = runtime_url
    # @ai-sdk/openai-compatible requires a non-empty apiKey in some versions.
    # Ollama ignores the value; omlx uses it if the server enforces auth.
    if backend == "ollama":
        opts['apiKey'] = "ollama"
    elif backend == "omlx" and api_key:
        opts['apiKey'] = api_key
    elif backend == "omlx":
        opts.setdefault('apiKey', "omlx")

    discovered, working_url = discover_models()
    if discovered:
        # Always refresh on success — stale model lists are worse than useless.
        entry['models'] = discovered
        print(f"[opencode-config] discovered {len(discovered)} {backend} models via {working_url}", file=sys.stderr)
    else:
        print(f"[opencode-config] no {backend} models discovered; preserving existing models dict ({len(entry.get('models', {}))} entries)", file=sys.stderr)

    def qualify(m):
        return m if '/' in m else f"{provider_key}/{m}"

    # Default model: env override wins; otherwise reset to a local model whenever
    # the currently-persisted `model` isn't from our active local provider_key
    # (e.g. a stale cloud selection opencode saved on a prior run).
    if default_model_override:
        data['model'] = qualify(default_model_override)
    elif discovered and (not data.get('model') or data['model'].split('/')[0] != provider_key):
        data['model'] = qualify(next(iter(discovered)))

    if small_model:
        data['small_model'] = qualify(small_model)

    if plan_model:
        data.setdefault('agent', {}).setdefault('plan', {})['model'] = qualify(plan_model)
    if exec_model:
        data.setdefault('agent', {}).setdefault('build', {})['model'] = qualify(exec_model)

perms = data.setdefault('permission', {})
perms.setdefault('webfetch', 'allow')
if local_search_enabled:
    mcps = data.setdefault('mcp', {})
    mcps.setdefault('searxng', {
        'type': 'local',
        'command': ['/opt/searxng-mcp/venv/bin/python', '/opt/searxng-mcp/server.py'],
        'environment': {'SEARXNG_URL': 'http://searxng:8080'},
    })
    perms['websearch'] = 'allow'
else:
    perms.setdefault('websearch', 'deny')
    # Remove stale searxng MCP block if local search was previously enabled.
    data.get('mcp', {}).pop('searxng', None)
data['instructions'] = ['/root/.config/opencode/AGENTS.md']
data.setdefault('compaction', {})['auto'] = False
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')

# Emit a machine-friendly summary line for the shell wrapper.
count = len(providers.get(provider_key, {}).get('models', {})) if provider_key else 0
print(f"MODEL_COUNT={count}", file=sys.stderr)
PYEOF

# ── skills sync (new-container path only) ────────────────────────────────────
sync_skills() {
  local url="${CLAUDE_SKILLS_ARCHIVE_URL:-https://github.com/aryehj/start-claude/archive/refs/heads/main.tar.gz}"
  local dest="$CLAUDE_CONFIG_DIR/skills"
  echo "==> Syncing skills from upstream into $dest"
  mkdir -p "$dest"
  local tmp
  tmp=$(mktemp -d)
  if curl -fsSL "$url" -o "$tmp/archive.tar.gz" \
     && tar -xzf "$tmp/archive.tar.gz" -C "$tmp"; then
    local src
    src=$(find "$tmp" -maxdepth 2 -type d -name skills | head -n1)
    if [[ -n "$src" && -d "$src" ]]; then
      for skill_path in "$src"/*/; do
        [[ -d "$skill_path" ]] || continue
        local name
        name=$(basename "$skill_path")
        rm -rf "$dest/$name"
        cp -R "$skill_path" "$dest/$name"
        echo "    injected skill: $name"
      done
    else
      echo "    warning: no skills/ directory in upstream archive" >&2
    fi
  else
    echo "    warning: failed to fetch skills from $url (continuing)" >&2
  fi
  rm -rf "$tmp"
}

# ── container run / reattach ─────────────────────────────────────────────────
DOCKER_ENV_ARGS=(
  -e "TERM=${TERM:-xterm-256color}"
  -e "COLORTERM=${COLORTERM:-}"
  -e "TERM_PROGRAM=${TERM_PROGRAM:-}"
  -e "GIT_AUTHOR_NAME=$GIT_USER_NAME"
  -e "GIT_AUTHOR_EMAIL=$GIT_USER_EMAIL"
  -e "GIT_COMMITTER_NAME=$GIT_USER_NAME"
  -e "GIT_COMMITTER_EMAIL=$GIT_USER_EMAIL"
  -e "HTTPS_PROXY=http://$BRIDGE_IP:$TINYPROXY_PORT"
  -e "HTTP_PROXY=http://$BRIDGE_IP:$TINYPROXY_PORT"
  -e "NO_PROXY=localhost,127.0.0.1,$BRIDGE_IP,$HOST_IP,searxng"
  -e "NODE_USE_ENV_PROXY=1"
  -e "TMPDIR=/tmp"
)
case "$BACKEND" in
  ollama)
    DOCKER_ENV_ARGS+=(-e "OLLAMA_HOST=http://$HOST_IP:$INFERENCE_PORT")
    ;;
  omlx)
    DOCKER_ENV_ARGS+=(-e "OMLX_HOST=http://$HOST_IP:$INFERENCE_PORT")
    if [[ -n "${OMLX_API_KEY:-}" ]]; then
      DOCKER_ENV_ARGS+=(-e "OMLX_API_KEY=$OMLX_API_KEY")
    fi
    ;;
esac

attach_existing() {
  echo "==> Attaching to existing container '$CONTAINER_NAME'"
  docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP_WORK"
  trap - EXIT
  exec docker exec -it -w "$PROJECT_DIR" "${DOCKER_ENV_ARGS[@]}" "$CONTAINER_NAME" /bin/bash
}

if docker container inspect "$CONTAINER_NAME" &>/dev/null; then
  # Check that the existing mount matches $PROJECT_DIR. If not, recreate.
  existing_mount=$(docker container inspect -f '{{range .Mounts}}{{if eq .Destination "'"$PROJECT_DIR"'"}}{{.Source}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
  if [[ "$existing_mount" == "$PROJECT_DIR" ]]; then
    # In-use detection: if another interactive shell is already attached, refuse.
    running_state=$(docker container inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")
    if [[ "$running_state" == "true" ]]; then
      if docker top "$CONTAINER_NAME" 2>/dev/null | grep -q '/bin/bash'; then
        existing_proj=$(docker container inspect -f '{{range .Mounts}}{{if ne .Destination "/root/.claude"}}{{if ne .Destination "/root/.claude.json"}}{{if ne .Destination "/root/.config/opencode"}}{{if ne .Destination "/root/.local/share/opencode"}}{{.Destination}} {{end}}{{end}}{{end}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null | awk '{print $1}')
        echo "warning: container '$CONTAINER_NAME' appears to already have an interactive session (project: $existing_proj). Attaching anyway; two shells sharing state may be confusing." >&2
      fi
    fi
    attach_existing
  else
    echo "==> Project dir changed; recreating container"
    docker rm -f "$CONTAINER_NAME" >/dev/null
  fi
fi

# Fresh container: sync skills first.
sync_skills

# ── inject project settings ───────────────────────────────────────────────────
# Disable bubblewrap sandbox — it cannot run inside a Docker/Colima container
# without elevated privileges. The VM-level tinyproxy + iptables chain is the
# real security boundary here.
PROJECT_SETTINGS_FILE="$PROJECT_DIR/.claude/settings.local.json"
if [[ -f "$PROJECT_SETTINGS_FILE" ]]; then
  python3 - "$PROJECT_SETTINGS_FILE" << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
changed = False
if 'theme' not in data:
    data['theme'] = 'light'
    changed = True
    print(f"==> Added theme:light to {path}")
sb = data.setdefault('sandbox', {})
if sb.get('enabled') is not False:
    sb['enabled'] = False
    changed = True
    print(f"==> Set sandbox.enabled=false in {path}")
for key in ('failIfUnavailable', 'allowUnsandboxedCommands'):
    if key in sb:
        del sb[key]
        changed = True
        print(f"==> Removed sandbox.{key} from {path}")
if changed:
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
PYEOF
else
  mkdir -p "$PROJECT_DIR/.claude"
  cat > "$PROJECT_SETTINGS_FILE" << 'JSONEOF'
{
  "theme": "light",
  "sandbox": {
    "enabled": false
  }
}
JSONEOF
  echo "==> Created $PROJECT_SETTINGS_FILE"
fi

NETWORK_ARGS=()
if $LOCAL_SEARCH_ENABLED; then
  NETWORK_ARGS=(--network "$AGENT_NET_NAME")
fi

echo "==> Creating container '$CONTAINER_NAME'"
echo "    project : $PROJECT_DIR  →  $PROJECT_DIR"
echo "    proxy   : http://$BRIDGE_IP:$TINYPROXY_PORT  (allowlist: $ALLOWLIST_FILE)"
echo "    inference: $INFERENCE_LABEL at http://$HOST_IP:$INFERENCE_PORT"
$LOCAL_SEARCH_ENABLED && echo "    search  : SearXNG on $AGENT_NET_NAME"
$LOCAL_SEARCH_ENABLED && echo "    vane    : http://localhost:$VANE_PORT (AI research UI)"

rm -rf "$TMP_WORK"
trap - EXIT
exec docker run \
  --name "$CONTAINER_NAME" \
  -it \
  --memory "${CLAUDE_AGENT_MEMORY_GB}g" \
  --cpus "$CLAUDE_AGENT_CPUS" \
  --add-host=host.docker.internal:host-gateway \
  ${NETWORK_ARGS[@]+"${NETWORK_ARGS[@]}"} \
  -v "$PROJECT_DIR:$PROJECT_DIR" \
  -v "$CLAUDE_CONFIG_DIR:/root/.claude" \
  -v "$CLAUDE_JSON_FILE:/root/.claude.json" \
  -v "$OPENCODE_CONFIG_DIR:/root/.config/opencode" \
  -v "$OPENCODE_DATA_DIR:/root/.local/share/opencode" \
  -w "$PROJECT_DIR" \
  "${DOCKER_ENV_ARGS[@]}" \
  "$IMAGE_TAG" \
  bash
