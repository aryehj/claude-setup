---
name: Slim claude-agent image and rebuild path
description: Trim unused deps, add a --reset-container flag, and persist BuildKit layer cache to reduce per-rebuild bandwidth.
---

# Slim claude-agent image and rebuild path

## Status

- [x] Phase 1: Drop unused dependencies
- [x] Phase 2: Add `--reset-container` flag
- [ ] Phase 3: Persist BuildKit layer cache across rebuilds

## Context

`start-agent.sh --rebuild` consumes ~600MB of network egress per invocation on
the user's monthly bandwidth cap. The rebuild path:

1. Deletes the `claude-agent:latest` image (and the container).
2. Runs `docker build --network=host` against
   `dockerfiles/claude-agent.Dockerfile` (`start-agent.sh:881`), which:
   - `apt-get update && apt-get install ...` then `apt-get upgrade -y`
     (Dockerfile lines 17-25)
   - NodeSource `setup_lts.x` + `apt-get install nodejs` (lines 39-40)
   - `npm install -g npm@latest @anthropic-ai/sandbox-runtime opencode-ai@latest`
     (lines 42, 61)
   - `claude.ai/install.sh` (line 53)
   - `uv pip install 'mcp[cli]' httpx` for the SearXNG MCP shim (line 75)
3. Optionally pulls `docker.io/searxng/searxng` if that container is also gone.

The Colima VM is preserved by default on `--rebuild` (the VM-delete prompt is
opt-in, `start-agent.sh:593-601`), so docker's local layer cache *should*
already help — but `docker image rm` plus normal cache eviction is enough that
re-runs still re-download the apt and npm payloads.

The same package list appears inline in `start-claude.sh:200-206`. Slimming
should keep the two scripts in lockstep so `start-claude.sh` remains
"functionally equivalent" to the agent image (per the Dockerfile header
comment, lines 7-9).

There is no flag today for "wipe container/state without touching the image" —
users reach for `--rebuild` even when only the container needs to be reset.

## Goals

- Reduce the bytes downloaded on a `--rebuild` against an existing VM.
- Reduce the runtime cost of common state-reset operations by avoiding
  unnecessary image rebuilds.
- Keep `start-claude.sh` and `claude-agent.Dockerfile` package lists in sync.
- No regression in features that depend on the dropped tools (Claude Code
  sandbox, npm/native-module installs, NodeSource setup, sandbox-runtime).

## Approach

Three orthogonal levers, ordered by effort:

1. **Strip the package list** of clearly-unused deps; verify by smoke test.
   Smallest blast radius, immediate win.
2. **Add a `--reset-container` flag** so most reset workflows stop touching
   the image at all. Eliminates the bandwidth cost for the common case
   instead of optimizing it.
3. **Persist BuildKit cache** to a host directory so genuine image rebuilds
   reuse layer cache even when the image tag has been deleted. Bigger
   moving parts, but uniquely covers the "I edited the Dockerfile" case.

Phases are independent; each ships value on its own and they compose.

## Unknowns / To Verify

- **Is `build-essential` actually needed at runtime?** It exists in the
  package list (~250MB installed) but no `apt-get install`-time step compiles
  C code. Candidates that *might* compile native modules: the npm globals
  (`@anthropic-ai/sandbox-runtime`, `opencode-ai`) and the `mcp[cli]` /
  `httpx` pip installs. Verify by building without `build-essential` and
  re-running each install step; if all succeed and the resulting CLIs work,
  it can be dropped. Affects Phase 1 step 2.
- **Is `gnupg` needed beyond NodeSource setup?** NodeSource's `setup_lts.x`
  uses `gpg` to verify the apt key. After Node is installed, nothing else in
  the image appears to need it. Verify by removing it from the apt list and
  letting NodeSource install it transitively if needed (it does, on first
  use); or move the Node install before the package strip-down. Affects
  Phase 1 step 2.
- **Does Colima's docker daemon ship buildx by default, and is
  `type=local` cache export supported?** Recent Colima versions bundle
  buildx, but the version in the user's environment is unverified. Check
  with `docker buildx version` and `docker buildx build --help | grep -A2
  cache-to` inside the running VM context. Affects Phase 3.

## Phase 1: Drop unused dependencies

### Steps

1. In `dockerfiles/claude-agent.Dockerfile` (lines 17-25) and
   `start-claude.sh` (lines 200-206), remove from the apt install list:
   - `python3-pip` — `uv` is the Python toolchain in this image
     (UV_INSTALL_DIR, UV_CACHE_DIR, UV_PROJECT_ENVIRONMENT are all wired up;
     pip is never invoked).
   - `libseccomp-dev` — only the runtime library `libseccomp2` is needed by
     `@anthropic-ai/sandbox-runtime`; the `-dev` headers are build-time.
   - `wget` — `curl` is also installed and is what the rest of the image
     uses (NodeSource, uv, claude installer all curl).
   - `unzip` — no current install path needs it; `tar`/`gzip` from coreutils
     handle the artefacts that *are* downloaded.
2. Conditionally drop, after a smoke-test build:
   - `build-essential` — see Unknowns. Try removing; if `npm install -g
     opencode-ai @anthropic-ai/sandbox-runtime` and `uv pip install
     'mcp[cli]' httpx` both succeed and the resulting binaries run, drop it
     for real. Otherwise leave with a comment explaining what compiles
     against it.
   - `gnupg` — see Unknowns. NodeSource may pull it in transitively; if a
     clean build still succeeds without listing it explicitly, drop it.
3. Update `README.md:67-69` to remove the dropped tools from the
   "what's in the container" table.
4. Smoke-test: `start-agent.sh --rebuild` against a clean image and confirm
   that:
   - The build completes.
   - `claude --version`, `opencode --version`, `uv --version`, `node
     --version`, `git --version`, `jq --version`, `rg --version`, `fdfind
     --version` all run inside the container.
   - The SearXNG MCP shim still imports cleanly:
     `/opt/searxng-mcp/venv/bin/python -c 'import mcp, httpx'`.
   - The Claude Code sandbox actually launches a sandboxed bash command
     (uses bubblewrap + libseccomp2 + sandbox-runtime).

### Acceptance criteria

- Image size (`docker image inspect claude-agent:latest --format
  '{{.Size}}'`) is smaller than before; record before/after in the commit
  message.
- All smoke-test commands above succeed.
- `start-claude.sh` and the Dockerfile still install the same set of
  packages as each other.

---

## Phase 2: Add `--reset-container` flag

### Steps

1. Add `RESET_CONTAINER=false` next to `REBUILD=false` and
   `RELOAD_ALLOWLIST=false` (`start-agent.sh:22-23`).
2. Add a `--reset-container` case to the arg parser
   (`start-agent.sh:94-96`). Reject the combination
   `--reset-container && --rebuild` with a clear error — they are mutually
   exclusive: `--reset-container` is the cheap subset.
3. Add `--reset-container` to the `Usage:` block at the top of the script
   (`start-agent.sh:11`) and the `--help` printout (around line 45). Wording
   suggestion: "Remove the project container (and SearXNG container) but
   keep the image and VM. Cheaper than --rebuild when you only need to
   reset container state."
4. Branch the existing rebuild logic at `start-agent.sh:593-639` so that
   the `--reset-container` path:
   - Skips the VM-delete prompt (lines 593-601) entirely.
   - Skips the `docker image rm` block (lines 637-639).
   - Still runs the `docker rm -f $CONTAINER_NAME` and (if local search is
     enabled) `docker rm -f $SEARXNG_CONTAINER` removals.
   - Falls through to the normal "build image if missing" check at line
     872, which will be a no-op since the image is still present.
5. The shared host state under `~/.claude-containers/shared/` and
   `~/.claude-agent/` should NOT be wiped — `--rebuild` doesn't wipe it
   either, and that state (auth, allowlist, opencode config) is what users
   want to preserve across resets.
6. Update `CLAUDE.md` "start-agent.sh key decisions" to add a bullet
   describing the new flag, alongside the existing "--rebuild semantics"
   bullet.
7. Update `README.md` if it documents the flag set.

### Acceptance criteria

- `start-agent.sh --reset-container` removes the container, leaves the
  image intact (`docker image inspect claude-agent:latest` still
  succeeds), and finishes by attaching to a fresh container — with no
  network egress for the image build itself.
- `start-agent.sh --reset-container --rebuild` exits non-zero with a
  helpful error.
- `--rebuild` behavior is unchanged.

---

## Phase 3: Persist BuildKit layer cache across rebuilds

### Steps

1. Verify the prerequisite: inside the running Colima VM, `docker buildx
   version` and `docker buildx build --help | grep -E 'cache-(to|from)'`
   both succeed. If buildx is missing, document the minimum Colima/Docker
   version required and either gate Phase 3 behind a version check or note
   it in the Unknowns section before proceeding.
2. Define a host-side cache directory next to the existing `~/.claude-agent/`
   state — e.g. `BUILDCACHE_DIR="$HOME/.claude-agent/buildcache"`. Create it
   in the same place `~/.claude-agent/allowlist.txt` is seeded (search for
   `mkdir -p` near `ALLOWLIST_FILE` in `start-agent.sh`).
3. Mount the cache dir into the VM. Colima already bind-mounts the user's
   home directory (`virtiofs` mount, `start-agent.sh:553`), so a path under
   `$HOME` is already visible inside the VM with the same path. Confirm
   with `colima ssh -- ls "$BUILDCACHE_DIR"` before relying on it.
4. Replace the `docker build --network=host -t "$IMAGE_TAG" -f
   "$DOCKERFILE_PATH" "$DOCKERFILE_DIR"` invocation
   (`start-agent.sh:881-882`) with a buildx invocation that exports and
   imports a local cache:
   - `docker buildx build --network=host`
   - `--cache-to=type=local,dest=$BUILDCACHE_DIR,mode=max`
   - `--cache-from=type=local,src=$BUILDCACHE_DIR` (no-op on first run; the
     directory just won't have any cache manifests yet)
   - `--load` so the resulting image lands in the local docker image store
     under `$IMAGE_TAG` (buildx defaults to not loading)
   - `-t "$IMAGE_TAG" -f "$DOCKERFILE_PATH" "$DOCKERFILE_DIR"`
5. If buildx requires a builder instance for `type=local` cache, create one
   lazily, e.g. `docker buildx create --use --name claude-agent-builder
   --driver docker-container 2>/dev/null || docker buildx use
   claude-agent-builder`. The `docker-container` driver is needed for
   `type=local` cache export; the default `docker` driver does not support
   it.
6. Add a `--prune-buildcache` flag (or document an equivalent manual step)
   that wipes `$BUILDCACHE_DIR` for users who want to force a clean
   rebuild. `--rebuild` itself should NOT wipe the cache by default — that
   would defeat the purpose.
7. Document the cache directory in `CLAUDE.md` under "start-agent.sh key
   decisions" and note: cache survives `--rebuild` and `--reset-container`,
   does not survive Colima VM deletion (in fact, is on the host so it
   does), invalidates per-layer when the Dockerfile changes upstream of
   that layer.
8. Smoke-test:
   - First `--rebuild` populates the cache directory (non-empty after).
   - Second `--rebuild` shows "CACHED" on most layers in buildx output.
   - Editing a late-stage Dockerfile line (e.g. the SearXNG MCP COPY)
     invalidates only layers from that point forward.

### Acceptance criteria

- A second `--rebuild` against an unchanged Dockerfile completes
  significantly faster and downloads materially less than the first
  (record rough numbers in the commit / PR description).
- `~/.claude-agent/buildcache/` exists and is non-empty after the first
  build.
- VM deletion + recreate followed by `--rebuild` still benefits from the
  cache, since it lives on the host.

---

## Notes

- An optional Phase 1.5 — drop `apt-get upgrade -y` (Dockerfile line 23,
  `start-claude.sh:206`) — was discussed but is not part of this plan; it
  has security implications (no exposed services, but still). Leave alone
  unless a follow-up explicitly motivates it.
- Phase 3 alternative: registry-based cache (`type=registry,ref=...`) lets
  the cache be shared across machines, but requires a registry the user
  controls and credentials in the build path. Not worth it for a
  single-developer setup.
- Phases 1-3 do not touch `research.py`, which has its own image lifecycle
  and bandwidth profile. If the user wants similar slimming there, file a
  separate plan.
- After each phase, re-run `tests/test-agent-firewall.sh` (per
  `CLAUDE.md` Layout) to confirm none of these changes broke the egress
  allowlist semantics.
