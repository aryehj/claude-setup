# CLAUDE.md

This repo contains tooling for spinning up isolated Claude Code dev containers
using Apple Containers. One script, one container per project.

## Layout

```
new-project.sh   — main script; builds image, creates and attaches container
ROADMAP.md       — planned work
README.md        — usage reference
CLAUDE.md        — this file
```

## What the script does

`new-project.sh` builds a Debian bookworm-slim image on first run (cached
after that), then creates and attaches a named container with:

- The project directory mounted at `/workspace`
- `~/.claude` mounted at `/root/.claude` (global memory, settings, sessions)
- ufw configured inside the VM: deny all, whitelist DNS/HTTP/HTTPS/SSH/git
- Node LTS, Claude Code CLI, uv/uvx, git, ripgrep, fd, jq

If the named container already exists, it just starts and re-attaches it.

## Key decisions

**Single shared image, per-project containers.** The image (`claude-dev:latest`)
is built once and reused. Each project gets its own named container so state
(installed packages, history) is isolated between projects.

**ufw inside the VM, not Docker-style network modes.** Apple Containers doesn't
expose the same network control surface as Docker. ufw runs in the entrypoint
and is the primary egress control. A host-level `pf` anchor is planned as a
harder outer boundary (see ROADMAP.md).

**`~/.claude` is bind-mounted, not copied.** This keeps global memory and
settings in sync with the host and avoids divergence across containers.

## Making changes

The Dockerfile and entrypoint script are embedded as heredocs in
`new-project.sh`. Edit them there. After changing the Dockerfile, delete the
cached image so it rebuilds:

```bash
container image rm claude-dev:latest
```

## Firewall rules

Allowed outbound (ufw, inside container):

| Port | Proto | Purpose |
|------|-------|---------|
| 53 | UDP+TCP | DNS |
| 80 | TCP | HTTP (apt, redirects) |
| 443 | TCP | HTTPS (Anthropic API, npm, PyPI, uv, GitHub, search) |
| 22 | TCP | SSH (git over SSH) |
| 9418 | TCP | git:// protocol |

GitHub CIDRs are also explicitly allowed. See the `setup_firewall()` function
in `new-project.sh` for the full list.

To allow a local LLM server (Ollama, LM Studio, etc.), uncomment the three
lines in `setup_firewall()` near the bottom of that function.
