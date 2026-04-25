# Vane/SearXNG Isolation into research.sh

## Status

- [ ] Phase 1: Create research.sh with isolated Colima VM
- [ ] Phase 2: Remove Vane/SearXNG complexity from start-agent.sh
- [ ] Phase 3: Clean up Dockerfile and documentation

## Context

`start-agent.sh` currently manages three containers on the same Colima VM (`claude-agent` profile):
- `claude-agent`: Claude Code + OpenCode CLI
- `searxng`: Privacy-respecting meta-search engine
- `vane`: AI research UI (formerly Perplexica)

All three share the same `claude-agent-net` docker network and the same egress firewall (tinyproxy + iptables CLAUDE_AGENT chain). This coupling adds ~200 lines of complexity to `start-agent.sh` (lines 172-176, 192-197, 489-496, 535-551, 554-610, 701-705, 775-823, 1017-1028, 1082, 1172-1175) and means Vane/SearXNG cannot be used independently of the claude-agent container.

The user wants Vane in its own isolated environment with:
- Its own Colima VM (network-isolated from `claude-agent`)
- Its own SearXNG instance
- Its own egress firewall with a permissive research-oriented allowlist
- Access to local LLM (Ollama/omlx) on the host
- Browser access via localhost:3000

## Goals

- New `research.sh` script that spins up an isolated Vane+SearXNG environment
- Remove all Vane/SearXNG code from `start-agent.sh`
- Remove the SearXNG MCP shim from the claude-agent Dockerfile
- `start-agent.sh` and `research.sh` can run simultaneously without interference
- Vane accessible at `http://localhost:3000` from the host browser
- LLM configuration in Vane points to host Ollama/omlx

## Unknowns / To Verify

1. **Vane image's internal port binding**: The current setup uses `-p 3000:3000`. Verify Vane's Dockerfile exposes port 3000. **How to verify**: `docker inspect docker.io/itzcrazykns1337/vane:slim-latest | jq '.[0].Config.ExposedPorts'`. **Affects**: Phase 1 port mapping.

2. **SearXNG container-to-container networking**: With Vane and SearXNG in the same container vs. separate containers on a user-defined network. **How to verify**: Check if Vane can reach `http://localhost:8080` when both services run in one container, or if they need `http://searxng:8080` via docker DNS. **Affects**: Phase 1 container design. Separate containers is safer and mirrors existing architecture.

---

## Phase 1: Create research.sh with isolated Colima VM (Opus recommended)

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
   - Default sizing: 4 GiB RAM, 2 CPUs (lighter than claude-agent since this is just Vane+SearXNG)
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

## Phase 2: Remove Vane/SearXNG complexity from start-agent.sh

### Steps

1. **Remove CLI flags and variables**:
   - Remove `--enable-local-search` (already deprecated, just the warning on line 101)
   - Remove `--disable-search` flag handling (lines 102, 13, 56-57, 82)
   - Remove `CLI_DISABLE_SEARCH` variable (line 30)
   - Remove `CLAUDE_AGENT_DISABLE_SEARCH` references (lines 82, 172)
   - Remove `LOCAL_SEARCH_ENABLED` variable and all conditionals using it (lines 172-175)
   - Remove `SEARXNG_CONTAINER`, `SEARXNG_DIR`, `SEARXNG_SETTINGS_FILE`, `VANE_CONTAINER`, `VANE_DATA_DIR`, `VANE_PORT` (lines 192-197)

2. **Remove `--rebuild` Vane/SearXNG cleanup** (lines 489-496):
   - Delete the blocks that remove `searxng` and `vane` containers during rebuild

3. **Remove `claude-agent-net` network creation** (lines 535-551):
   - Delete the entire network creation block
   - Remove `AGENT_NET_NAME`, `AGENT_NET_CIDR` variables

4. **Remove SearXNG settings.yml seed** (lines 554-610):
   - Delete the `if $LOCAL_SEARCH_ENABLED` block that seeds and drift-checks `settings.yml`
   - Delete `SEARXNG_CONFIG_CHANGED` variable

5. **Remove inter-container iptables rule** (lines 701-705):
   - Delete the `if [ "$LOCAL_SEARCH_ENABLED" = "true" ]` rule for port 8080

6. **Remove SearXNG container lifecycle** (lines 775-797):
   - Delete the entire `if $LOCAL_SEARCH_ENABLED; then ... fi` block for SearXNG

7. **Remove Vane container lifecycle** (lines 799-823):
   - Delete the entire `if $LOCAL_SEARCH_ENABLED; then ... fi` block for Vane

8. **Simplify OpenCode config injection** (lines 1015-1028):
   - Change `perms.setdefault('websearch', 'deny')` to always apply (line 1026)
   - Remove the `if local_search_enabled` block that injects `mcp.searxng` (lines 1017-1024)
   - Remove the `local_search_enabled` parameter from the Python script entirely

9. **Clean up DOCKER_ENV_ARGS** (line 1082):
   - Remove `,searxng` from `NO_PROXY`

10. **Remove network args from docker run** (lines 1172-1175):
    - Delete `NETWORK_ARGS` variable and its usage in `docker run`

11. **Clean up allowlist seed** (lines 283-358):
    - Remove search engine entries (`search.brave.com`, `api.github.com` comment, etc.) that were only needed for SearXNG
    - Keep entries useful for webfetch and general dev work

12. **Update usage/help text** to remove `--disable-search` and related documentation

### Files

- `start-agent.sh` (modify, remove ~200 lines)

### Testing

1. Run `start-agent.sh --rebuild` and verify it completes without errors
2. Verify `claude-agent` container starts and `claude` CLI works
3. Verify `docker network ls` does NOT show `claude-agent-net`
4. Verify `docker ps` does NOT show `searxng` or `vane` containers
5. Run `start-agent.sh` without `--rebuild` and verify re-attach works
6. Verify webfetch still works from inside the container (egress via tinyproxy)

---

## Phase 3: Clean up Dockerfile and documentation

### Steps

1. **Remove SearXNG MCP shim from Dockerfile** (`dockerfiles/claude-agent.Dockerfile` lines 63-75):
   - Delete the `COPY searxng-mcp/server.py` line
   - Delete the `uv venv /opt/searxng-mcp/venv` and `uv pip install` lines
   - Delete the comment block explaining the shim

2. **Delete `dockerfiles/searxng-mcp/` directory**:
   - Remove `dockerfiles/searxng-mcp/server.py`

3. **Update CLAUDE.md**:
   - Add `research.sh` to the Layout section
   - Add a new "research.sh key decisions" section explaining the isolated VM architecture
   - Remove references to SearXNG/Vane from the "start-agent.sh key decisions" section
   - Update the `--disable-search` references

4. **Update ADR.md**:
   - Add ADR-018 documenting the extraction of Vane/SearXNG into `research.sh`
   - Mark ADR-014 and ADR-016 as superseded by ADR-018

5. **Update README.md**:
   - Add `research.sh` usage section
   - Update `start-agent.sh` usage to remove `--disable-search`

### Files

- `dockerfiles/claude-agent.Dockerfile` (modify)
- `dockerfiles/searxng-mcp/server.py` (delete)
- `CLAUDE.md` (modify)
- `ADR.md` (modify)
- `README.md` (modify)

### Testing

1. Run `start-agent.sh --rebuild` to rebuild the image without the MCP shim
2. Verify `/opt/searxng-mcp/` does not exist inside the container
3. Review documentation for accuracy and completeness

---

## Notes

- **Simultaneous operation**: `start-agent.sh` (profile `claude-agent`) and `research.sh` (profile `research`) use separate Colima VMs, so they can run concurrently without interference. They share host ports differently: `start-agent.sh` uses no host ports by default; `research.sh` binds port 3000.

- **Port conflict**: If another service uses port 3000 on the host, add a `--port=` flag to `research.sh` to allow override.

- **Data persistence**: `~/.research/vane-data/` persists Vane's LLM configuration across `--rebuild`. Consider documenting the one-time LLM setup step prominently.

- **Allowlist vs. LLM egress**: The local LLM (Ollama/omlx) is accessed via `host.docker.internal`, which is resolved via `--add-host` and goes through the `$HOST_IP:$INFERENCE_PORT` iptables RETURN rule, not through tinyproxy. This mirrors the existing `start-agent.sh` architecture.

- **Alternative considered**: Running Vane and SearXNG in a single container instead of two. Rejected because it would require a custom Dockerfile combining both images, increasing maintenance burden. The two-container approach reuses upstream images directly.

- **Migration path**: Users who relied on `--disable-search` to avoid Vane/SearXNG no longer need it; the default is now no search. Users who want research capabilities run `research.sh` separately.
