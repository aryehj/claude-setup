# TL; DR
The author is a dilletente who starts a lot of projects, but is slow to learn syntax and commands. Therefore, the author automated most of the creation of safe-ish containerized workspaces for LLM-aided development in a given working directory. 

# start-claude.sh

Spins up an isolated [Apple Containers](https://developer.apple.com/documentation/virtualization)
dev environment for a project, pre-configured for Claude Code.

## Requirements

- macOS with Apple Containers installed (`container` CLI on PATH)
- Apple Silicon Mac
- Kata kernel installed: `container system kernel set --recommended`
- Rosetta 2 installed: `softwareupdate --install-rosetta --agree-to-license`

## Setup

```bash
# Optional: Make it available everywhere
echo 'export PATH="/Path/To/start-claude:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## Usage

```bash
# From inside your project directory
start-claude.sh

# Explicit project dir
start-claude.sh ~/projects/my-app

# Explicit project dir + container name
start-claude.sh ~/projects/my-app my-app

# Custom git identity for commits
start-claude.sh --git-name "Jane" --git-email "jane@example.com"

# Equals form also works
start-claude.sh --git-name=Jane --git-email=jane@example.com ~/projects/my-app
```

The script starts the container service automatically if it isn't already
running, so no manual `container system start` is needed beforehand.

On first run, the script pulls `debian:bookworm-slim`, installs tools inside a
temporary container, then exports it as `claude-dev:latest` — takes a few
minutes. Subsequent projects reuse the cached image and start instantly.

If you run the command again for a project that already has a container, it
re-attaches to the existing one rather than creating a new one.

## What's inside the container

| Tool | Notes |
|------|-------|
| `claude` | Claude Code CLI (installed via official installer) |
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
| Your project dir | Same path (e.g. `/Users/you/projects/my-app`) |
| `~/.claude-containers/shared/` | `/root/.claude` |
| `~/.claude-containers/claude.json` | `/root/.claude.json` |

**Authentication note:** `~/.claude` and `~/.claude.json` are shared across all
containers via the host volume mounts above. Run `claude login` once in any
container; all containers share the session, and auth survives `--rebuild`.

## Rebuilding the image

To rebuild from scratch (e.g. after editing the setup script in
`start-claude.sh`), use the `--rebuild` flag:

```bash
start-claude.sh --rebuild
```

This removes the existing container for the project (if any) and the
`claude-dev:latest` image, then rebuilds from scratch.

## Included skills

The `skills/` directory holds reusable Claude Code skills. Whenever
`start-claude.sh` creates a new container, it downloads the upstream repo
archive and injects each skill directory into the shared
`~/.claude-containers/shared/skills/` mount, replacing any existing directory
with the same name. Skills you've added locally under other names are left
alone.

Override the source with `CLAUDE_SKILLS_ARCHIVE_URL` (point it at a fork, a
branch tarball, or any `*.tar.gz` whose top-level has a `skills/` directory).
If the fetch fails, the warning is printed and the container starts anyway.

Invoke a synced skill inside any Claude Code session with its slash name, e.g.
`/cleanup`.

| Skill | What it does |
|-------|-------------|
| `cleanup` | Post-implementation housekeeping — updates CLAUDE.md, README.md, appends ADR.md, and renames completed plan files |
| `plan` | Explores the codebase and writes implementation plans to `plans/` as markdown files targeted at Claude Sonnet |

## Environment variable reference

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_CONTAINER_IMAGE` | `debian:bookworm-slim` | Override the base image |
| `CLAUDE_SKILLS_ARCHIVE_URL` | upstream `main` tarball | Override the source archive for skills sync |
| `GIT_USER_NAME` | `Dev` | Git author/committer name (overridden by `--git-name` flag) |
| `GIT_USER_EMAIL` | `dev@localhost` | Git author/committer email (overridden by `--git-email` flag) |
| `UV_CACHE_DIR` | `${TMPDIR:-/tmp}/uv-cache` | UV cache location (resolved dynamically at shell startup; both `/tmp/uv-cache` and `$TMPDIR/uv-cache` are in the sandbox's `filesystem.allowWrite`) |

---

# start-agent.sh

Sibling script to `start-claude.sh`. Instead of one Apple Containers microVM
per project, it runs a single shared [Colima](https://github.com/abiosoft/colima)
VM with a single shared docker container that includes both the **Claude Code**
and **OpenCode** CLIs, and enforces a network egress allowlist at the VM level
that the in-container LLM cannot modify. Local inference is routed to an
[Ollama](https://ollama.com) instance running on the macOS host.

Use `start-agent.sh` when you want:

- Both agents (Claude Code + OpenCode) in the same environment
- A hard, LLM-uneditable egress allowlist (tinyproxy + iptables in the VM)
- Local 30B-class model inference via host Ollama
- A single VM to manage rather than one per project

Use `start-claude.sh` when you want per-project microVM isolation via Apple
Containers with no Colima, no docker, and no shared VM.

## Requirements

- macOS with Colima and docker installed: `brew install colima docker`
- Ollama on the host (optional, for local inference): `brew install ollama`
- For Ollama: bind it to all interfaces so the VM can reach it:
  ```bash
  launchctl setenv OLLAMA_HOST 0.0.0.0:11434
  # then restart the Ollama app
  ```

## Usage

```bash
# From inside your project directory
start-agent.sh

# Override VM sizing (defaults: 8 GiB / 6 CPUs)
start-agent.sh --memory=12G --cpus=8

# Custom git identity for commits
start-agent.sh --git-name "Jane" --git-email jane@example.com

# Rebuild image + container (prompts before deleting the Colima VM)
start-agent.sh --rebuild

# Apply edits to the allowlist without touching the running container
start-agent.sh --reload-allowlist
```

First run brings up the Colima VM (`claude-agent` profile), installs
`tinyproxy` inside it, builds `claude-agent:latest` from
`dockerfiles/claude-agent.Dockerfile`, seeds the allowlist, applies iptables
rules, and drops you into a bash shell inside the container at `$(pwd)`.

Subsequent runs from the same directory reattach in a few seconds. Running
from a different directory recreates the container with the new mount.

## What's inside

| Tool | Notes |
|------|-------|
| `claude` | Claude Code CLI |
| `opencode` | [OpenCode](https://opencode.ai) CLI (installed via `opencode-ai` npm) |
| `uv` / `uvx` | Python package manager |
| `node` / `npm` | Node.js LTS |
| `git`, `ripgrep`, `fd`, `jq` | Dev tooling |

## Mounts

| Host | Container |
|------|-----------|
| Your project dir | Same path |
| `~/.claude-containers/shared/` | `/root/.claude` |
| `~/.claude-containers/claude.json` | `/root/.claude.json` |
| `~/.claude-agent/opencode-config/` | `/root/.config/opencode` |
| `~/.claude-agent/opencode-data/` | `/root/.local/share/opencode` |

`~/.claude-containers/shared/` and `claude.json` are deliberately shared with
`start-claude.sh`, so auth and skills persist across both scripts. Run only
one at a time.

## Egress allowlist

Everything outbound from the container is denied by default. Three egress
paths are open:

1. `HTTP(S)_PROXY` → in-VM tinyproxy, which enforces a regex filter generated
   from a human-editable domain allowlist.
2. `OLLAMA_HOST` → the macOS host on port `11434`.
3. DNS to the docker bridge gateway.

The enforcement lives in the `DOCKER-USER` iptables chain inside the Colima
VM. The container has no `CAP_NET_ADMIN` and cannot touch the rules even if
fully compromised. See `ADR.md` §ADR-010 for the threat model.

### Editing the allowlist

```bash
# On the macOS host — one domain per line; '#' for comments
$EDITOR ~/.claude-agent/allowlist.txt

# Apply changes (~2s, no container restart)
start-agent.sh --reload-allowlist
```

Suffix matching applies — `github.com` covers `api.github.com`,
`codeload.github.com`, etc. The file is seeded on first run with a permissive
dev/research list (Anthropic, GitHub, package registries, major scholarly
publishers, etc.). Prune it to match your actual usage.

## Using Ollama from inside the container

Inside the container:

```bash
echo $OLLAMA_HOST              # http://<host-ip>:11434
curl -s $OLLAMA_HOST/api/tags  # lists models
```

OpenCode is pre-configured with an `ollama` provider entry in
`~/.config/opencode/opencode.json` pointing at the host Ollama's
OpenAI-compatible endpoint. Edit the same file to add model entries:

```json
{
  "provider": {
    "ollama": {
      "models": {
        "qwen2.5-coder:32b": { "name": "Qwen2.5 Coder 32B" }
      }
    }
  }
}
```

## Environment variable reference (start-agent-specific)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MEMORY` | `8` | VM memory in GiB (overridden by `--memory`) |
| `CLAUDE_AGENT_CPUS` | `6` | VM CPU count (overridden by `--cpus`) |
| `GIT_USER_NAME` / `GIT_USER_EMAIL` | `Dev` / `dev@localhost` | Git identity (overridden by `--git-name` / `--git-email`) |
| `CLAUDE_SKILLS_ARCHIVE_URL` | upstream `main` tarball | Override skills source archive |
