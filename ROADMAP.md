# new-project.sh — Roadmap

## Done

- **Base container** — Debian bookworm-slim with git, curl, ripgrep, fd, jq, build tools
- **Node.js LTS + Claude Code CLI** — `claude` available on PATH inside container
- **uv + uvx** — installed via official installer, symlinked to `/usr/local/bin`
- **Volume mounts** — project dir → `/workspace`, `~/.claude` → `/root/.claude`
- **Idempotent runs** — re-attaches to existing container instead of recreating
- **Internal firewall (ufw)** — deny all in/out by default, whitelist:
  - DNS (53 UDP/TCP)
  - HTTP/HTTPS (80, 443) — apt, npm, PyPI, uv, Anthropic API, search
  - SSH out (22) — git over SSH
  - git:// protocol (9418)
  - GitHub published CIDRs
- **Local LLM hook** — commented-out ufw rule + instructions for reaching Ollama/LM Studio/llama.cpp on the host

---

## To Do

### Host-level firewall via macOS `pf`

The ufw rules run inside the container VM — a root process inside the container
could modify or disable them. A stronger boundary is enforcing the same rules
from the host using macOS's `pf` (packet filter), which the container cannot
touch.

**How it works:**

Apple Containers gives each container an IP in a subnet (default
`192.168.64.0/24`). Traffic egresses through a virtual interface on the host.
`pf` can filter on that subnet before packets ever leave the machine.

Rather than editing `/etc/pf.conf` directly, the right pattern is a dedicated
**anchor** — a named, independently loadable ruleset that hangs off the main
config. macOS already uses this mechanism for its own rules (`com.apple.*`
anchors handle internet sharing, the app firewall, VPN, etc.).

Benefits of an anchor over editing `pf.conf` directly:

- Reload just your rules without touching Apple's: `pfctl -a claude-containers -f /etc/pf.anchors/claude-containers`
- Temporarily lift restrictions without disabling pf: `pfctl -a claude-containers -F rules`
- Survives macOS updates that rewrite `/etc/pf.conf`

**Planned anchor file** (`/etc/pf.anchors/claude-containers`):

```
CONTAINER_NET = "192.168.64.0/24"

block out proto { tcp udp } from $CONTAINER_NET to any

pass out proto { tcp udp } from $CONTAINER_NET to any port 53    # DNS
pass out proto tcp          from $CONTAINER_NET to any port 80    # HTTP
pass out proto tcp          from $CONTAINER_NET to any port 443   # HTTPS
pass out proto tcp          from $CONTAINER_NET to any port 22    # SSH/git
pass out proto tcp          from $CONTAINER_NET to any port 9418  # git://

# Local LLM (uncomment + set host IP)
# pass out proto tcp from $CONTAINER_NET to <HOST_IP> port 11434
```

**Outstanding work:**

- Confirm the virtual interface name and subnet by running `ifconfig` with a
  live container (interface is created dynamically; likely `vmnet*` or `feth*`)
- Confirm default subnet (`container system property get network.subnet`)
- Write a companion `setup-host-firewall.sh` that installs the anchor, appends
  the two lines to `/etc/pf.conf`, and enables pf — requires one-time `sudo`
- Document how to keep GitHub CIDRs in sync between the ufw rules (entrypoint)
  and the pf anchor

**Security posture with both layers:**

| Layer | Enforced by | Bypassable from inside container? |
|---|---|---|
| ufw | container (entrypoint) | Yes — root inside can modify |
| pf anchor | macOS host | No |

Goal is both running in lockstep: ufw as documentation of intent + first line,
pf as the hard outer limit.
