#!/usr/bin/env bash
# start-agent.sh — spin up a Colima-backed agent container for a project.
#
# Sibling to start-claude.sh. Uses one shared Colima VM (profile:
# claude-agent), one shared docker container (claude-agent), with both the
# Claude Code and OpenCode CLIs installed. Enforces a VM-level egress
# allowlist via tinyproxy + iptables DOCKER-USER rules, and routes local
# inference to an Ollama instance running on the macOS host.
#
# Usage:
#   start-agent.sh [--rebuild] [--reload-allowlist]
#                  [--memory=VALUE] [--cpus=N]
#                  [--git-name=NAME] [--git-email=EMAIL]
#                  [project-dir]

set -euo pipefail

# ── args ──────────────────────────────────────────────────────────────────────
REBUILD=false
RELOAD_ALLOWLIST=false
CLI_MEMORY=""
CLI_CPUS=""
CLI_GIT_NAME=""
CLI_GIT_EMAIL=""
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
  --memory=VALUE         VM memory (e.g. 8, 8G, 8GB). Default: 8 GiB.
  --cpus=N               VM CPU count. Default: 6.
  --git-name=NAME        Git author/committer name inside the container.
  --git-email=EMAIL      Git author/committer email inside the container.
  -h, --help             Show this help.

ALLOWLIST:
  Edit  ~/.claude-agent/allowlist.txt  on the macOS host to change which
  domains the container can reach. One domain per line; '#' for comments;
  suffix match (github.com covers api.github.com). Apply changes with:
      start-agent.sh --reload-allowlist

ENVIRONMENT:
  CLAUDE_AGENT_MEMORY    Default VM memory (overridden by --memory).
  CLAUDE_AGENT_CPUS      Default VM CPU count (overridden by --cpus).
  GIT_USER_NAME          Default git name (overridden by --git-name).
  GIT_USER_EMAIL         Default git email (overridden by --git-email).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild)           REBUILD=true ;;
    --reload-allowlist)  RELOAD_ALLOWLIST=true ;;
    --memory=*)          CLI_MEMORY="${1#--memory=}" ;;
    --memory)            CLI_MEMORY="${2:?--memory requires a value}"; shift ;;
    --cpus=*)            CLI_CPUS="${1#--cpus=}" ;;
    --cpus)              CLI_CPUS="${2:?--cpus requires a value}"; shift ;;
    --git-name=*)        CLI_GIT_NAME="${1#--git-name=}" ;;
    --git-name)          CLI_GIT_NAME="${2:?--git-name requires a value}"; shift ;;
    --git-email=*)       CLI_GIT_EMAIL="${1#--git-email=}" ;;
    --git-email)         CLI_GIT_EMAIL="${2:?--git-email requires a value}"; shift ;;
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
OLLAMA_PORT=11434

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
opencode.ai

# === Source control & code hosting ===
github.com
githubusercontent.com
githubassets.com
codeload.github.com
gitlab.com
bitbucket.org
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
docker.io
docker.com
quay.io
ghcr.io

# === OS package repos ===
debian.org
ubuntu.com
deb.nodesource.com
astral.sh

# === Model & dataset hubs ===
huggingface.co
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
zenodo.org
figshare.com
osf.io
dataverse.org
datadryad.org
kaggle.com

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
  colima status -p "$COLIMA_PROFILE" 2>/dev/null | grep -q 'Running'
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
# it), so naively forwarding argv breaks any argument that contains spaces
# or shell metacharacters — in particular Go templates like
# '{{(index .IPAM.Config 0).Gateway}}'. Shell-quote each arg with printf %q
# before joining so the remote shell reconstructs the intended argv.
vm_ssh() {
  local cmd="" a
  for a in "$@"; do
    cmd+=" $(printf '%q' "$a")"
  done
  colima ssh -p "$COLIMA_PROFILE" -- "$cmd"
}

# Variant that takes a complete shell command line as a single argument and
# runs it through `sh -c` in the VM. Use when you want shell pipelines,
# redirections, or heredocs evaluated remotely.
vm_sh() {
  colima ssh -p "$COLIMA_PROFILE" -- "sh -c $(printf '%q' "$1")"
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
  echo "warning: could not determine the macOS host IP from inside the VM; Ollama connectivity will not work." >&2
  HOST_IP="127.0.0.1"
fi

echo "==> VM network: bridge=$BRIDGE_IP cidr=$BRIDGE_CIDR host=$HOST_IP"

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
LogLevel Warning
MaxClients 100
FilterDefaultDeny Yes
Filter "/etc/tinyproxy/filter"
FilterExtended Yes
FilterURLs No
ConnectPort 443
ConnectPort 80
CONF

generate_filter_file "$TMP_WORK/filter"

# Ship both files into the VM in one pass. `colima ssh` is a bit awkward with
# stdin piping; tar + base64 is the reliable portable trick.
(cd "$TMP_WORK" && tar -cf - tinyproxy.conf filter) | base64 | \
  vm_ssh sudo sh -c 'base64 -d | tar -xf - -C /tmp && install -m 644 /tmp/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf && install -m 644 /tmp/filter /etc/tinyproxy/filter && rm -f /tmp/tinyproxy.conf /tmp/filter'

# Enable and (re)start/reload tinyproxy. On first run, enable+start; otherwise
# reload to pick up filter changes without interrupting in-flight connections.
if $RELOAD_ALLOWLIST; then
  vm_ssh sudo systemctl reload tinyproxy 2>/dev/null || vm_ssh sudo systemctl restart tinyproxy
else
  vm_ssh sudo systemctl enable --now tinyproxy >/dev/null 2>&1 || true
  vm_ssh sudo systemctl restart tinyproxy
fi

# ── iptables rules in the VM (DOCKER-USER) ───────────────────────────────────
cat > "$TMP_WORK/firewall-apply.sh" <<FWEOF
#!/bin/sh
set -e
BRIDGE_IP="$BRIDGE_IP"
BRIDGE_CIDR="$BRIDGE_CIDR"
HOST_IP="$HOST_IP"
TINYPROXY_PORT="$TINYPROXY_PORT"
OLLAMA_PORT="$OLLAMA_PORT"

# Ensure DOCKER-USER exists (docker creates it on fresh daemons, but be safe).
sudo iptables -N DOCKER-USER 2>/dev/null || true
sudo iptables -C FORWARD -j DOCKER-USER 2>/dev/null || sudo iptables -I FORWARD 1 -j DOCKER-USER

# Flush our previous rules (identified by comment). Walk the chain by line
# number in reverse so deletions don't shift indices underneath us.
sudo iptables -S DOCKER-USER | awk '/--comment claude-agent/{print NR-1}' | sort -rn | while read -r i; do
  [ -z "\$i" ] || sudo iptables -D DOCKER-USER "\$i" 2>/dev/null || true
done
# Fallback cleanup by explicit rule spec (in case the awk approach misses).
while sudo iptables -S DOCKER-USER 2>/dev/null | grep -q 'claude-agent'; do
  rule=\$(sudo iptables -S DOCKER-USER | grep -m1 'claude-agent' | sed 's/^-A/-D/')
  [ -z "\$rule" ] && break
  # shellcheck disable=SC2086
  sudo iptables \$rule || break
done

# Established/related first.
sudo iptables -I DOCKER-USER 1 -s "\$BRIDGE_CIDR" \
  -m conntrack --ctstate ESTABLISHED,RELATED \
  -m comment --comment claude-agent -j RETURN

# Container -> in-VM tinyproxy.
sudo iptables -I DOCKER-USER 2 -s "\$BRIDGE_CIDR" -d "\$BRIDGE_IP" \
  -p tcp --dport "\$TINYPROXY_PORT" \
  -m comment --comment claude-agent -j RETURN

# Container -> macOS host Ollama.
sudo iptables -I DOCKER-USER 3 -s "\$BRIDGE_CIDR" -d "\$HOST_IP" \
  -p tcp --dport "\$OLLAMA_PORT" \
  -m comment --comment claude-agent -j RETURN

# Container -> in-VM DNS (dockerd's embedded resolver lives on 127.0.0.11,
# which the container reaches via its own netns, but some name-lookup paths
# use the bridge gateway as resolver. Allow UDP/TCP 53 to the bridge IP.)
sudo iptables -I DOCKER-USER 4 -s "\$BRIDGE_CIDR" -d "\$BRIDGE_IP" \
  -p udp --dport 53 \
  -m comment --comment claude-agent -j RETURN
sudo iptables -I DOCKER-USER 5 -s "\$BRIDGE_CIDR" -d "\$BRIDGE_IP" \
  -p tcp --dport 53 \
  -m comment --comment claude-agent -j RETURN

# Default-deny everything else from the bridge.
sudo iptables -A DOCKER-USER -s "\$BRIDGE_CIDR" \
  -m comment --comment claude-agent \
  -j REJECT --reject-with icmp-admin-prohibited
FWEOF
chmod +x "$TMP_WORK/firewall-apply.sh"

echo "==> Applying firewall rules in VM"
base64 < "$TMP_WORK/firewall-apply.sh" | \
  vm_ssh sh -c 'base64 -d > /tmp/firewall-apply.sh && sh /tmp/firewall-apply.sh && rm -f /tmp/firewall-apply.sh'

# ── --reload-allowlist: fast-path exit ───────────────────────────────────────
if $RELOAD_ALLOWLIST; then
  entry_count=$(grep -cv -E '^\s*(#|$)' "$ALLOWLIST_FILE" || echo 0)
  echo "==> Allowlist reloaded ($entry_count entries)"
  rm -rf "$TMP_WORK"
  trap - EXIT
  exit 0
fi

# ── Ollama preflight (non-fatal) ─────────────────────────────────────────────
echo "==> Probing Ollama at http://$HOST_IP:$OLLAMA_PORT from inside VM"
if ! vm_ssh curl -sf --max-time 3 "http://$HOST_IP:$OLLAMA_PORT/api/tags" >/dev/null 2>&1; then
  cat >&2 <<WARN
warning: Ollama not reachable at http://$HOST_IP:$OLLAMA_PORT from inside the
Colima VM. Ensure Ollama is running on the macOS host and bound to 0.0.0.0.
On the host, run once:
    launchctl setenv OLLAMA_HOST 0.0.0.0:$OLLAMA_PORT
and restart the Ollama app. Continuing without local inference.
WARN
fi

# ── build the image if missing ───────────────────────────────────────────────
if ! docker image inspect "$IMAGE_TAG" &>/dev/null; then
  echo "==> Building $IMAGE_TAG from $DOCKERFILE_PATH"
  docker build -t "$IMAGE_TAG" -f "$DOCKERFILE_PATH" "$DOCKERFILE_DIR"
  date +%s > "$IMAGE_STAMP"
elif [[ -f "$IMAGE_STAMP" ]]; then
  BUILD_TIME=$(cat "$IMAGE_STAMP")
  NOW=$(date +%s)
  AGE_DAYS=$(( (NOW - BUILD_TIME) / 86400 ))
  if (( AGE_DAYS >= 30 )); then
    echo "==> Warning: $IMAGE_TAG is ${AGE_DAYS} days old. Run with --rebuild to refresh."
  fi
fi

# ── host-side persistent state dirs ──────────────────────────────────────────
mkdir -p "$CLAUDE_CONFIG_DIR" "$OPENCODE_CONFIG_DIR" "$OPENCODE_DATA_DIR"
[[ -f "$CLAUDE_JSON_FILE" ]] || echo '{}' > "$CLAUDE_JSON_FILE"

# ── inject project .claude/settings.local.json ───────────────────────────────
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
if isinstance(data.get('sandbox'), bool):
    data['sandbox'] = {"enabled": True, "autoAllowBashIfSandboxed": True}
    changed = True
sb = data.setdefault('sandbox', {})
fs = sb.setdefault('filesystem', {})
aw = fs.setdefault('allowWrite', [])
for p in ['/tmp/uv-cache', '$TMPDIR/uv-cache', '/tmp/.venv', '$TMPDIR/.venv']:
    if p not in aw:
        aw.append(p)
        changed = True
if sb.get('failIfUnavailable') is not True:
    sb['failIfUnavailable'] = True
    changed = True
if sb.get('allowUnsandboxedCommands') is not False:
    sb['allowUnsandboxedCommands'] = False
    changed = True
if changed:
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"==> Migrated {path}")
PYEOF
else
  mkdir -p "$PROJECT_DIR/.claude"
  cat > "$PROJECT_SETTINGS_FILE" << 'JSONEOF'
{
  "theme": "light",
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": true,
    "failIfUnavailable": true,
    "allowUnsandboxedCommands": false,
    "filesystem": {
      "allowWrite": ["/tmp/uv-cache", "$TMPDIR/uv-cache", "/tmp/.venv", "$TMPDIR/.venv"]
    }
  }
}
JSONEOF
  echo "==> Created $PROJECT_SETTINGS_FILE"
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
if data.get('effortLevel') != 'medium':
    data['effortLevel'] = 'medium'
    changed = True
if changed:
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
PYEOF
else
  echo '{"showThinkingSummaries": true, "coauthorTag": "none", "effortLevel": "medium"}' > "$GLOBAL_SETTINGS_FILE"
fi

# ── inject OpenCode config (Ollama provider) ─────────────────────────────────
OPENCODE_CONFIG_FILE="$OPENCODE_CONFIG_DIR/opencode.json"
python3 - "$OPENCODE_CONFIG_FILE" "http://$HOST_IP:$OLLAMA_PORT/v1" << 'PYEOF'
import json, os, sys
path, base_url = sys.argv[1], sys.argv[2]
if os.path.exists(path):
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = {}
else:
    data = {}
data.setdefault('$schema', 'https://opencode.ai/config.json')
providers = data.setdefault('provider', {})
ollama = providers.setdefault('ollama', {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Ollama (host)",
    "options": {},
    "models": {},
})
ollama.setdefault('options', {})['baseURL'] = base_url
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
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
  -e "CLAUDE_CODE_DISABLE_1M_CONTEXT=1"
  -e "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1"
  -e "GIT_AUTHOR_NAME=$GIT_USER_NAME"
  -e "GIT_AUTHOR_EMAIL=$GIT_USER_EMAIL"
  -e "GIT_COMMITTER_NAME=$GIT_USER_NAME"
  -e "GIT_COMMITTER_EMAIL=$GIT_USER_EMAIL"
  -e "OLLAMA_HOST=http://$HOST_IP:$OLLAMA_PORT"
  -e "HTTPS_PROXY=http://$BRIDGE_IP:$TINYPROXY_PORT"
  -e "HTTP_PROXY=http://$BRIDGE_IP:$TINYPROXY_PORT"
  -e "NO_PROXY=localhost,127.0.0.1,$BRIDGE_IP,$HOST_IP"
)

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

echo "==> Creating container '$CONTAINER_NAME'"
echo "    project : $PROJECT_DIR  →  $PROJECT_DIR"
echo "    proxy   : http://$BRIDGE_IP:$TINYPROXY_PORT  (allowlist: $ALLOWLIST_FILE)"
echo "    ollama  : http://$HOST_IP:$OLLAMA_PORT"

rm -rf "$TMP_WORK"
trap - EXIT
exec docker run \
  --name "$CONTAINER_NAME" \
  -it \
  --memory "${CLAUDE_AGENT_MEMORY_GB}g" \
  --cpus "$CLAUDE_AGENT_CPUS" \
  --add-host=host.docker.internal:host-gateway \
  -v "$PROJECT_DIR:$PROJECT_DIR" \
  -v "$CLAUDE_CONFIG_DIR:/root/.claude" \
  -v "$CLAUDE_JSON_FILE:/root/.claude.json" \
  -v "$OPENCODE_CONFIG_DIR:/root/.config/opencode" \
  -v "$OPENCODE_DATA_DIR:/root/.local/share/opencode" \
  -w "$PROJECT_DIR" \
  "${DOCKER_ENV_ARGS[@]}" \
  "$IMAGE_TAG" \
  bash
