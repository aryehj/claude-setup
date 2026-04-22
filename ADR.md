# Architecture Decision Records

## ADR-001: Set `UV_CACHE_DIR` to a writable path in the container environment

**Date:** 2026-04-03
**Status:** Accepted

### Context

Inside the container, Claude Code's sandbox restricts `/root/.cache` to
read-only. UV defaults to `/root/.cache/uv` for its cache directory. Every
`uv run` invocation fails with:

```
error: Failed to initialize cache at `/root/.cache/uv`
  Caused by: failed to create directory `/root/.cache/uv`: Read-only file system (os error 30)
```

Claude Code would then spend multiple tool calls trying to work around this
(prefixing `UV_CACHE_DIR=/tmp/...` on individual commands), burning context and
often still failing when it forgot the prefix.

### Decision

Set `UV_CACHE_DIR=/tmp/uv-cache` at multiple levels:

1. **`CONTAINER_ENV` (`-e UV_CACHE_DIR=/tmp/uv-cache`)** — passed to both
   `container run` and `container exec`. This is the critical path: Claude Code
   inherits the container's environment, and all `bash -c` subprocesses it spawns
   inherit it in turn.
2. **`/root/.bashrc`** — interactive login shells inside the container.
3. **`/etc/environment`** — PAM-based login sessions.
4. **`/etc/profile.d/uv-cache.sh`** — login shells sourcing profile.d.

Layer 1 is sufficient for Claude Code's sandbox. Layers 2-4 are
belt-and-suspenders for manual shell sessions.

### Consequences

- `uv run`, `uv pip`, and `uvx` work out of the box inside the sandbox with no
  manual `UV_CACHE_DIR` prefix.
- The cache lives under `/tmp/uv-cache`, which is writable but ephemeral — it
  does not survive container restarts. This is acceptable because UV
  re-downloads packages quickly and the cache is not large.
- Requires `--rebuild` to take effect on existing containers.

## ADR-002: Add `/tmp/uv-cache` to sandbox `filesystem.allowWrite`

**Date:** 2026-04-03
**Status:** Accepted

### Context

ADR-001 set `UV_CACHE_DIR=/tmp/uv-cache` as an environment variable, but this
alone was insufficient. Claude Code's bubblewrap sandbox mounts most of `/tmp`
as read-only. The env var told uv *where* to write, but the sandbox prevented
the actual writes:

```
error: Failed to initialize cache at `/tmp/uv-cache`
  Caused by: failed to create directory `/tmp/uv-cache`: Read-only file system (os error 30)
```

Setting `$TMPDIR` (`/tmp/claude`) also failed — the sandbox restricts that path
for its own use.

### Decision

Add `/tmp/uv-cache` to `sandbox.filesystem.allowWrite` in each project's
`.claude/settings.local.json`. The script now:

1. **New projects:** The generated `settings.local.json` includes the
   `filesystem.allowWrite` array with `/tmp/uv-cache`.
2. **Existing projects:** The migration block in `start-claude.sh` checks for
   the presence of `/tmp/uv-cache` in `allowWrite` and adds it if missing,
   alongside the existing boolean→object migration.

### Consequences

- `uv` commands work inside the sandbox without any manual workarounds.
- The env var (`UV_CACHE_DIR`) and sandbox permission (`filesystem.allowWrite`)
  are now kept in sync by the same script.
- Existing containers need their `settings.local.json` updated (happens
  automatically on next `start-claude.sh` run) but do not require `--rebuild`.

## ADR-004: Resolve `UV_CACHE_DIR` dynamically via `$TMPDIR`

**Date:** 2026-04-04
**Status:** Accepted (supersedes parts of ADR-001 and ADR-002)

### Context

ADR-001 hardcoded `UV_CACHE_DIR=/tmp/uv-cache` and passed it as a static env var
via `CONTAINER_ENV`. ADR-002 added `/tmp/uv-cache` to the sandbox `allowWrite` list.
In practice, Claude Code's bubblewrap sandbox mounts `/tmp` read-only at the
filesystem level before the allowlist is evaluated, so writes to `/tmp/uv-cache`
still failed. Users reported that `uv run --with` (which creates temporary
virtual environments) also failed for the same reason.

The sandbox sets `$TMPDIR` to a guaranteed-writable directory at runtime. Using
this instead of hardcoding `/tmp` resolves both issues.

### Decision

1. **Remove `UV_CACHE_DIR` from `CONTAINER_ENV`.** No longer pass it as a static
   container env var.
2. **Set `UV_CACHE_DIR="${TMPDIR:-/tmp}/uv-cache"` in `.bashrc` and
   `/etc/profile.d/uv-cache.sh`.** The variable resolves at shell startup to the
   sandbox-provided writable temp directory. Falls back to `/tmp` for interactive
   use outside the sandbox.
3. **Drop `/etc/environment`.** It does not support shell variable expansion, so
   it cannot use `$TMPDIR`.
4. **Add `$TMPDIR/uv-cache` to sandbox `filesystem.allowWrite`** alongside the
   existing `/tmp/uv-cache` entry, as a belt-and-suspenders measure.
5. **`mkdir -p` on every shell startup.** Each sandbox session may get a fresh
   `$TMPDIR`, so the `uv-cache` subdirectory is created at profile load time.

### Consequences

- `uv run`, `uv pip`, `uvx`, and `uv run --with` all work inside the sandbox
  without manual workarounds.
- The cache is ephemeral per sandbox session (each may use a different `$TMPDIR`),
  which is acceptable — UV re-downloads quickly.
- Requires `--rebuild` to bake the new profile scripts into the image. Existing
  containers with the old static env var will continue to use `/tmp/uv-cache`
  (unchanged behavior) until rebuilt.

## ADR-003: Set theme in project-level settings, not global `.claude.json`

**Date:** 2026-04-03
**Status:** Accepted

### Context

The script previously set `"theme": "light"` by overwriting `/root/.claude.json`
on every `container run`:

```bash
bash -c 'echo "{\"theme\":\"light\"}" > /root/.claude.json && exec bash'
```

`/root/.claude.json` is a top-level file, separate from the `/root/.claude/`
directory that is volume-mounted for credential persistence. Because `.claude.json`
was not persisted, it was ephemeral — but it also meant the overwrite destroyed
any auth-related state that Claude Code wrote there (e.g. `oauthAccount`,
`hasCompletedOnboarding`), potentially contributing to re-login prompts on
container recreation.

Mounting `.claude.json` as a second volume was considered but adds complexity
(seeding the file, merging settings across versions).

### Decision

Move the theme setting to the project-level `settings.local.json`, which the
script already creates and migrates. The `container run` entrypoint is now plain
`bash` with no file writes.

- New projects get `"theme": "light"` in the generated `settings.local.json`.
- Existing projects get it added by the migration block (same pattern as the
  sandbox and `allowWrite` migrations).

### Consequences

- `.claude.json` is no longer touched by the script. Claude Code manages it
  internally; its contents are ephemeral to each container but no longer
  clobbered on creation.
- Theme is project-scoped, which is how `settings.local.json` is designed to
  work. Different projects could theoretically use different themes.
- Auth persistence depends solely on the `~/.claude/` volume mount (which
  contains `.credentials.json`). If `.claude.json` turns out to hold state
  required for session continuity, a separate volume mount would need to be
  added. *(This caveat materialised — see ADR-006.)*

## ADR-005: Sync skills from upstream repo on new container build

**Date:** 2026-04-04
**Status:** Accepted

### Context

The `skills/` directory in this repo is the authoritative copy of the Claude
Code skills we want available in every container. Previously, getting them
into `~/.claude/skills/` was manual: the README instructed users to `cp -r`
or symlink each skill into place. Skills drift quickly, and anyone using an
older copy of the shared `.claude` mount had no way of knowing their skills
were stale.

The shared mount (`~/.claude-containers/shared/` → `/root/.claude`) already
centralises skill storage across containers, so pushing updated skills into
it once per container build propagates to every project.

### Decision

Right before `container run` creates a new container, the script downloads
the upstream repo tarball (default: `main` branch of `aryehj/start-claude`,
overridable via `CLAUDE_SKILLS_ARCHIVE_URL`), extracts it, and for each
directory under the archive's `skills/`:

1. `rm -rf` the matching directory in `~/.claude-containers/shared/skills/`
2. `cp -R` the upstream version in its place

Skills that exist locally but are absent upstream are left untouched. Fetch
or extraction failures produce a warning and container creation proceeds.

### Consequences

- Every new container (including `--rebuild`, which deletes the existing
  container first) picks up the latest upstream skills automatically.
- Users can iterate on a skill locally (e.g. edit files under
  `~/.claude-containers/shared/skills/plan/`) only until the next container
  build clobbers it — local-only edits to synced skills are disposable. Users
  who want durable customisations should fork the repo and point
  `CLAUDE_SKILLS_ARCHIVE_URL` at their fork, or name their skill differently
  so it doesn't collide with an upstream name.
- The sync requires network access at container-creation time. Offline
  builds still work but skip the sync with a warning.
- Re-attaching to an existing container does not trigger a sync, matching
  the existing invariant that image/container state only changes on new
  builds.

## ADR-006: Persist `/root/.claude.json` via a file bind-mount

**Date:** 2026-04-04
**Status:** Accepted (addresses caveat in ADR-003)

### Context

ADR-003 moved the theme setting out of `.claude.json` so the script stopped
clobbering that file, but left its persistence unaddressed — the file was
ephemeral per container. This turned out to break authentication across
`--rebuild`: Claude Code stores `oauthAccount` and other auth state in
`~/.claude.json`, not just in `~/.claude/.credentials.json`. When `--rebuild`
destroyed the container, the fresh container had `.credentials.json` mounted
in (via the `~/.claude/` volume) but no `.claude.json`, and Claude Code
prompted for re-login every time.

The user confirmed they are container-only (no host Claude install), so
separation between host and container `.claude.json` state is not needed.

### Decision

Add a second bind mount: the host file `~/.claude-containers/claude.json`
maps to `/root/.claude.json` in the container. The script creates and
initializes the file to `{}` on the host if it does not already exist, so the
bind mount resolves to a file rather than an auto-created directory.

The file sits alongside (not inside) `~/.claude-containers/shared/`, because
`shared/` is already mounted at `/root/.claude`, and nesting `claude.json`
inside it would expose it at the wrong path (`/root/.claude/claude.json`).

### Consequences

- `claude login` now survives `--rebuild`. Both halves of Claude Code's auth
  state (`~/.claude/.credentials.json` and `~/.claude.json`) are persisted on
  the host.
- All containers share the same `.claude.json`, matching the existing
  "shared across containers" model for `~/.claude/`. Per-project config
  still lives in each project's `.claude/settings.local.json`, unaffected.
- Relies on Apple Containers supporting file-level bind mounts
  (`-v host_file:container_file`). If a future version drops that support,
  fall back to mounting the parent directory or seeding `.claude.json` into
  `~/.claude-containers/shared/` with a symlink.
- To reset auth state: delete `~/.claude-containers/claude.json` and
  `~/.claude-containers/shared/.credentials.json`, then re-run
  `claude login`.

## ADR-007: Redirect UV project venv via `UV_PROJECT_ENVIRONMENT`

**Date:** 2026-04-07
**Status:** Accepted

### Context

Projects mounted into containers often have a `.venv` directory created on the
host (macOS/ARM). The Claude Code agent inside the Linux container sees this
`.venv`, attempts to use it, and fails — the binaries are wrong platform, paths
are wrong, and the sandbox may block writes. The agent then wastes cycles trying
workarounds (manual `--python` flags, prefixing env vars on individual commands,
or trying to `rm -rf` and recreate the venv in place).

This is a well-known problem in any "mount host project into dev container"
workflow. UV provides `UV_PROJECT_ENVIRONMENT` for exactly this use case: it
overrides where `uv sync`, `uv run`, etc. create the project virtual
environment (default: `.venv` in the project root).

### Decision

1. **Set `UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/.venv"` in `.bashrc` and
   `/etc/profile.d/uv-cache.sh`.** Same dynamic `$TMPDIR` resolution pattern
   as `UV_CACHE_DIR` (ADR-004). The variable resolves at shell startup to the
   sandbox-provided writable temp directory.
2. **`mkdir -p` on every shell startup** to ensure the directory exists (same
   pattern as `UV_CACHE_DIR`).
3. **Add `/tmp/.venv` and `$TMPDIR/.venv` to `sandbox.filesystem.allowWrite`**
   in each project's `.claude/settings.local.json`, alongside the existing UV
   cache entries. Both the default settings block and the migration block are
   updated.

UV intentionally keeps `UV_PROJECT_ENVIRONMENT` as a user-level environment
variable rather than a `pyproject.toml` / `uv.toml` setting — a project should
not be able to direct installs to arbitrary paths on the user's machine. This
makes the env-var approach the canonical solution.

### Consequences

- `uv sync`, `uv run`, `uv add`, etc. create and use a Linux venv in `$TMPDIR/.venv`
  instead of the project's `.venv`. The host `.venv` is completely ignored, not
  deleted.
- The venv is ephemeral per sandbox session (each may use a different `$TMPDIR`),
  so `uv sync` must run once per session. This is acceptable — UV installs from
  cache quickly, and the cache itself persists across commands within a session.
- Requires `--rebuild` to bake the new profile scripts into the image.
- Existing project `settings.local.json` files pick up the new `allowWrite`
  entries automatically on next `start-claude.sh` run (no rebuild needed for
  the sandbox permissions, only for the env var).

## ADR-010: start-agent: in-VM tinyproxy + DOCKER-USER iptables for egress allowlist

**Date:** 2026-04-15
**Status:** Accepted

### Context

`start-agent.sh` (sibling to `start-claude.sh`) runs both Claude Code and
OpenCode in a shared Colima-hosted docker container, and needs a network
egress allowlist that the in-container LLM cannot modify. Several placements
were possible:

1. **Host-side pf + host-side proxy.** Strongest isolation — the rules live
   two VM boundaries away from the container. But pfctl anchors, launchd
   plists for the proxy, vmnet CIDR discovery, and `sudo` for every reload
   all bleed setup pain into the user's macOS install.
2. **In-container iptables + in-container proxy.** Trivially simple, but
   trivially bypassable: the LLM is `root` inside the container by default,
   and a single `iptables -F` (or editing the proxy config) undoes it.
3. **In-VM iptables (DOCKER-USER) + in-VM tinyproxy.** Rules live outside
   the container's network namespace, inside a Lima VM the container cannot
   reach without a container escape plus VM root — two jumps outside any
   realistic behavioral threat model, with none of the host-side setup
   overhead of option 1.

### Decision

Option 3.

- **tinyproxy** runs as a systemd service inside the Colima VM, bound to the
  docker bridge gateway IP (discovered at script time via
  `docker network inspect bridge`). Config: `FilterDefaultDeny Yes`,
  `FilterExtended Yes`, `FilterURLs No`, and a `Filter` file of anchored
  regexes (`(^|\.)domain\.tld$`) generated on the host from
  `~/.claude-agent/allowlist.txt`. The filter applies to the hostname
  portion of HTTPS `CONNECT` requests as well as plain HTTP.
- **iptables** rules live in a dedicated `CLAUDE_AGENT` chain. `DOCKER-USER`
  (which the docker daemon explicitly leaves alone on restart) carries a
  single `-s $BRIDGE_CIDR -j CLAUDE_AGENT` jump. The child chain holds the
  RETURN rules (established/related, proxy port, host inference port,
  bridge DNS) and a catch-all REJECT. Re-applying is atomic: `iptables -F
  CLAUDE_AGENT` wipes prior state, then `-A` repopulates in order. No
  comment-tag matching, no rule-number arithmetic, no risk of leaving
  stale rules co-resident with new ones.
- **Re-apply on every run** instead of persisting. The firewall script is
  regenerated on the host and streamed into `sudo sh` over `colima ssh` on
  every invocation. No state drift between "rules the host script thinks
  are active" and "rules actually active after a VM reboot." The cost is a
  ~1s colima-ssh round trip at the start of every launch.
- **Allowlist lives at `~/.claude-agent/allowlist.txt`** on the macOS host,
  plain text, one domain per line, suffix matching. `--reload-allowlist`
  regenerates the filter and reloads tinyproxy (SIGHUP) without touching
  the running container — a ~2s fast path.

### Consequences

- The LLM inside the container has no write path to the iptables rules or
  the allowlist. Modifying either requires a container escape plus VM root.
- All outbound traffic except the established-return path, the proxy port,
  the Ollama port, and DNS to the bridge gateway is REJECTed at the VM
  level. Applications inside the container that don't honor `HTTPS_PROXY` /
  `HTTP_PROXY` will simply fail to reach anything — an acceptable price,
  and arguably a feature (no silent bypass). *(Node.js apps — Claude Code
  and OpenCode — are handled by ADR-013's `global-agent` bootstrap.)*
- Every invocation pays a small latency tax (applying firewall rules), in
  exchange for zero configuration drift.
- The seed allowlist is intentionally permissive for development and
  research workflows; users are expected to prune it.
- Per-rule granularity is coarse (hostname match only). If the allowlist
  grows past ~200 entries or needs per-path / per-method semantics, squid
  with `acl ... dstdomain` is the conventional upgrade.

## ADR-009: Set git identity via environment variables, not just gitconfig

**Date:** 2026-04-08
**Status:** Accepted

### Context

`git config --global` writes to `/root/.gitconfig` during image build. This
works for direct shell usage but fails inside Claude Code's bubblewrap sandbox,
which may not expose `/root/.gitconfig` in its mount namespace. When the sandbox
runs `git commit`, git cannot auto-detect the author identity and the commit
fails with "Author identity unknown".

### Decision

Set git identity in three layers (matching the existing pattern for other env
vars):

1. **`git config --global`** — baked into the image for non-sandboxed git.
2. **`.bashrc` and `/etc/profile.d/git-identity.sh`** — exports
   `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`,
   `GIT_COMMITTER_EMAIL` so interactive shells have them.
3. **`CONTAINER_ENV`** — passes the same four variables as container-level env
   vars so they are inherited from process birth, before shell init.

Git's `GIT_AUTHOR_*` / `GIT_COMMITTER_*` environment variables take precedence
over all config files, so they work regardless of whether the sandbox mounts
`/root/.gitconfig`.

### Consequences

- Git commits work inside the bubblewrap sandbox without needing sandbox config
  changes.
- The env vars override any repo-level `.gitconfig`. This is acceptable because
  the container is single-user and all projects should use the same identity.
- Defaults are `Dev` / `dev@localhost`, overridable at script invocation via
  `--git-name`/`--git-email` CLI flags or `$GIT_USER_NAME`/`$GIT_USER_EMAIL`
  env vars.
- Requires `--rebuild` to bake the new profile scripts into the image.

## ADR-011: start-agent: build the image with `docker build --network=host`

**Date:** 2026-04-15
**Status:** Accepted

### Context

ADR-010's DOCKER-USER egress allowlist rejects every packet from the docker
bridge CIDR that isn't destined for tinyproxy, host Ollama, or bridge DNS.
Runtime containers honor `HTTPS_PROXY` and route their traffic through
tinyproxy, but `docker build` RUN steps also execute inside short-lived
containers attached to the bridge — and those build containers have no proxy
env set. The first install step (`apt-get update && apt-get install ...`) hit
the REJECT rule immediately:

```
W: Failed to fetch http://deb.debian.org/debian/dists/bookworm/InRelease
   Could not connect to deb.debian.org:80 — connect (113: No route to host)
```

Three options to unstick the build:

1. **Route the build through tinyproxy via `--build-arg HTTP_PROXY=...`.**
   Docker treats the proxy vars as predefined build ARGs and exposes them as
   env vars during RUN. In practice this is unreliable on the legacy builder:
   some tools (notably apt) only consult the lowercase `http_proxy`, and
   propagating both cases through every RUN step means either touching the
   Dockerfile or writing an `/etc/apt/apt.conf.d/99proxy` shim.
2. **Temporarily drop the REJECT rule while building, then re-add.** Simple
   in theory, but introduces a timing window where every bridge-attached
   container is unfirewalled, plus a cleanup path that has to survive build
   failures and SIGINT.
3. **`docker build --network=host`.** RUN steps share the Colima VM's host
   network namespace. The VM's netns is not subject to the `FORWARD` chain
   (DOCKER-USER only fires for packets being routed between interfaces), so
   the build has full egress. Runtime containers still attach to the docker
   bridge via `docker run` and remain firewalled normally — the `--network`
   flag applies only to the build.

### Decision

Option 3. `docker build` is invoked with `--network=host` in `start-agent.sh`.

### Consequences

- Build-time network egress is unrestricted. Acceptable because we trust the
  Dockerfile — it's checked into the repo, the image is rebuilt only on
  explicit `--rebuild`, and the LLM running at runtime has no write path to
  the Dockerfile or the build invocation.
- Runtime egress enforcement is unchanged. Containers created by `docker run`
  attach to the bridge and pass through `DOCKER-USER` like before; the six
  smoke tests in `README.md` (default-deny, allowed-via-proxy,
  denied-via-proxy, Ollama carve-out, env sanity, hot-reload) all pass with
  this configuration.
- No Dockerfile changes needed to handle proxies, and no timing window where
  the bridge is unfirewalled. The whole interaction is local to the
  `docker build` line.
- If we ever migrate to BuildKit (the legacy builder is deprecation-warned on
  every run), the same flag works there too — BuildKit accepts
  `--network=host` and honors it identically for RUN steps. No lock-in.

## ADR-012: start-agent: omlx as an alternative local inference backend

**Date:** 2026-04-16
**Status:** Accepted

### Context

`start-agent.sh` hard-coded Ollama as the local inference backend. Ollama
binds to `localhost` by default; reaching it from the Colima VM requires
rebinding to `0.0.0.0`, which exposes the model server to every device on
the user's LAN. The `plans/ollama-host-firewall.md` plan addresses this with
a host-side pf firewall, but that adds operational complexity (anchor files,
LaunchDaemons, interface-scoped rules).

[omlx](https://github.com/jundot/omlx) is an MLX-based inference server for
Apple Silicon with an OpenAI-compatible API and built-in `--api-key` support.
When started with an API key, omlx returns 401/403 to unauthenticated
requests, so it can safely bind to `0.0.0.0` without a host-side firewall —
LAN peers that probe the port get rejected at the application layer.

### Decision

Add omlx as an alternative backend behind `--backend=omlx` (default remains
`ollama`). A single `case "$BACKEND"` dispatch block governs all
backend-specific behavior:

1. **Port.** `INFERENCE_PORT` is 11434 for Ollama, 8000 for omlx. All
   downstream references (iptables carve-out, preflight probe, OpenCode
   config, container env) use `$INFERENCE_PORT`.
2. **API key.** `OMLX_API_KEY` from the host env is passed into the
   container as an env var and written into the OpenCode provider config's
   `apiKey` field. Missing key is a warning, not a hard error (the user may
   run omlx without auth during local testing).
3. **Container env vars.** `OLLAMA_HOST` is only set for the `ollama`
   backend; `OMLX_HOST` and `OMLX_API_KEY` are set for `omlx`. Neither
   backend pollutes the other's namespace.
4. **OpenCode config.** The `ollama` and `omlx` provider entries use
   separate keys in `opencode.json` and coexist across backend switches.
5. **Preflight probe.** Ollama probes `/api/tags`; omlx probes `/v1/models`
   with a Bearer token if the key is set.

### Consequences

- Users on shared networks can use `--backend=omlx` and skip the host-side
  pf firewall entirely. The `plans/ollama-host-firewall.md` plan is
  unnecessary when using omlx.
- No Dockerfile changes. omlx runs on the macOS host; the container reaches
  it via the same iptables carve-out used for Ollama (just a different port).
- Adding future backends (llama.cpp, vLLM, etc.) means adding one more
  `case` branch in the dispatch, one conditional in the env/preflight/config
  blocks, and one doc section. The pattern scales linearly.

## ADR-013: start-agent: bootstrap `global-agent` so Node.js respects the proxy

**Date:** 2026-04-17
**Status:** Accepted

### Context

ADR-010's DOCKER-USER iptables rules reject all outbound traffic from the
container except to tinyproxy, the inference port, and bridge DNS. The
container sets `HTTP_PROXY` / `HTTPS_PROXY` so that proxy-aware tools (curl,
apt, wget) route through tinyproxy, and the allowlist controls which domains
are reachable.

Claude Code and OpenCode are Node.js applications. Node.js's built-in
`http`/`https` modules and the `fetch` implementation (backed by `undici`)
do **not** honor `HTTP_PROXY`/`HTTPS_PROXY` environment variables. When
Claude Code attempted to reach `api.anthropic.com`, the request went direct,
hit the DOCKER-USER REJECT rule, and failed — the agent could not
authenticate.

ADR-010's consequences section noted that "applications inside the container
that don't honor `HTTPS_PROXY` / `HTTP_PROXY` will simply fail to reach
anything." For CLI tools like curl this was fine; for the primary workload
(Claude Code) it was a showstopper.

### Decision

1. **Install `global-agent` globally in the Dockerfile** alongside the other
   npm packages (`npm install -g ... global-agent`). `global-agent` monkey-
   patches Node.js's `http.globalAgent` and `https.globalAgent` at require
   time to route requests through the proxy specified in
   `GLOBAL_AGENT_HTTP_PROXY`.
2. **Set `NODE_OPTIONS=--require global-agent/bootstrap`** as a container
   env var in `DOCKER_ENV_ARGS`. This causes every Node.js process (Claude
   Code, OpenCode, any npm scripts) to load `global-agent` before
   application code runs.
3. **Set `GLOBAL_AGENT_HTTP_PROXY`** to the tinyproxy URL
   (`http://$BRIDGE_IP:$TINYPROXY_PORT`). `global-agent` reads this to
   determine the proxy endpoint.
4. **Set lowercase `http_proxy`/`https_proxy`/`no_proxy`** alongside the
   uppercase variants. Some Node.js libraries (and Python's `urllib`) check
   lowercase; belt-and-suspenders.

### Consequences

- Claude Code and OpenCode API calls now route through tinyproxy and are
  subject to the domain allowlist. Authentication to `api.anthropic.com`
  works because `anthropic.com` is in the default allowlist.
- Every Node.js process in the container pays the `global-agent` require
  cost (~5ms). Negligible compared to network round-trips.
- `NO_PROXY` / `no_proxy` exempt `localhost`, `127.0.0.1`, the bridge IP,
  and the host IP — so local inference traffic (Ollama/omlx) still goes
  direct, as intended.
- `NODE_OPTIONS` is set at container env level (not baked into the image),
  so it applies to both `docker run` (new container) and `docker exec`
  (reattach) paths. The image remains usable without the proxy if someone
  runs it outside the firewalled VM.
- Requires `--rebuild` to install `global-agent` in the image.

### 2026-04-17 revision: replace `global-agent` with `NODE_USE_ENV_PROXY=1`

The original implementation — `npm install -g global-agent` plus
`NODE_OPTIONS=--require global-agent/bootstrap` — had compounding failure
modes:

1. Globally-installed npm packages aren't on Node's default require path
   (needs `NODE_PATH`).
2. `global-agent` v4 dropped the `bootstrap` submodule entry, so even with
   `NODE_PATH` set, `--require global-agent/bootstrap` throws
   `MODULE_NOT_FOUND`.
3. Every Node process in the container consequently crashed before any
   application code ran. Claude Code's Bun-native API client masked the
   breakage; its Node helpers (including the OAuth code-exchange path used
   by `/login`) crashed immediately and surfaced as a misleading
   `OAuth error: Request failed with status code 403`. OpenCode failed to
   launch outright.

Node 24 ships undici with first-class proxy support behind
`NODE_USE_ENV_PROXY=1`. Set that env var alongside `HTTP_PROXY`,
`HTTPS_PROXY`, and `NO_PROXY`; Node's built-in `fetch`, `http`, and
`https` then honor the proxy without any require-time wiring. Removes the
npm package, the `--require` preload, the `NODE_PATH` dependency, the
lowercase proxy-var duplicates, and `GLOBAL_AGENT_HTTP_PROXY` — one
officially-supported flag replaces the whole stack.

## ADR-014: start-agent: SearXNG-backed local websearch

**Date:** 2026-04-21
**Status:** Accepted

### Context

`permission.websearch` in `opencode.json` was hard-set to `"deny"` (ADR-010
era) because the only available backend was the Exa MCP, which routes all
queries through a third-party gateway — defeating the tinyproxy allowlist as
the single egress-control mechanism and leaking query content to Exa. The
plan was to replace it with a local gateway when one became practical.

SearXNG is a privacy-respecting, self-hosted meta-search engine with an
official docker image and a stable JSON API (`/search?format=json`). It is
also the documented search backend for Perplexica, so one SearXNG instance
can serve both the agent's MCP websearch tool and a future Perplexica
deployment on the same bridge.

### Decision

**Why a local search gateway at all.** The allowlist (tinyproxy filter) is
the single source of egress truth for the stack. A third-party search
gateway (Exa, Serper, Brave Search API, etc.) is an unconstrained egress
hole: any query can be routed through it, and the gateway operator sees every
search term. A local meta-search engine that fans out through tinyproxy keeps
all query routing under the allowlist's control.

**Why SearXNG specifically.** It is Perplexica's documented search backend
(pre-requisite for a future Perplexica deployment), ships an official docker
image (`docker.io/searxng/searxng`), exposes a stable JSON API, and supports
`outgoing.proxies` for routing its own fan-out through a proxy. No other
shortlisted option (direct-DDG-scraping MCP servers, Brave Search API shim)
cleanly satisfies both the Perplexica reuse requirement and the
allowlist-as-single-control requirement.

**Why SearXNG on the docker bridge, not host netns.** Running SearXNG's
container with `--network host` would put its fan-out traffic in the VM's
host network namespace, bypassing the `FORWARD` chain and therefore the
`CLAUDE_AGENT` iptables rules entirely. SearXNG would be able to reach any
upstream engine regardless of the allowlist — breaking the single-control
property. Running it as a bridge-attached container means its egress hits the
`DOCKER-USER → CLAUDE_AGENT` chain, and the tinyproxy `RETURN` rule
(`-d $BRIDGE_IP -p tcp --dport 8888`) covers SearXNG's outbound CONNECTs
the same way it covers the agent container's. Two additional iptables rules
are added: one RETURN for bridge→bridge:8080 (claude-agent MCP shim →
SearXNG JSON API) and one implicit coverage via the existing tinyproxy rule
(SearXNG → tinyproxy for fan-out, already allowed source-agnostically).

**Why `outgoing.proxies` in `settings.yml` and NOT `HTTPS_PROXY` env vars.**
Primary-source finding in SearXNG's `searx/network/client.py`: the httpx
client is built with an explicit `transport=SxngAsyncHTTPTransport(...)`.
Per httpx's documented behavior, passing `transport=` overrides the default
transport, which is also what handles env-var proxy pickup
(`AsyncHTTPTransport` reads `HTTPS_PROXY` automatically; a custom transport
does not). Result: `HTTPS_PROXY` set on the SearXNG container has no effect
on its outbound engine requests — they bypass tinyproxy silently. The working
knob is `outgoing.proxies.all//: http://<bridge-ip>:8888` in `settings.yml`,
which SearXNG passes as a `proxy=` argument to its custom transport. A future
contributor who sees the bridge env has `HTTPS_PROXY` set and assumes the
proxy is already wired would be wrong; this ADR names the failure mode so
that assumption isn't made silently.

**Why a custom ~40-line Python FastMCP shim over `ihor-sokoliuk/mcp-searxng`
(npm).** The community npm package is actively maintained and is in the
official `modelcontextprotocol/servers` list, but it bundles a second tool,
`web_url_read`, that fetches and Markdowns arbitrary URLs. That duplicates
opencode's native `webfetch` capability and — critically — sits outside
opencode's `permission.webfetch` control. The MCP-provided `web_url_read`
would bypass opencode's permission model entirely; opencode does not support
per-tool disabling within a given MCP server. The custom shim exposes exactly
one tool (`websearch`) with no URL-fetch side channel. Rejecting the more
popular package on security-posture grounds is the kind of decision that gets
silently reversed during a "let's use the standard package" cleanup; the
decision is documented here so it isn't.

**Why a narrow curated engine set with a matching allowlist (two-lock pair).**
The default SearXNG image enables 50+ engines spanning torrent trackers, art
sites, and social networks — each requiring its own allowlist entry and adding
attack surface for prompt-injection via engine-provided results. The seeded
`settings.yml` uses `use_default_settings.engines.keep_only` to whitelist
nine engines (Google, Bing, DuckDuckGo, Brave, Qwant, Wikipedia, arXiv,
GitHub code search, Stack Exchange) and disables everything else. Enabling a
new engine requires editing both `~/.claude-agent/searxng/settings.yml` AND
`~/.claude-agent/allowlist.txt` — two separate files, intentional friction so
"turn on a new engine" cannot happen via a single-file change.

**`--enable-local-search` is a single flag that gates three components.**
The flag controls (a) SearXNG container lifecycle, (b) the inter-container
iptables RETURN rule, and (c) the `mcp.searxng` block and
`permission.websearch: "allow"` in `opencode.json`. All three are applied or
removed together so the system never enters a state where the MCP config
references a missing container or the firewall blocks a live one.

**User-defined network `claude-agent-net` for inter-container DNS.** Docker's
embedded DNS (container-name resolution) only functions on user-defined
networks, not the default bridge. Both `claude-agent` and `searxng` are
attached to `claude-agent-net` (created idempotently) so `http://searxng:8080`
resolves correctly from inside `claude-agent`. Tinyproxy remains bound to
`$BRIDGE_IP` (default bridge gateway); containers on `claude-agent-net` reach
it via VM-internal routing between bridge interfaces, with a corresponding
`DOCKER-USER -s $AGENT_NET_CIDR -j CLAUDE_AGENT` jump rule ensuring the
tinyproxy RETURN rule fires. Containers not in the `claude-agent-net` network
(the default bridge path when `--enable-local-search` is off) are unaffected.

### Consequences

- `--enable-local-search` enables privacy-preserving websearch with all
  traffic governed by the existing tinyproxy allowlist. No new egress control
  mechanism is introduced.
- SearXNG's `~/.claude-agent/searxng/` directory survives `--rebuild`. The
  generated `secret_key` is stable; regenerating requires manually removing
  that directory.
- The SearXNG instance is also the pre-requisite for a future Perplexica
  deployment: point Perplexica's config at `http://searxng:8080` on the same
  bridge, add a CLAUDE_AGENT RETURN rule for perplexica→searxng:8080, done.
- `NO_PROXY=searxng` is added to `claude-agent`'s env so the Python MCP
  shim's direct HTTP call to `http://searxng:8080` bypasses tinyproxy (which
  would reject it — `searxng` is not on the domain allowlist, nor should it
  be, since it is a container name not a public host).
- Any engine that bypasses `outgoing.proxies` (e.g. one that opens a raw
  socket instead of going through the configured httpx transport) also bypasses
  the allowlist. The seeded engine list was chosen from engines that use the
  standard httpx transport; the verification step (`grep -rn 'httpx\.(get|post'`
  across engine source) should be re-run if the engine list is expanded.

## ADR-015: Seed a global container CLAUDE.md from a repo template

### Context

`~/.claude-containers/shared/` is bind-mounted to `/root/.claude/` in every
container spawned by `start-claude.sh` and `start-agent.sh`. Claude Code
auto-injects `~/.claude/CLAUDE.md` into every session's system prompt, but we
weren't populating that file, so models running inside containers routinely
formed wrong hypotheses about the environment: probing `/Users/<name>/.claude/`
(the macOS layout suggested by the cwd) instead of `/root/.claude/`; retrying
fetches of `github.com` without recognizing the hostname-allowlist rejection;
fighting the read-only sandbox mounts on `/tmp` and `/root/.cache` instead of
retargeting to `$TMPDIR`. The repo-root `CLAUDE.md` documents these facts but
is only auto-injected when cwd is under this repo, so projects opened
elsewhere in the container got none of it.

### Decision

Keep a single `templates/global-claude.md` in the repo, structured as common
facts → claude-agent specifics → claude-dev exceptions. On startup, both
scripts copy the template to `~/.claude-containers/shared/CLAUDE.md` if that
path does not already exist. User edits are preserved. A
`--reseed-global-claudemd` flag on each script overwrites unconditionally so
template updates can be pulled in explicitly. No dynamic regeneration (no
splicing of the current backend, current allowlist, etc.) in this iteration —
static content, one source of truth, consequences below.

### Consequences

- New container sessions get environment context in their system prompt
  regardless of cwd, reducing misdirected probing and retry loops.
- With seed-if-missing semantics, users who ran the script before this change
  won't pick up template updates without passing `--reseed-global-claudemd`.
  Acceptable tradeoff: the file is user-editable, so silent overwrites would
  destroy local customization.
- Static claims can go stale. Any change to default allowlist contents, the
  set of supported backends, or sandbox behavior must be reflected in
  `templates/global-claude.md` as part of the same change.
- Baking the file into the Dockerfile would not work: the shared bind mount
  overrides `/root/.claude/` at runtime, so anything written there at image
  build time is hidden. Host-side seeding is the only path that's actually
  read.
- OpenCode (in `start-agent.sh` only) is seeded from the same template into
  `~/.claude-agent/opencode-config/AGENTS.md` and referenced from
  `opencode.json`'s `instructions` field. The trailing `claude-dev` exceptions
  section is stripped on the OpenCode copy via `awk` — it describes
  bubblewrap / `$TMPDIR` constraints that only apply inside `start-claude.sh`,
  and OpenCode never runs there. `--reseed-global-claudemd` reseeds both the
  Claude Code and OpenCode copies.

## ADR-016: start-agent: Vane as default-on AI research UI; SearXNG + Vane default-on

**Date:** 2026-04-22
**Status:** Accepted

### Context

ADR-014 introduced SearXNG as an opt-in feature (`--enable-local-search`). In
practice, the search stack is lightweight (one extra container on the bridge)
and broadly useful — there is no good reason to skip it. Additionally, Vane
(formerly Perplexica, `itzcrazykns1337/vane:slim-latest`) was identified as a
human-facing AI research UI that complements the agent's MCP websearch tool:
SearXNG serves both. The prior opt-in flag created friction and left the
feature undiscovered by default.

### Decision

**Flip search from opt-in to default-on.** SearXNG and Vane now start
alongside `claude-agent` on every run. `--disable-search` (env:
`CLAUDE_AGENT_DISABLE_SEARCH=1`) suppresses both. `--enable-local-search` is
kept as a no-op deprecated alias with a warning to ease the transition.

**Vane container wired to SearXNG.** Vane runs as `docker.io/itzcrazykns1337/
vane:slim-latest` on the same `claude-agent-net` user-defined network, with
`SEARXNG_API_URL=http://searxng:8080` pre-configured. Port 3000 is bound to
the macOS host (`-p 3000:3000`). Vane's data directory
(`~/.claude-agent/vane-data/`) is bind-mounted so LLM configuration and
search history survive `--rebuild` (which removes the container but not the
volume directory).

**LLM pre-configuration is not possible via env var.** The Vane image does not
expose an env var for the Ollama/omlx endpoint. Users configure it once via
the web UI at `http://localhost:3000` on first access; the setting persists in
the data volume. A startup note directs new users there.

**No new firewall rules needed.** Vane → SearXNG traffic uses the existing
intra-`AGENT_NET_CIDR` port-8080 RETURN rule. Vane → Ollama/omlx uses the
existing `HOST_IP:INFERENCE_PORT` RETURN rule, which has no source-CIDR
restriction — it covers all containers routed through the CLAUDE_AGENT chain.

### Consequences

- All new `claude-agent` runs get a local search stack and a browser-accessible
  AI research UI without any flags.
- Port 3000 on the macOS host is occupied by default. Users with a local dev
  server on 3000 must pass `--disable-search` or stop Vane manually.
- `--rebuild` removes both the SearXNG and Vane containers; data persists in
  `~/.claude-agent/searxng/` and `~/.claude-agent/vane-data/`.
- First-time Vane use requires a one-time manual LLM endpoint configuration
  in the web UI; subsequent runs restore the persisted config automatically.
