# CLAUDE.md

This repo contains tooling for spinning up isolated Claude Code dev containers
using Apple Containers. One script, one container per project.

## Layout

```
start-claude.sh              — Apple Containers path; per-project microVM with Claude Code
start-agent.sh               — Colima path; shared VM + container with Claude Code + OpenCode + VM-level egress allowlist
research.py                  — Python script; isolated Colima VM + Vane + SearXNG research environment
dockerfiles/                 — Dockerfiles built by start-agent.sh (claude-agent.Dockerfile)
templates/                   — seed templates copied to host state dirs on first run
  global-claude.md                    — seeded to ~/.claude-containers/shared/CLAUDE.md
  research-denylist-sources.txt       — seeded to ~/.research/denylist-sources.txt by research.py
  research-denylist-additions.txt     — seeded to ~/.research/denylist-additions.txt by research.py
skills/                      — reusable Claude Code skills (back up of ~/.claude/skills/)
plans/                       — implementation plans written by /plan skill
tests/                       — unit tests and eval harness
  test_research.py                     — unit tests for research.py pure helpers
  probe-vane-egress.sh                 — smoke test for research-vane egress env vars
  vane-eval/                           — Vane research-quality eval harness (OFAT sweep)
    queries.md                         — six research queries with reference answers
    run_cheap.py                       — cheap phase: OFAT sweep against omlx directly
    select_winners.py                  — pick winner + ablations from a graded cheap run
    run_vane.py                        — Vane confirm phase: replay winner through full pipeline
    lib/                               — shared helpers (cells.py, queries.py)
    test_lib.py, test_run_cheap.py     — pytest suites for lib + run_cheap
    test_run_vane.py                   — pytest suite for select_winners + run_vane helpers
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

**`effortLevel` is intentionally unpinned.** Claude Code applies model-native defaults, which change with each release. Use `/effort <level>` or `effortLevel` in a project's `.claude/settings.local.json` for situational overrides. See ADR-017.

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

**SearXNG-backed websearch runs by default.** A `searxng` docker container
is started on the bridge alongside `claude-agent`, and opencode's
`permission.websearch` is flipped to `"allow"` with a custom Python FastMCP
shim (`/opt/searxng-mcp/server.py`) wired in as `mcp.searxng`. SearXNG
fans out to upstream engines through tinyproxy via `outgoing.proxies` in
`~/.claude-agent/searxng/settings.yml` — not via `HTTPS_PROXY` env vars,
which SearXNG silently ignores due to an explicit `transport=` in its httpx
client (see ADR-014). The allowlist therefore governs all SearXNG egress,
same as the rest of the stack. `NO_PROXY=searxng` is added to `claude-agent`
so the MCP shim's direct container-to-container HTTP call bypasses tinyproxy.
Pass `--disable-search` to skip SearXNG (which also disables OpenCode websearch).

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

## research.py key decisions

`research.py` is a Python (stdlib-only) orchestrator for a dedicated Colima VM
(`research` profile) hosting Vane (AI research UI) and its own SearXNG instance,
network-isolated from `claude-agent`. Its design goal is to let Vane scrape the
long tail of search-result URLs while filtering known-bad content — a default-allow
denylist model, contrasting with `start-agent.sh`'s default-deny allowlist.
See ADR-018 for the language choice rationale.

**Separate Colima profile (`research`) for VM-level isolation.** `start-agent.sh`
(`claude-agent` profile) and `research.py` (`research` profile) use independent
Colima VMs with separate iptables chains, docker bridges, and container namespaces.
Both can run simultaneously. Docker networks (`claude-agent-net` and `research-net`)
do not bridge between VMs. The only potential host-level conflict is port 3000 —
avoid running both if another service already occupies it.

**Dedicated SearXNG instance, not shared with `claude-agent`.** `research.py` starts
its own `research-searxng` container on `research-net`, routing fan-out through the
`research` VM's Squid denylist proxy. The `searxng` container in `start-agent.sh`
(OpenCode's MCP websearch backend) is a separate instance with an allowlist-based
egress model. The two instances never interact.

**Denylist (default-allow + blocked domains) rather than allowlist.** Unlike
`start-agent.sh`'s tinyproxy allowlist, `research.py` uses Squid with a composed
denylist so Vane can reach arbitrary search-result URLs without pre-approving every
destination. The denylist is composed from hagezi upstream feeds (quality and threat
filtering) plus `denylist-additions.txt` for exfil-capable services upstream feeds
won't cover. See ADR-021 and ADR-023.

**Port 3000 on the macOS host for Vane.** `research-vane` is started with
`-p 3000:3000` so the UI is accessible at `http://localhost:3000`. `start-agent.sh`
does not bind port 3000 (Vane was extracted from it — see ADR-028), so both scripts
can run simultaneously without port conflict.

**LLM inference via `host.docker.internal`.** Vane's LLM endpoint is configured once
via the UI at `http://localhost:3000`. Use `http://host.docker.internal:11434` for
Ollama or `http://host.docker.internal:8000/v1` for omlx. The hostname is wired via
`--add-host=host.docker.internal:host-gateway` on `docker run`, and the iptables
`RESEARCH` chain has a dedicated RETURN rule for `$HOST_IP:$INFERENCE_PORT`. LLM
traffic goes direct (not through Squid), same pattern as `start-agent.sh`.
Configuration persists in `~/.research/vane-data/` across `--rebuild`.

**`research.py` denylist seeds live in `templates/research-denylist-sources.txt` and `templates/research-denylist-additions.txt`.**
On first run both files are seeded to `~/.research/`. The composed denylist is
`(cached-upstream ∪ denylist-additions.txt) − denylist-overrides.txt`. Upstream
feeds are URL-pinned downloads cached in `~/.research/denylist-cache/`. On-disk
files are never silently overwritten — use `--reseed-denylist` to pick up template
updates. See ADR-023.

**Hagezi feeds use the `wildcard/<list>-onlydomains.txt` format, not `domains/<list>.txt`.**
The onlydomains files list one apex/registrable domain per line with subdomain
hierarchies pre-rolled-up. `denylist_to_squid_acl()` prefixes each entry with
`.` so a single `.foo.com` covers the apex and every subdomain via Squid's
`dstdomain` suffix-match. The older `domains/` files list subdomains
exhaustively but omit the apex, which causes Squid to leak `https://foo.com/`
while blocking `https://www.foo.com/`. Onlydomains feeds are also ~50% smaller.
Hagezi deliberately omits a handful of canonical Google ad apexes
(`doubleclick.net`, `googleadservices.com`, etc.) — those are added back in
`templates/research-denylist-additions.txt`. See ADR-025.

**`research.py` auto-prunes orphan files in `denylist-cache/` on every refresh and reload.**
`prune_orphan_cache_files()` deletes any `.txt` in the cache dir whose basename
isn't produced by a current URL in `denylist-sources.txt`. Without this, a
template SHA bump or feed-path change leaves stale `.txt` files that
`compose_denylist`'s `*.txt` glob silently merges in alongside new feeds.
Called from both `refresh_denylist_cache()` and `reload_denylist_fast_path()`,
so editing `sources.txt` and running either is self-healing. Pruned filenames
are echoed to stdout. See ADR-026.

**`research.py` hard-exits if `~/.research/allowlist.txt` is detected.**
Installations predating the denylist migration have this file. `research.py`
prints the two manual steps required (`rm -rf ~/.research/`, then `--rebuild`)
and exits non-zero. No automatic migration — the user must take the explicit
action. See ADR-022.

**`research.py` uses Squid (not tinyproxy) as the in-VM filtering proxy.**
Squid's `dstdomain` ACL performs O(1) hash-table lookups, supporting million-entry
denylists where tinyproxy's regex-NFA approach OOMs. Port 8888 is kept, so the
iptables RESEARCH chain and SearXNG's `outgoing.proxies` config are unchanged.
Squid 6 rejects ACL files that contain both a domain and one of its subdomains —
`_prune_subdomains()` strips redundant entries before writing the file.
`start-agent.sh` stays on tinyproxy (~280-entry allowlist, regex fine at that
scale). See ADR-021.

**`research.py` Vane container is wired through Squid via `HTTP_PROXY` and `HTTPS_PROXY`.**
`ensure_vane_container` passes all three proxy env vars: `HTTP_PROXY`,
`HTTPS_PROXY` (both `http://{bridge_ip}:8888`), and
`NO_PROXY=research-searxng,host.docker.internal,localhost,127.0.0.1`.
The structural reason: the `RESEARCH` iptables chain REJECTs all
research-net→external traffic, so both HTTP and HTTPS scrape targets need a
proxy path out. `NO_PROXY` exempts in-network direct-bridge destinations
(SearXNG and the host LLM endpoint). An earlier HTTPS-only configuration
(ADR-027) was based on a wrong-Vane observation during a debug session where
two Vane containers shared host port 3000; post-dedup testing confirmed
`HTTP_PROXY` causes no regression on `research-vane`. See ADR-029 (supersedes
ADR-027 after the ADR-028 dedup). Existing installs need
`docker rm -f research-vane && ./research.py` (or full `--rebuild`) to pick up
the corrected env vars. `tests/probe-vane-egress.sh` checks all three env vars
and a sidecar HTTPS round-trip.

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

For `research.py`, the script is a single Python file at the repo root. Edit it
directly. After changing it:

```bash
./research.py --rebuild
```

This removes the `research-vane` and `research-searxng` containers and recreates
them. An additional confirmation prompt offers to delete the `research` Colima VM
too — only say yes if you want to start from a completely clean state.
