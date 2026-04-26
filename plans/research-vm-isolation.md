# Vane/SearXNG Isolation into research.py

## Status

- [x] Phase 1: Create research script with isolated Colima VM (implemented as `research.py`)
- [x] Phase 2: Remove Vane from start-agent.sh (keep SearXNG)
- [ ] Phase 3: Update documentation

## Context

`start-agent.sh` currently manages three containers on the same Colima VM (`claude-agent` profile):
- `claude-agent`: Claude Code + OpenCode CLI
- `searxng`: Privacy-respecting meta-search engine — load-bearing for OpenCode's `websearch` permission via the SearXNG MCP shim baked into the claude-agent image at `/opt/searxng-mcp/server.py`
- `vane`: AI research UI (formerly Perplexica) — *not* used by OpenCode; only consumed via browser at `localhost:3000`

All three share the `claude-agent-net` docker network and the same egress firewall (tinyproxy + iptables CLAUDE_AGENT chain). Vane is the loose coupling here — it shares SearXNG with OpenCode but is otherwise independent of the agent container, and its presence is the reason port 3000 leaves the VM and the reason `--disable-search` exists.

The user wants Vane in its own isolated environment with:
- Its own Colima VM (network-isolated from `claude-agent`)
- Its own SearXNG instance (independent of the SearXNG that OpenCode uses)
- Its own egress firewall with a permissive research-oriented allowlist
- Access to local LLM (Ollama/omlx) on the host
- Browser access via localhost:3000

**Important**: SearXNG stays in `start-agent.sh` because OpenCode's websearch tool is backed by it via the MCP shim at `/opt/searxng-mcp/server.py` (see `start-agent.sh` lines 1017-1024 and `dockerfiles/claude-agent.Dockerfile` lines 63-75). Removing SearXNG from `start-agent.sh` would break OpenCode websearch. Only Vane and Vane-specific complexity is removed.

## Goals

- New `research.py` script that spins up an isolated Vane+SearXNG environment (implemented; see Phase 1 note)
- Remove Vane (and only Vane) from `start-agent.sh`; keep SearXNG and the MCP shim
- `start-agent.sh` and `research.py` can run simultaneously without interference
- OpenCode's `websearch` tool continues to work in `start-agent.sh` (backed by the SearXNG that remains)
- Vane accessible at `http://localhost:3000` from the host browser, fed by its *own* dedicated SearXNG
- LLM configuration in Vane points to host Ollama/omlx

## Unknowns / To Verify

1. **Vane image's internal port binding**: The current setup uses `-p 3000:3000`. Verify Vane's Dockerfile exposes port 3000. **How to verify**: `docker inspect docker.io/itzcrazykns1337/vane:slim-latest | jq '.[0].Config.ExposedPorts'`. **Affects**: Phase 1 port mapping.

2. **SearXNG container-to-container networking**: With Vane and SearXNG in the same container vs. separate containers on a user-defined network. **How to verify**: Check if Vane can reach `http://localhost:8080` when both services run in one container, or if they need `http://searxng:8080` via docker DNS. **Affects**: Phase 1 container design. Separate containers is safer and mirrors existing architecture.

---

## Phase 1: Create research.sh with isolated Colima VM (Opus recommended)

> **Implemented.** This phase was completed as `research.py` (Python, stdlib-only)
> rather than `research.sh` (bash). See `plans/implemented-research-python.md` for
> the rationale and ADR-018 for the evaluation. The steps below are preserved for
> historical reference; actual implementation details are in `research.py`.

### Steps

1. **Create `research.sh`** at the repo root with the following structure:
   - Argument parsing: `--rebuild`, `--reload-allowlist`, `--backend=ollama|omlx`, `--memory=`, `--cpus=`
   - Constants: `COLIMA_PROFILE="research"`, `CONTAINER_NAME_VANE="research-vane"`, `CONTAINER_NAME_SEARXNG="research-searxng"`, `RESEARCH_NET_NAME="research-net"`, `VANE_PORT=3000`, `SEARXNG_PORT=8080`
   - Host-side dirs: `~/.research/allowlist.txt`, `~/.research/searxng/settings.yml`, `~/.research/vane-data/`

2. **Seed a permissive research allowlist** at `~/.research/allowlist.txt` on first run. Include:
   - Search engines: `google.com`, `bing.com`, `duckduckgo.com`, `search.brave.com`, `www.google.com`
   - Docs/references: `*.wikipedia.org`, `*.stackexchange.com`, `*.stackoverflow.com`, `docs.*`, `developer.*`, `api.*`
   - News/periodicals: `*.nytimes.com`, `*.washingtonpost.com`, `*.bbc.com`, `*.bbc.co.uk`, `*.reuters.com`, `*.theguardian.com`, `*.economist.com`
   - Academic: `arxiv.org`, `*.arxiv.org`, `scholar.google.com`, `*.semanticscholar.org`, `*.researchgate.net`, `*.jstor.org`, `*.doi.org`, `*.ncbi.nlm.nih.gov`
   - Code hosts (read-only via raw access): `raw.githubusercontent.com`, `codeload.github.com`, `gist.githubusercontent.com`, `gitlab.com` (for raw file URLs)
   - CDNs/static: `*.cloudflare.com`, `*.fastly.net`, `*.akamaized.net`, `cdn.*`
   - Misc research: `*.archive.org`, `*.reddit.com`, `*.ycombinator.com`

3. **Colima VM lifecycle** (`research` profile):
   - `colima start --profile research` with `--network-address` (same pattern as `claude-agent`)
   - **No custom image is built.** `research.sh` only orchestrates `docker run` against stock upstream images (`docker.io/searxng/searxng`, `docker.io/itzcrazykns1337/vane:slim-latest`). There is no `dockerfiles/research-*.Dockerfile` and no equivalent of the `claude-agent.Dockerfile` heavy base — Vane and SearXNG bring their own minimal runtimes.
   - Default sizing: **2 GiB RAM, 2 CPUs**. The VM only hosts two long-running web app containers (SearXNG ~150 MB, Vane Next.js ~300-500 MB) plus Colima/docker overhead. No build steps, no Node/Python toolchain, no interactive shell, no LLM (LLM calls go to the host).
   - Overridable via `RESEARCH_MEMORY` / `RESEARCH_CPUS` env vars or `--memory=` / `--cpus=` flags

4. **tinyproxy installation and config** in the VM:
   - Same pattern as `start-agent.sh` lines 613-656
   - Generate filter file from `~/.research/allowlist.txt`
   - `FilterDefaultDeny Yes`, `FilterExtended Yes` (bookworm's tinyproxy 1.11.1)
   - Listen on `$BRIDGE_IP:8888`

5. **iptables RESEARCH chain**:
   - Create `RESEARCH` chain, flush and repopulate atomically
   - Jump from `DOCKER-USER` for bridge CIDR and `RESEARCH_NET_CIDR`
   - Allow: established/related, proxy port, inference port, DNS, inter-container 8080
   - REJECT everything else

6. **SearXNG settings.yml seed** at `~/.research/searxng/settings.yml`:
   - Same structure as current `start-agent.sh` lines 559-586
   - `outgoing.proxies.all://` pointing to `http://$BRIDGE_IP:8888`
   - Engines enabled: google, bing, duckduckgo, brave, wikipedia, arxiv, google_scholar

7. **Docker network creation**: `docker network create research-net` if it doesn't exist

8. **SearXNG container**: 
   ```bash
   docker run -d \
     --name research-searxng \
     --network research-net \
     -v ~/.research/searxng/settings.yml:/etc/searxng/settings.yml:ro \
     docker.io/searxng/searxng
   ```

9. **Vane container**:
   ```bash
   docker run -d \
     --name research-vane \
     --network research-net \
     --add-host=host.docker.internal:host-gateway \
     -p 3000:3000 \
     -e "SEARXNG_API_URL=http://research-searxng:8080" \
     -v ~/.research/vane-data:/home/vane/data \
     docker.io/itzcrazykns1337/vane:slim-latest
   ```

10. **Inference backend probe** (non-fatal): same pattern as `start-agent.sh` lines 722-751, probing Ollama at `$HOST_IP:11434` or omlx at `$HOST_IP:8000`

11. **Output on success**:
    ```
    ==> Research environment ready
        Vane    : http://localhost:3000
        SearXNG : http://localhost:8080 (internal)
        LLM     : configure at http://localhost:3000 → Settings → LLM
                  use http://host.docker.internal:11434 (Ollama) or
                  http://host.docker.internal:8000/v1 (omlx)
    ```

12. **`--rebuild` behavior**: Remove `research-vane`, `research-searxng` containers. With confirmation, also remove the `research` Colima VM.

13. **`--reload-allowlist` fast path**: Regenerate tinyproxy filter, SIGHUP tinyproxy, exit. Does not restart containers.

### Files

- `research.sh` (new, ~400-500 lines)
- `~/.research/allowlist.txt` (seeded on first run)
- `~/.research/searxng/settings.yml` (seeded on first run)
- `~/.research/vane-data/` (created on first run)

### Testing

1. Run `research.sh` from a fresh state (no `~/.research/` directory)
2. Verify VM starts: `colima list` shows `research` profile running
3. Verify containers: `colima ssh -p research -- docker ps` shows `research-vane` and `research-searxng`
4. Verify firewall: from inside Vane container, `curl -I https://google.com` should succeed; `curl -I https://not-on-allowlist.com` should fail
5. Open `http://localhost:3000` in browser, verify Vane UI loads
6. In Vane settings, configure LLM endpoint as `http://host.docker.internal:11434` (Ollama) and verify AI features work
7. Test `--reload-allowlist`: add a domain to `~/.research/allowlist.txt`, run `research.sh --reload-allowlist`, verify the domain is now reachable
8. Test `--rebuild`: run `research.sh --rebuild`, verify containers are recreated

---

## Phase 2: Remove Vane from start-agent.sh (keep SearXNG)

SearXNG, the `claude-agent-net` user-defined network, the SearXNG MCP shim in the Dockerfile, and the OpenCode `websearch=allow` + `mcp.searxng` config block all stay. Only Vane and Vane-specific complexity is removed.

### Steps

1. **Decide the fate of `--disable-search`**: With Vane gone, `--disable-search` would only control SearXNG. Two options:
   - **Option A (recommended)**: Keep `--disable-search` working but rename internally — the flag still skips SearXNG (and therefore disables OpenCode websearch). User-facing semantics unchanged. `LOCAL_SEARCH_ENABLED` variable name remains accurate.
   - **Option B**: Remove `--disable-search` entirely, make SearXNG always-on. Simpler but removes an opt-out for users who want to skip SearXNG entirely.
   - The plan assumes Option A. If Option B is preferred at implementation time, additionally remove items in step 2 below and unconditional-ize the `if $LOCAL_SEARCH_ENABLED` blocks that remain after Vane removal.

2. **Remove Vane-only variables** (line 195-197):
   - Remove `VANE_CONTAINER`, `VANE_DATA_DIR`, `VANE_PORT`
   - Keep `SEARXNG_CONTAINER`, `SEARXNG_DIR`, `SEARXNG_SETTINGS_FILE`

3. **Remove Vane from `--rebuild` cleanup** (lines 493-496):
   - Delete only the `vane` container rm block; keep the `searxng` rm block intact

4. **Remove Vane container lifecycle** (lines 799-823):
   - Delete the entire `if $LOCAL_SEARCH_ENABLED; then ... fi` block for Vane
   - SearXNG block immediately above (lines 775-797) stays

5. **Remove Vane references in startup output** (lines 1181-1182):
   - Delete the `vane    : http://localhost:$VANE_PORT (AI research UI)` echo line
   - Keep the `search  : SearXNG on $AGENT_NET_NAME` line

6. **Update help text** (lines 56-57, 82, 13):
   - Update `--disable-search` description: change "Skip SearXNG and Vane containers" to "Skip SearXNG container (also disables OpenCode websearch)"
   - Update `CLAUDE_AGENT_DISABLE_SEARCH` env var description similarly
   - Remove `[--disable-search]` from usage line if Option B; otherwise keep

7. **Update CLAUDE.md project doc** (search for `Vane runs alongside SearXNG by default` block in `CLAUDE.md`):
   - Delete the entire `**Vane runs alongside SearXNG by default.**` paragraph
   - Update the `**SearXNG-backed websearch runs by default.**` paragraph: replace `Pass --disable-search (env: CLAUDE_AGENT_DISABLE_SEARCH=1) to skip both SearXNG and Vane` with `Pass --disable-search to skip SearXNG (which also disables OpenCode websearch)`. Drop the `--enable-local-search` deprecation sentence if Option B.

### What does NOT change

- `dockerfiles/claude-agent.Dockerfile` lines 63-75 (the SearXNG MCP shim copy + venv) — stays
- `dockerfiles/searxng-mcp/server.py` — stays
- `claude-agent-net` user-defined network creation (lines 535-551) — stays (still needed for claude-agent ↔ searxng DNS)
- iptables intra-network rule for port 8080 (lines 701-705) — stays (still needed for claude-agent → searxng MCP path)
- SearXNG settings.yml seed and drift-check (lines 554-610) — stays
- SearXNG container lifecycle (lines 775-797) — stays
- OpenCode config `mcp.searxng` injection (lines 1017-1024) — stays
- `NO_PROXY=...,searxng` (line 1082) — stays
- `NETWORK_ARGS=(--network "$AGENT_NET_NAME")` for claude-agent (lines 1172-1175) — stays
- Search engine entries in the allowlist (lines 283-358) — stays (needed for SearXNG fan-out)

### Files

- `start-agent.sh` (modify, remove ~30 lines: Vane container block, Vane variables, Vane rebuild cleanup, Vane echo lines)
- `CLAUDE.md` (modify, remove the Vane paragraph)

### Testing

1. Run `start-agent.sh --rebuild` and verify it completes without errors
2. Verify `claude-agent` and `searxng` containers exist; `vane` does NOT (`docker ps`)
3. Verify port 3000 is no longer bound on the host (`lsof -iTCP:3000 -sTCP:LISTEN` empty)
4. Inside the container, run a quick OpenCode websearch (e.g. `opencode run "search the web for latest python release"`) and verify results return — proves the SearXNG MCP path still works
5. Verify `--disable-search` still skips SearXNG (and therefore websearch) if Option A retained
6. Run `start-agent.sh` (no rebuild) and verify re-attach works

---

## Phase 3: Update documentation

### Steps

1. **Update CLAUDE.md** (in addition to Vane-paragraph removal already covered in Phase 2):
   - Add `research.py` to the Layout section (already present if added during Phase 1 implementation)
   - Add a new "research.py key decisions" section after the "start-agent.sh key decisions" section (or verify it exists from Phase 1 implementation), explaining: separate Colima profile for VM-level isolation, dedicated SearXNG (not shared with claude-agent), permissive research-oriented allowlist, host-port 3000 for browser access, LLM via host.docker.internal

2. **Update ADR.md**:
   - Add ADR-021 documenting the extraction of Vane from `start-agent.sh` into standalone `research.py`. Reference the design choice: Vane was always a browser-only consumer of SearXNG, never accessed by OpenCode, so isolating it cleanly separates "agent's search backend" from "user-facing research UI". Note that the SearXNG that remains in `start-agent.sh` is dedicated to OpenCode and is *not* shared with `research.py`'s SearXNG (each runs in its own VM with its own egress firewall). Reference ADR-018 (which documents the Python language choice) as prior art.
   - Update ADR-016 status: "**Superseded by ADR-021** for the Vane portion. The default-on SearXNG decision still stands for OpenCode's websearch backend."
   - Leave ADR-014 unchanged (still describes the SearXNG architecture that remains in start-agent.sh)

3. **Update README.md**:
   - Add a `research.py` usage section (basic invocation, `--rebuild`, `--reload-allowlist`, allowlist file location)
   - Note that running both `start-agent.sh` and `research.py` simultaneously is supported

### Files

- `CLAUDE.md` (modify)
- `ADR.md` (modify)
- `README.md` (modify)

### Testing

- Review documentation for accuracy and completeness
- Verify ADR cross-references resolve correctly

---

## Notes

- **Simultaneous operation**: `start-agent.sh` (profile `claude-agent`) and `research.py` (profile `research`) use separate Colima VMs, so they can run concurrently without interference. They share host ports differently: `start-agent.sh` uses no host ports by default; `research.py` binds port 3000.

- **Port conflict**: If another service uses port 3000 on the host, add a `--port=` flag to `research.py` to allow override.

- **Data persistence**: `~/.research/vane-data/` persists Vane's LLM configuration across `--rebuild`. Consider documenting the one-time LLM setup step prominently.

- **Allowlist vs. LLM egress**: The local LLM (Ollama/omlx) is accessed via `host.docker.internal`, which is resolved via `--add-host` and goes through the `$HOST_IP:$INFERENCE_PORT` iptables RETURN rule, not through tinyproxy. This mirrors the existing `start-agent.sh` architecture.

- **Alternative considered**: Running Vane and SearXNG in a single container instead of two. Rejected because it would require a custom Dockerfile combining both images, increasing maintenance burden. The two-container approach reuses upstream images directly.

- **Why no custom base image**: Unlike `start-agent.sh`, which builds `claude-agent:latest` from `dockerfiles/claude-agent.Dockerfile` to host the Claude Code + OpenCode CLIs and a Python MCP shim, `research.py` runs only browser-facing services that ship as complete Docker Hub images. No agent runs *inside* the research VM — the user is the only consumer (via browser) and the LLM lives on the host. Adding a custom Dockerfile would be pure overhead.

- **Migration path**: Users who used Vane at `localhost:3000` should run `research.py` instead. OpenCode users see no change — websearch continues to work via the SearXNG that stays in `start-agent.sh`.

- **Two SearXNG instances by design**: `start-agent.sh`'s SearXNG (inside the `claude-agent` Colima VM, on `claude-agent-net`) serves OpenCode's MCP shim. `research.py`'s SearXNG (inside the `research` Colima VM, on `research-net`) serves Vane. They are independent: separate egress allowlists, separate VMs, separate settings.yml. This is intentional — coupling them back together would defeat the network-isolation goal.
