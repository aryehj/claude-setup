# new-project.sh

Spins up an isolated [Apple Containers](https://developer.apple.com/documentation/virtualization)
dev environment for a project, pre-configured for Claude Code.

## Requirements

- macOS with Apple Containers installed (`container` CLI on PATH)
- Apple Silicon Mac

## Setup

```bash
# Make it available everywhere
echo 'export PATH="/Users/Shared/claude-setup:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## Usage

```bash
# From inside your project directory
new-project.sh

# Explicit project dir
new-project.sh ~/projects/my-app

# Explicit project dir + container name
new-project.sh ~/projects/my-app my-app
```

On first run, the script builds a Docker image (`claude-dev:latest`) — this
takes a minute. Subsequent projects reuse the cached image and start instantly.

If you run the command again for a project that already has a container, it
re-attaches to the existing one rather than creating a new one.

## What's inside the container

| Tool | Notes |
|------|-------|
| `claude` | Claude Code CLI |
| `uv` / `uvx` | Python package manager |
| `node` / `npm` | Node.js LTS |
| `git` | Latest from apt |
| `ripgrep` | `rg` |
| `fd` | `fd-find` |
| `jq` | JSON CLI |
| `curl` / `wget` | |
| `python3` | System Python + pip |
| `build-essential` | gcc, make, etc. |

## Mounts

| Host | Container |
|------|-----------|
| Your project dir | `/workspace` |
| `~/.claude` | `/root/.claude` |

`~/.claude` is shared across all containers — global memory, settings, and
sessions stay in sync with your host Claude Code installation.

## Firewall

The container runs with a default-deny egress firewall (ufw). Allowed outbound:

- DNS, HTTP, HTTPS — package managers, Anthropic API, search
- SSH (22) — git over SSH
- git:// (9418)
- GitHub IP ranges

### Local LLM server

To reach a local LLM server (Ollama, LM Studio, llama.cpp, LocalAI) running on
your Mac, two things are required:

1. **Host:** configure your LLM server to bind to `0.0.0.0`, not `127.0.0.1`:
   - Ollama: `OLLAMA_HOST=0.0.0.0 ollama serve`
   - LM Studio: Settings → Local Server → enable "serve on local network"
   - llama.cpp: `--host 0.0.0.0`

2. **Container:** uncomment the three lines at the bottom of `setup_firewall()`
   in `new-project.sh` (around line 148).

## Rebuilding the image

After editing the Dockerfile or entrypoint in `new-project.sh`:

```bash
container image rm claude-dev:latest
new-project.sh  # rebuilds automatically
```

## Environment variable reference

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_CONTAINER_IMAGE` | `ghcr.io/astral-sh/uv:bookworm` | Override the base image |
| `LOCAL_LLM_PORT` | `11434` | Port for local LLM server (when enabled) |
