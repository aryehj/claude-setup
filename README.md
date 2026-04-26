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

# Overwrite ~/.claude-containers/shared/CLAUDE.md with the current template
start-claude.sh --reseed-global-claudemd
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

## Global container CLAUDE.md

On first run, the script copies `templates/global-claude.md` from the repo
into `~/.claude-containers/shared/CLAUDE.md`. Claude Code auto-injects that
file into every session running inside any container, giving the model shared
context about the environment (path layout, `$TMPDIR`, sandbox mounts, etc.)
regardless of the project it's opened in. Your edits are preserved across
subsequent runs. Pass `--reseed-global-claudemd` to overwrite with the current
template.

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
| `plan` | Explores the codebase and writes implementation plans to `plans/` as markdown files (runs on Opus) |
| `implement` | Executes the active phase of a plan file, with task tracking and checkpoint commits |

## Environment variable reference

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_CONTAINER_IMAGE` | `debian:bookworm-slim` | Override the base image |
| `CLAUDE_CONTAINER_MEMORY` | `4G` | Per-container memory limit passed to `container run --memory` |
| `CLAUDE_CONTAINER_CPUS` | `4` | Per-container CPU count passed to `container run --cpus` |
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
[Ollama](https://ollama.com) or [omlx](https://github.com/jundot/omlx) instance
running on the macOS host.

Use `start-agent.sh` when you want:

- Both agents (Claude Code + OpenCode) in the same environment
- A hard, LLM-uneditable egress allowlist (tinyproxy + iptables in the VM)
- Local 30B-class model inference via host Ollama or omlx
- A single VM to manage rather than one per project

Use `start-claude.sh` when you want per-project microVM isolation via Apple
Containers with no Colima, no docker, and no shared VM.

## Requirements

- macOS with Colima and docker installed: `brew install colima docker`
- A local inference server on the host (optional):
  - **Ollama** (default): `brew install ollama`, then bind to all interfaces:
    ```bash
    launchctl setenv OLLAMA_HOST 0.0.0.0:11434
    # then restart the Ollama app
    ```
  - **omlx** (alternative): an MLX-based server with API-key auth:
    ```bash
    brew install omlx
    export OMLX_API_KEY=your-secret-key
    omlx serve --model-dir ~/models --api-key "$OMLX_API_KEY"
    ```
    omlx's API-key auth eliminates the need for a host-side pf firewall.

## Usage

```bash
# From inside your project directory
start-agent.sh

# Override VM sizing (defaults: 8 GiB / 6 CPUs)
start-agent.sh --memory=12G --cpus=8

# Custom git identity for commits
start-agent.sh --git-name "Jane" --git-email jane@example.com

# With omlx instead of Ollama
export OMLX_API_KEY=your-secret-key
start-agent.sh --backend=omlx

# Rebuild image + container (prompts before deleting the Colima VM)
start-agent.sh --rebuild

# Apply edits to the allowlist without touching the running container
start-agent.sh --reload-allowlist

# Disable SearXNG + Vane (search and Vane run by default; skip them with this flag)
start-agent.sh --disable-search

# Set OpenCode models per mode (or export CLAUDE_AGENT_PLAN_MODEL /
# CLAUDE_AGENT_EXEC_MODEL / CLAUDE_AGENT_SMALL_MODEL to persist across runs)
start-agent.sh --plan-model=gemma3:27b --exec-model=qwen2.5-coder:32b --small-model=qwen2.5-coder:7b

# Set the OpenCode default model (no CLI flag; env var only)
CLAUDE_AGENT_DEFAULT_MODEL=ollama/qwen2.5-coder:32b start-agent.sh

# Overwrite ~/.claude-containers/shared/CLAUDE.md AND
# ~/.claude-agent/opencode-config/AGENTS.md with the repo template
start-agent.sh --reseed-global-claudemd
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

## Global container CLAUDE.md

On first run, `start-agent.sh` copies `templates/global-claude.md` from the
repo into `~/.claude-containers/shared/CLAUDE.md`. Claude Code auto-injects
that file into every session, giving the model shared context about the
environment — path layout, proxy allowlist, local-inference host, `$TMPDIR`
conventions — regardless of the project it's opened in. The file is shared
with `start-claude.sh` via the same mount, so editing it once applies to both.
Your edits are preserved across subsequent runs. Pass `--reseed-global-claudemd`
to overwrite with the current template.

The same template is seeded into `~/.claude-agent/opencode-config/AGENTS.md`
(mounted at `/root/.config/opencode/AGENTS.md`) and wired into OpenCode via
the `instructions` field in `opencode.json`, so OpenCode picks up the same
environment context. The `claude-dev` exceptions block at the end of the
template is stripped on the OpenCode copy since it doesn't apply inside
`claude-agent`. `--reseed-global-claudemd` reseeds this file too.

## Egress allowlist

Everything outbound from the container is denied by default. Three egress
paths are open:

1. `HTTP(S)_PROXY` → in-VM tinyproxy, which enforces a regex filter generated
   from a human-editable domain allowlist.
2. Inference server → the macOS host on the backend's port (`11434` for
   Ollama, `8000` for omlx).
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
dev/research list (Anthropic, package registries, major scholarly publishers,
etc.). Prune it to match your actual usage.

The seed intentionally **omits write-capable hosts** that can't be split from
their read surface at the HTTP-proxy layer: `github.com`, `gitlab.com`,
`bitbucket.org`, `huggingface.co`, container registries (`docker.io`,
`quay.io`, `ghcr.io`), and dataset-upload hubs (`zenodo`, `figshare`,
`kaggle`, `osf`, `dataverse`, `datadryad`). Code reads over tarball/raw still
work via `codeload.github.com` + `githubusercontent.com`. Add the write hosts
back explicitly if your workflow requires `gh`, HTTPS push, image push, or
dataset upload from inside the container.

**Existing users:** these defaults only apply to newly-seeded allowlists. If
`~/.claude-agent/allowlist.txt` already exists, hand-remove the write hosts
above (or delete the file to re-seed) and run
`start-agent.sh --reload-allowlist`.

### Verifying the egress allowlist

Six smoke tests exercise the full enforcement path. Run them from inside the
container (the first five) plus one reload from the macOS host.

**1. Bridge default-deny.** A direct request bypassing the proxy must be
rejected by the `DOCKER-USER` REJECT rule.

```bash
curl --noproxy '*' -sS --max-time 5 https://example.com
# expected: curl: (7) Failed to connect … Couldn't connect to server (fails fast)
```

**2. Allowlisted host via proxy.** A host in the allowlist must reach the
internet through tinyproxy.

```bash
curl -sS --max-time 10 https://api.github.com/zen
# expected: a short aphorism string from GitHub
```

**3. Denied host via proxy.** A host *not* in the allowlist must be rejected
by tinyproxy's filter with a 403 on the CONNECT tunnel.

```bash
curl -sS --max-time 10 https://example.com
# expected: curl: (56) CONNECT tunnel failed, response 403
```

**4. Inference server carve-out.** The host's inference endpoint must be
reachable via the dedicated iptables rule, bypassing the proxy.

```bash
# Ollama (default):
curl -sS --max-time 5 "$OLLAMA_HOST/api/tags"
# expected: JSON {"models":[…]}  (or a fast connection-refused if Ollama is stopped)

# omlx (--backend=omlx):
curl -sS --max-time 5 -H "Authorization: Bearer $OMLX_API_KEY" "$OMLX_HOST/v1/models"
# expected: JSON {"data":[…]}  (or connection-refused if omlx is stopped)
```

**5. Runtime env sanity.** Proxy and inference vars must be wired into the
container from process birth.

```bash
env | grep -iE 'proxy|ollama|omlx'
# Ollama: HTTP_PROXY, HTTPS_PROXY, NO_PROXY, OLLAMA_HOST all set
# omlx:   HTTP_PROXY, HTTPS_PROXY, NO_PROXY, OMLX_HOST, OMLX_API_KEY all set
```

**6. Allowlist hot-reload.** The host-side fast path must update the filter
without restarting the container. From the macOS host:

```bash
echo 'example.com' >> ~/.claude-agent/allowlist.txt
start-agent.sh --reload-allowlist
```

Then re-run test 3 in the container. `example.com` should now return
`HTTP/1.0 200 Connection established` (tinyproxy's CONNECT ack). Remove the
line and reload again to restore the default allowlist.

## Using the local inference server from inside the container

### Ollama (default)

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

### omlx (`--backend=omlx`)

```bash
echo $OMLX_HOST                # http://<host-ip>:8000
curl -s -H "Authorization: Bearer $OMLX_API_KEY" $OMLX_HOST/v1/models
```

OpenCode is pre-configured with an `omlx` provider entry. Add model
entries the same way:

```json
{
  "provider": {
    "omlx": {
      "models": {
        "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit": { "name": "Qwen2.5 Coder 32B" }
      }
    }
  }
}
```

## Local websearch (SearXNG) and Vane

SearXNG and Vane run **by default** alongside `claude-agent`. Pass
`--disable-search` (or set `CLAUDE_AGENT_DISABLE_SEARCH=1`) to skip both.

**SearXNG** is wired into OpenCode as a local `websearch` MCP tool. It fans
out to search engines through the same tinyproxy allowlist, so all engine
traffic is governed by `~/.claude-agent/allowlist.txt` — no third-party search
gateway. The SearXNG config (including a generated `secret_key`) is seeded on
first run at `~/.claude-agent/searxng/settings.yml` and survives `--rebuild`.
To reset it, delete that directory and re-run.

Enabled engines by default: Google, Bing, DuckDuckGo, Brave, Qwant, Wikipedia,
arXiv, GitHub code search, Stack Exchange. Add an engine by editing
`settings.yml` **and** `allowlist.txt` — both files must change, by design.

**Vane** (formerly Perplexica) is an AI-powered research UI at
`http://localhost:3000`. It uses SearXNG as its search backend. On first
access, configure the LLM endpoint via the web UI settings screen:
- Ollama: `http://host.docker.internal:11434`
- omlx: `http://host.docker.internal:8000/v1`

This config persists in `~/.claude-agent/vane-data/` and survives `--rebuild`.

See `ADR.md` §ADR-014 for the threat model and design rationale.

---

# research.py

Spins up a dedicated Colima VM (`research` profile) with an isolated egress
firewall and two containers: **SearXNG** (meta-search) and **Vane** (AI
research UI at `http://localhost:3000`). State lives in `~/.research/`.

## Requirements

- macOS with Colima and docker installed
- Local inference server optional (Ollama or omlx), same as `start-agent.sh`

## Usage

```bash
./research.py                          # bring up the environment
./research.py --reload-denylist        # recompose denylist from local files (no network)
./research.py --refresh-denylist       # re-fetch upstream feeds, then reload
./research.py --reseed-denylist        # overwrite sources/additions templates from repo
./research.py --rebuild                # recreate containers (prompts for VM deletion)
./research.py --backend=omlx           # use omlx instead of Ollama
```

On first run, seeds `~/.research/denylist-sources.txt` and `denylist-additions.txt`
from `templates/`. Edit the on-disk files and run `--reload-denylist` to apply
changes. To pick up upstream template updates, run `--reseed-denylist`.

**Existing users:** if you ran `research.py` before the denylist migration, you
have a `~/.research/allowlist.txt`. On next launch `research.py` will print the
required steps and exit:

```bash
rm -rf ~/.research/
./research.py --rebuild
```

## Egress denylist

research.py uses a **denylist** (default-allow) so Vane can scrape arbitrary
search-result URLs. The composed denylist is:

    (cached upstream feeds ∪ denylist-additions.txt) − denylist-overrides.txt

All three files live in `~/.research/` on the macOS host. Egress is enforced by
**Squid** + the iptables RESEARCH chain inside the Colima VM. Squid listens on
port 8888 and performs O(1) hash-table domain lookups — supporting million-entry
denylists without OOM. (start-agent.sh uses tinyproxy for its ~280-entry allowlist;
the asymmetry is intentional — see ADR-021.)

### Threat model

**Primary motivation: research quality.** The upstream hagezi feeds (`multi.pro`,
`fake`, `tif`) block misinformation sites, content farms, AI SEO slop, and
malicious infrastructure. This is load-bearing for Vane's usefulness — unfiltered
search results degrade research quality faster than they create security risk.

**Secondary motivation: exfil hygiene.** `denylist-additions.txt` blocks
legitimate-but-weaponizable services that upstream feeds won't cover: anonymous
paste/upload sites, webhook capture endpoints, reverse tunnels, messaging APIs,
code-hosting write paths. This limits what a prompt-injection payload in a
search result could reach.

**Acknowledged limitation:** an adversary who controls their own domain (or
registers a fresh one) bypasses both layers. Human supervision of Vane is the
actual exfil control, not the proxy. See ADR-023 for the full threat-model
framing.

### Feed contents and refresh cadence

| Feed | Purpose | Refresh cadence |
|------|---------|-----------------|
| `multi.pro` | Broad coverage — malware, tracking, content farms, AI slop | Monthly |
| `fake` | Misinformation and propaganda sites | Monthly |
| `tif` | Active threat intel — entries rotate as threats are taken down | Daily or weekly |

The `tif` feed degrades fastest when stale. Run `--refresh-denylist` weekly at
minimum; daily is ideal if you use research.py regularly.

### Editing the denylist

To update the denylist without restarting containers:

```bash
$EDITOR ~/.research/denylist-additions.txt   # add domains to block
$EDITOR ~/.research/denylist-overrides.txt   # or remove false positives
./research.py --reload-denylist
```

To refresh upstream feeds and reload:

```bash
./research.py --refresh-denylist
```

To pick up template updates after `git pull`:

```bash
./research.py --reseed-denylist --reload-denylist
```

## Environment variable reference (research.py)

| Variable | Default | Description |
|----------|---------|-------------|
| `RESEARCH_BACKEND` | `ollama` | Inference backend: `ollama` or `omlx` (overridden by `--backend`) |
| `RESEARCH_MEMORY` | `2` | VM memory in GiB (overridden by `--memory`) |
| `RESEARCH_CPUS` | `2` | VM CPU count (overridden by `--cpus`) |
| `OMLX_API_KEY` | *(unset)* | API key for omlx |

---

## Environment variable reference (start-agent-specific)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_BACKEND` | `ollama` | Inference backend: `ollama` or `omlx` (overridden by `--backend`) |
| `CLAUDE_AGENT_MEMORY` | `8` | VM memory in GiB (overridden by `--memory`) |
| `CLAUDE_AGENT_CPUS` | `6` | VM CPU count (overridden by `--cpus`) |
| `OMLX_API_KEY` | *(unset)* | API key for omlx; passed into the container when `--backend=omlx` |
| `CLAUDE_AGENT_DISABLE_SEARCH` | *(unset)* | Set to `1` to disable SearXNG and Vane (overridden by `--disable-search`) |
| `CLAUDE_AGENT_DEFAULT_MODEL` | *(unset)* | Default OpenCode model written to `opencode.json` (env var only — no CLI flag). Use `provider/model` or a bare ID that matches the active provider |
| `CLAUDE_AGENT_PLAN_MODEL` | *(unset)* | OpenCode model for plan-mode agent (overridden by `--plan-model`) |
| `CLAUDE_AGENT_EXEC_MODEL` | *(unset)* | OpenCode model for execution/build agent (overridden by `--exec-model`) |
| `CLAUDE_AGENT_SMALL_MODEL` | *(unset)* | OpenCode small model (overridden by `--small-model`) |
| `GIT_USER_NAME` / `GIT_USER_EMAIL` | `Dev` / `dev@localhost` | Git identity (overridden by `--git-name` / `--git-email`) |
| `CLAUDE_SKILLS_ARCHIVE_URL` | upstream `main` tarball | Override skills source archive |
