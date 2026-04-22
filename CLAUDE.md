# CLAUDE.md

This repo contains tooling for spinning up isolated Claude Code dev containers
using Apple Containers. One script, one container per project.

## Layout

```
start-claude.sh              — Apple Containers path; per-project microVM with Claude Code
start-agent.sh               — Colima path; shared VM + container with Claude Code + OpenCode + VM-level egress allowlist
dockerfiles/                 — Dockerfiles built by start-agent.sh (claude-agent.Dockerfile)
skills/                      — reusable Claude Code skills (back up of ~/.claude/skills/)
plans/                       — implementation plans written by /plan skill
ROADMAP.md                   — planned work
README.md                    — usage reference
ADR.md                       — architecture decision records
CLAUDE.md                    — this file
```

## What the script does

`start-claude.sh` sets up a `claude-dev:latest` image on first run (cached after
that), then creates and attaches a named container with:

- The project directory mounted at its host path (not `/workspace`)
- Node LTS, Claude Code CLI (via official installer), uv/uvx, git, ripgrep, fd, jq
- bubblewrap, socat, libseccomp2/dev, `@anthropic-ai/sandbox-runtime` (Claude Code sandbox dependencies)

It also starts the container service automatically (`container system start`) so
the script works even if the service isn't already running.

If the named container already exists, it just starts and re-attaches it.

## Key decisions

**Single shared image, per-project containers.** The image (`claude-dev:latest`)
is built once and reused. Each project gets its own named container so state
(installed packages, history) is isolated between projects.

**Setup runs inline, image built via `container build`.** The script runs setup
commands inside a temporary `debian:bookworm-slim` container, exports the
filesystem with `container export --output`, then builds `claude-dev:latest`
using `container build` with a `FROM scratch + ADD rootfs.tar` Dockerfile. The
builder daemon is started automatically if not running. This replaced the old
`container export --image` flag which was removed in v0.11.0.

**`container system start` is idempotent.** The script always calls it before
any other container operations. It returns immediately if the service is already
running, so there's no need to check status first.

**`container inspect` returns `[]` with exit 0 for missing containers.** The
existence check uses `[[ "$(container inspect ...)" != "[]" ]]` rather than
checking the exit code.

**Claude Code installer binary is symlinked into `/usr/local/bin`.** The official installer places the `claude` binary in `~/.local/bin`, which is not in the default PATH. The setup script symlinks it into `/usr/local/bin` (`ln -sf /root/.local/bin/claude /usr/local/bin/claude`) so `claude` is available regardless of shell login mode. `PATH` is also exported before the installer runs to suppress its "not in PATH" warning. `~/.local/bin` is also added to `PATH` in `/root/.bashrc` so the claude binary itself doesn't warn at startup that its install location isn't in PATH. `uv` avoids this entirely by using `UV_INSTALL_DIR=/usr/local/bin`.

**`UV_CACHE_DIR` resolves dynamically to `${TMPDIR:-/tmp}/uv-cache`.**
Claude Code's sandbox makes `/root/.cache` read-only, which breaks UV's default
cache path. The sandbox also mounts `/tmp` read-only at the bubblewrap level, so
hardcoding `/tmp/uv-cache` fails even when it's in the sandbox allowlist.
Instead, `UV_CACHE_DIR` is set in `.bashrc` and `/etc/profile.d/` to
`${TMPDIR:-/tmp}/uv-cache`, which resolves at shell startup to the
sandbox-provided writable temp directory (the sandbox sets `$TMPDIR`
automatically). Both `/tmp/uv-cache` and `$TMPDIR/uv-cache` are in the sandbox
`filesystem.allowWrite` list as a belt-and-suspenders measure. This also fixes
`uv run --with` which creates temporary virtual environments in `$TMPDIR`.
See ADR-001 in `ADR.md`.

**`UV_PROJECT_ENVIRONMENT` redirects venvs to `${TMPDIR:-/tmp}/.venv`.**
Projects mounted from the host often have a `.venv` with macOS binaries that
are unusable inside the Linux container. `UV_PROJECT_ENVIRONMENT` tells UV
where to create the project virtual environment instead of the default `.venv`
in the project root. Set in `.bashrc` and `/etc/profile.d/` using the same
dynamic `$TMPDIR` pattern as `UV_CACHE_DIR`. The host `.venv` is ignored, not
deleted. Both `/tmp/.venv` and `$TMPDIR/.venv` are in the sandbox
`filesystem.allowWrite` list. The venv is ephemeral per sandbox session, so
`uv sync` runs once per session. See ADR-007 in `ADR.md`.

**`TERM`, `COLORTERM`, and `TERM_PROGRAM` are forwarded into the container.** Without these, Claude Code falls back to a lower color mode (16 or 256 colors) and renders very differently from the host. Both `container run` (new container) and `container exec` (re-attach) pass them via `CONTAINER_ENV`.

**`~/.claude` is shared across all containers via a host volume mount.**
`~/.claude-containers/shared/` on the host is mounted to `/root/.claude` inside
every container. This persists auth credentials (`.credentials.json`), memory,
and user settings across container restarts and across projects. `claude login`
only needs to be run once; all containers share the session.

**`/root/.claude.json` is also persisted, as a file bind-mount.** Claude Code
stores `oauthAccount` and related auth state in the top-level `~/.claude.json`,
not just in `~/.claude/.credentials.json` — so losing it forces a re-login even
when `.credentials.json` survives. The script creates
`~/.claude-containers/claude.json` on the host (initialized to `{}` if absent)
and mounts it to `/root/.claude.json` alongside the `~/.claude/` directory
mount. This way both halves of Claude Code's auth state survive `--rebuild`.

**Skills are synced from the upstream repo on every new-container build.**
Right before `container run`, the script downloads the repo tarball from
`$CLAUDE_SKILLS_ARCHIVE_URL` (default: the `main` branch of
`aryehj/start-claude`), extracts it, and for each directory under the archive's
`skills/` folder, removes the matching directory under
`~/.claude-containers/shared/skills/` and copies the upstream version in its
place. Skills present locally but absent upstream are left untouched — the
clobber is per-skill-directory, not a wholesale wipe. Fetch failures warn but
do not abort container creation. This path only runs when a new container is
being created; re-attach to an existing container skips the sync. See ADR-005.

**Global container CLAUDE.md is seeded from `templates/global-claude.md`.**
On startup, both scripts copy the template to `~/.claude-containers/shared/CLAUDE.md`
if that file does not exist. Claude Code auto-injects `~/.claude/CLAUDE.md` into
every session's system prompt, so this gives models shared environment context
(path layout, `$TMPDIR`, sandbox mounts, proxy allowlist) regardless of the
project cwd. User edits are preserved; `--reseed-global-claudemd` overwrites
unconditionally to pick up template updates. See ADR-015.

In `start-agent.sh` the same template is also seeded into
`~/.claude-agent/opencode-config/AGENTS.md` and referenced from `opencode.json`'s
`instructions` field so OpenCode picks up the same context. The trailing
`## Differences in claude-dev` section is stripped on the OpenCode copy via
`awk` since those bubblewrap/`$TMPDIR` facts only apply inside
`start-claude.sh`. `--reseed-global-claudemd` reseeds both copies.

**1M extended context is disabled; Claude Code uses the standard 200K window.**
The `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` environment variable is set in `.bashrc`,
`/etc/profile.d/`, and passed as a container-level env var via `CONTAINER_ENV` to
prevent Claude Code from using the 1M context option. The `CONTAINER_ENV` path
ensures the variable is present from process birth, before shell init runs.
This keeps the model on its well-tested 200K context window, avoiding potential
quality degradation observed with the larger window.

**Git identity is set via both `~/.gitconfig` and environment variables.**
`git config --global` is run during image build so `/root/.gitconfig` exists in
the image. However, Claude Code's bubblewrap sandbox may not expose
`/root/.gitconfig`, so the identity is also set via `GIT_AUTHOR_NAME`,
`GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, and `GIT_COMMITTER_EMAIL` environment
variables — in `.bashrc`, `/etc/profile.d/git-identity.sh`, and `CONTAINER_ENV`.
The env vars override gitconfig and work regardless of sandbox mount topology.
Defaults are `Dev` / `dev@localhost`. Priority order (highest first):
`--git-name`/`--git-email` CLI flags, `$GIT_USER_NAME`/`$GIT_USER_EMAIL` env
vars, hardcoded defaults.

**`showThinkingSummaries` is enabled in global user settings.** The script
ensures `showThinkingSummaries: true` is set in `~/.claude/settings.json`
(which lives on the host at `~/.claude-containers/shared/settings.json` and is
shared across all containers via the volume mount). If the file exists, the
setting is merged in; if not, a new file is created. This makes Claude Code's
thinking process visible in the transcript.

**`effortLevel` defaults to medium in global user settings.** The script sets
`effortLevel: "medium"` in `~/.claude/settings.json` alongside the other global
settings. This is set at the global level (not via environment variable) so that
individual projects can override it by setting `"effortLevel"` in their
`.claude/settings.local.json` — project settings take precedence over global
settings. The env var `CLAUDE_CODE_EFFORT_LEVEL` is intentionally not used
because it takes highest priority and would prevent per-project overrides.

**Sandbox is configured in strict mode.** The project-level
`settings.local.json` sets `sandbox.failIfUnavailable: true` (hard-fail if
the sandbox cannot start, instead of silently degrading) and
`sandbox.allowUnsandboxedCommands: false` (block any bash command that cannot
be sandboxed — no escape hatch). `autoAllowBashIfSandboxed` remains `true` so
sandboxed commands run without a permission prompt. The migration block adds
these settings to existing files that lack them.

**Theme is set at the project level, not globally.** The light theme is
configured in each project's `.claude/settings.local.json` rather than in the
global `~/.claude.json`. This avoids needing to persist or merge `.claude.json`
across container lifecycles. The migration block in the settings injection
section adds `"theme": "light"` to existing settings files that lack it.

## start-agent.sh key decisions

`start-agent.sh` is a sibling to `start-claude.sh`, not a replacement. It runs
both Claude Code and OpenCode on top of a single shared Colima VM and a
single shared docker container, with a VM-level egress allowlist the
in-container LLM cannot modify, and routes local inference to Ollama or
omlx on the macOS host.

**Colima, one shared VM + one shared container.** The `claude-agent` Colima
profile hosts a single `claude-agent` docker container. `$(pwd)` is bind-
mounted at the same path on both sides at launch; different project dirs
recreate the container rather than coexisting. Default sizing 8 GiB /
6 CPUs, overridable via `CLAUDE_AGENT_MEMORY` / `CLAUDE_AGENT_CPUS` env vars
and `--memory=` / `--cpus=` CLI flags.

**Dockerfile, not an inline heredoc.** Unlike `start-claude.sh`, the agent
image is built from `dockerfiles/claude-agent.Dockerfile` via
`docker build`. More readable, cacheable per-layer, and easier to iterate
on. The Dockerfile mirrors the existing setup plus a global
`npm install -g opencode-ai@latest`.

**Egress allowlist enforced by in-VM tinyproxy + a CLAUDE_AGENT iptables
child chain.** tinyproxy runs as a systemd service inside the Colima VM
(not inside the container), bound to the docker bridge gateway, with
`FilterDefaultDeny Yes` and a regex filter file generated from the host-
side allowlist. A dedicated `CLAUDE_AGENT` iptables chain owns the
bridge-egress policy; `DOCKER-USER` jumps to it for any traffic sourced
from the bridge CIDR. The chain allows: established/related return
traffic, container → in-VM tinyproxy, container → macOS host inference
port (11434 for Ollama, 8000 for omlx), and container → bridge DNS.
Everything else is REJECTed. Re-applying is atomic — `iptables -F
CLAUDE_AGENT` wipes prior rules, then `-A` repopulates in order — so no
comment-tag matching or rule-number walking is needed. See ADR-010.

**Allowlist file on the host, not in the repo.**
`~/.claude-agent/allowlist.txt` is seeded on first run with a permissive
dev/research list. The user edits it on the macOS host; applying changes
is a ~2s `start-agent.sh --reload-allowlist` fast path that regenerates
the tinyproxy filter and sends SIGHUP without touching the container. The
LLM inside the container has no write path to the allowlist.

**The seed omits write-capable hosts.** tinyproxy filters by hostname, not
URL path or HTTP method, so `github.com` can't be "read-only." The seeded
list therefore omits `github.com`/`gitlab.com`/`bitbucket.org`,
`huggingface.co`, container registries, and dataset-upload hubs
(zenodo/figshare/kaggle/osf/dataverse/datadryad). Code reads still work via
`codeload.github.com` + `githubusercontent.com`. This makes it safer to
enable webfetch/websearch in the agent — an injected page can't direct the
agent to push, open a PR, or publish a dataset via the allowed egress.
Users who need `gh`/HTTPS-push/image-push from inside the container add
those hosts back by hand.

**Ollama via host networking.** `HOST_IP` is discovered at launch from the
VM's default route (under `colima start --network-address`, that's the
macOS host). The container is pointed at `http://$HOST_IP:11434` via the
`OLLAMA_HOST` env var, and the iptables allowlist has a dedicated RETURN
rule for that destination. On first-time setup the user runs
`launchctl setenv OLLAMA_HOST 0.0.0.0:11434` once on the host. Ollama
preflight failures warn but do not block startup.

**OpenCode inference provider via `opencode.json` injection.** OpenCode's
config lives at `~/.config/opencode/opencode.json`; the script writes/
migrates a provider entry there using `@ai-sdk/openai-compatible` with a
`baseURL` pointing at the selected backend. For Ollama the provider key is
`"ollama"` with `baseURL: http://$HOST_IP:11434/v1`; for omlx the key is
`"omlx"` with `baseURL: http://$HOST_IP:8000/v1` and an `apiKey` field if
`$OMLX_API_KEY` is set. Both entries can coexist — switching backends
between runs does not remove the other provider's config. Config and data
dirs (`~/.claude-agent/opencode-config`, `~/.claude-agent/opencode-data`)
are bind-mounted into the container to persist credentials and state.

**Per-mode OpenCode models are set via `--plan-model`, `--exec-model`, and `--small-model`.**
These flags (or their env var equivalents `CLAUDE_AGENT_PLAN_MODEL`, `CLAUDE_AGENT_EXEC_MODEL`,
`CLAUDE_AGENT_SMALL_MODEL`) inject `agent.plan.model`, `agent.build.model`, and `small_model`
into `opencode.json` respectively. Bare model IDs (e.g. `gemma3:27b`) are prefixed with the
active provider key; full `provider/model` strings are used as-is, enabling cross-provider
mixing. Model discovery runs unchanged — these fields are written after discovery and do not
affect the models list. Omitting a flag leaves any existing per-mode entry in place.

**`--backend=omlx` selects omlx as the local inference server.** Default
is `ollama`. omlx is an MLX-based inference server for Apple Silicon with
an OpenAI-compatible API on port 8000 and `--api-key` support. Its API-key
authentication means no host-side pf firewall is needed (unlike Ollama,
which requires either `localhost`-only binding or a pf firewall when bound
to `0.0.0.0`). `OMLX_API_KEY` from the host env is passed into the
container as both `OMLX_API_KEY` and in the OpenCode config's `apiKey`
field. `OMLX_HOST` replaces `OLLAMA_HOST` in the container env when using
this backend. The `CLAUDE_AGENT_BACKEND` env var sets the default; the
`--backend=` CLI flag overrides it. See ADR-012.

**Shared `~/.claude` state with `start-claude.sh`.** The same
`~/.claude-containers/shared/` directory and `~/.claude-containers/claude.json`
file are mounted so auth state, global settings, and synced skills are
reused across both scripts. Run only one at a time to avoid stomping.

**`--rebuild` semantics.** Removes the image and container non-
interactively. Deleting the Colima VM itself requires an additional `y`
confirmation prompt, because VM deletion wipes every image in the VM's
docker runtime and is not reversible — a meaningful divergence from
`start-claude.sh`, where `container rm` only affects one microVM.

**`NODE_USE_ENV_PROXY=1` makes Node honor the proxy natively.** Node 24
ships undici with built-in `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`
support, gated behind `NODE_USE_ENV_PROXY=1`. We set that env var in
`DOCKER_ENV_ARGS` alongside the uppercase proxy URLs and let Node do the
rest — no `global-agent`, no `--require` shim, no `NODE_PATH`, no extra
packages to keep in sync. Claude Code's own Bun runtime already honors
`HTTPS_PROXY` on its own, so this is only load-bearing for OpenCode and for
any Node helpers Claude Code spawns. See ADR-013.

**Web tools: webfetch allowed, websearch denied by default.** `opencode.json`
is generated with `permission.webfetch: "allow"` and `permission.websearch:
"deny"`. webfetch egress is constrained by the existing tinyproxy allowlist
— an injected page can't redirect fetches to hosts that aren't already
trusted. websearch is off by default because its default backend (Exa MCP)
would route queries through a third-party gateway.

**SearXNG-backed websearch via `--enable-local-search`.** When the flag is
set (env: `CLAUDE_AGENT_ENABLE_LOCAL_SEARCH=1`), a `searxng` docker container
is started on the bridge alongside `claude-agent`, and opencode's
`permission.websearch` is flipped to `"allow"` with a custom Python FastMCP
shim (`/opt/searxng-mcp/server.py`) wired in as `mcp.searxng`. SearXNG
fans out to upstream engines through tinyproxy via `outgoing.proxies` in
`~/.claude-agent/searxng/settings.yml` — not via `HTTPS_PROXY` env vars,
which SearXNG silently ignores due to an explicit `transport=` in its httpx
client (see ADR-014). The allowlist therefore governs all SearXNG egress,
same as the rest of the stack. `NO_PROXY=searxng` is added to `claude-agent`
so the MCP shim's direct container-to-container HTTP call bypasses tinyproxy.

**`host.docker.internal:host-gateway` is set belt-and-suspenders** on
`docker run` so tools that hard-code the name resolve to the host even on
Colima, where the mapping is not automatic.

**`docker build` runs with `--network=host`.** Build-step RUN containers
attach to the docker bridge by default and inherit the DOCKER-USER REJECT
rule from ADR-010, so `apt-get update` on Dockerfile step 3 fails with
"no route to host". `--network=host` puts the build in the VM's host netns,
which bypasses the `FORWARD` chain entirely. Runtime containers still attach
to the bridge via `docker run` and are firewalled normally. Passing
`HTTP_PROXY`/`HTTPS_PROXY` as build-args is the conventional alternative but
is unreliable on the legacy builder (apt only reads lowercase `http_proxy`).
See ADR-011 in `ADR.md`.

**Firewall smoke tests live in README.md.** Six copy-paste commands verify
default-deny, allowed-via-proxy, denied-via-proxy, Ollama carve-out, env
wiring, and allowlist hot-reload. Re-run them after any change to the
`DOCKER-USER` rule insertion, the tinyproxy config generator, or the
allowlist-reload fast path.

## Commit style

Do NOT include `Co-Authored-By` lines in commit messages.

## Making changes

The setup script is embedded as a `bash -c '...'` heredoc inside
`start-claude.sh`. Edit it there. After changing it, run with `--rebuild` to
apply the changes:

```bash
start-claude.sh --rebuild
```

This removes the existing project container (if any) and the `claude-dev:latest`
image, then rebuilds from scratch.

For `start-agent.sh`, the image is built from
`dockerfiles/claude-agent.Dockerfile`. Edit the Dockerfile for image-level
changes; edit `start-agent.sh` for host-side orchestration, firewall, or
allowlist-handling changes. After either, run:

```bash
start-agent.sh --rebuild
```

which removes `claude-agent:latest` and the container, then rebuilds. An
additional confirmation prompt offers to delete the Colima VM too — only
say yes if you want to start over from a clean VM (loses everything else
inside the VM's docker runtime).
