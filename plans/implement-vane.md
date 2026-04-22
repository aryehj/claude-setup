# Implement Vane in start-agent.sh

## Status

- [ ] Phase 1: Refactor flag semantics (`--enable-local-search` → default-on + `--disable-search`)
- [ ] Phase 2: Add Vane container lifecycle
- [ ] Phase 3: Update documentation

## Context

`start-agent.sh` currently supports SearXNG as an opt-in feature via `--enable-local-search`. The user wants:

1. SearXNG + Vane to run **by default** (no flag needed)
2. A new `--disable-search` flag to turn them both off
3. Vane exposed on port 3000 for human research workflows (separate from agent coding tasks)
4. Vane to use the existing SearXNG container as its search backend
5. Vane to use the same local LLM backend (Ollama/omlx) as OpenCode

Current state:
- SearXNG container logic: lines 746-765
- `--enable-local-search` flag parsing: line 102
- `LOCAL_SEARCH_ENABLED` boolean: lines 172-176
- `claude-agent-net` user-defined network: lines 528-545
- SearXNG settings.yml seed: lines 548-582

Vane (formerly Perplexica) is an AI-powered search frontend. The `:slim-latest` image expects an external SearXNG instance at `SEARXNG_API_URL`. Web UI runs on port 3000. LLM configuration happens via the web UI on first launch and is persisted in `/home/vane/data`.

## Goals

- `start-agent.sh` (no args) starts SearXNG + Vane alongside claude-agent
- `--disable-search` skips both SearXNG and Vane
- Vane web UI accessible at `http://localhost:3000` from the macOS host
- Vane uses SearXNG at `http://searxng:8080` (container DNS on claude-agent-net)
- Vane's data volume persists across container recreations
- Deprecate `--enable-local-search` (warn + continue, treated as a no-op since search is now default)

## Unknowns / To Verify

1. **Vane LLM pre-configuration**: The research indicates Ollama settings are configured via the web UI on first launch. Verify whether `OLLAMA_API_URL` or similar env var can be set on the container to pre-configure it. If not, the user will need to enter the Ollama URL manually on first access.
   - **How to verify**: Check Docker Hub image docs or source repo for env var support.
   - **Affects**: Phase 2 step 3.

2. **Port 3000 binding semantics**: Confirm `-p 3000:3000` exposes to host correctly through Colima's network layer.
   - **How to verify**: Test after implementation.
   - **Affects**: Phase 2 step 2.

---

## Phase 1: Refactor flag semantics

### Steps

1. **Add `--disable-search` flag parsing** (around line 102).
   - Add `CLI_DISABLE_SEARCH=""` to the arg vars block (line 30 area).
   - Add case `--disable-search) CLI_DISABLE_SEARCH="true" ;;` to the arg parser.
   - Add env var support: `CLAUDE_AGENT_DISABLE_SEARCH`.

2. **Deprecate `--enable-local-search`**.
   - Keep the case branch but emit a warning: `echo "warning: --enable-local-search is deprecated (search is now enabled by default). Use --disable-search to disable." >&2`
   - Do not set any variable (it's a no-op).

3. **Invert the boolean logic**.
   - Replace lines 172-176 with:
     ```bash
     if [[ "${CLI_DISABLE_SEARCH:-}" == "true" || "${CLAUDE_AGENT_DISABLE_SEARCH:-}" =~ ^(1|true|yes)$ ]]; then
       LOCAL_SEARCH_ENABLED=false
     else
       LOCAL_SEARCH_ENABLED=true
     fi
     ```

4. **Update usage text** (lines 36-84).
   - Replace `--enable-local-search` entry with `--disable-search` entry.
   - Update the ENVIRONMENT section to reference `CLAUDE_AGENT_DISABLE_SEARCH`.

### Files

- `start-agent.sh` (flag parsing, usage, boolean logic)

### Testing

```bash
# Default: search enabled
start-agent.sh --rebuild
# Expect: "Starting SearXNG container" and "Starting Vane container" in output

# Explicit disable
start-agent.sh --disable-search
# Expect: no SearXNG/Vane output, containers not created

# Deprecated flag (no-op with warning)
start-agent.sh --enable-local-search
# Expect: deprecation warning, search still runs (default)
```

---

## Phase 2: Add Vane container lifecycle

### Steps

1. **Add Vane constants** (after line 194).
   ```bash
   VANE_CONTAINER="vane"
   VANE_DATA_DIR="$HOME/.claude-agent/vane-data"
   VANE_PORT=3000
   ```

2. **Add Vane container startup block** (after SearXNG lifecycle, ~line 765).
   ```bash
   if $LOCAL_SEARCH_ENABLED; then
     echo "==> Starting Vane container"
     mkdir -p "$VANE_DATA_DIR"
     if ! docker container inspect "$VANE_CONTAINER" &>/dev/null; then
       docker run -d \
         --name "$VANE_CONTAINER" \
         --network "$AGENT_NET_NAME" \
         -p "$VANE_PORT:3000" \
         -e "SEARXNG_API_URL=http://searxng:8080" \
         -v "$VANE_DATA_DIR:/home/vane/data" \
         docker.io/itzcrazykns1337/vane:slim-latest >/dev/null
       echo "    vane: created (http://localhost:$VANE_PORT)"
     else
       docker start "$VANE_CONTAINER" >/dev/null 2>&1 || true
       echo "    vane: started (existing container)"
     fi
   else
     if docker container inspect "$VANE_CONTAINER" &>/dev/null; then
       echo "==> Removing Vane container (--disable-search set)"
       docker rm -f "$VANE_CONTAINER" >/dev/null
     fi
   fi
   ```

3. **Wire Ollama/omlx host URL** (if env var support exists).
   - If Vane supports `OLLAMA_API_URL` or similar, add to the `docker run`:
     ```bash
     -e "OLLAMA_API_URL=http://$HOST_IP:$INFERENCE_PORT"
     ```
   - If not, add a post-startup note to the user:
     ```bash
     echo "    note: configure LLM at http://localhost:$VANE_PORT on first access"
     ```

4. **Handle `--rebuild` cleanup** (after line 488).
   - Add alongside SearXNG cleanup:
     ```bash
     if $LOCAL_SEARCH_ENABLED && docker container inspect "$VANE_CONTAINER" &>/dev/null; then
       echo "==> --rebuild: removing container '$VANE_CONTAINER'"
       docker rm -f "$VANE_CONTAINER" >/dev/null
     fi
     ```

5. **Add iptables rule for Vane** (if needed).
   - The existing `AGENT_NET_CIDR` rule allowing port 8080 should cover SearXNG traffic. Vane's outbound traffic to `searxng:8080` uses the same path.
   - Verify: Vane → SearXNG is intra-`claude-agent-net`; no additional rule needed unless Vane needs to reach the host directly (it shouldn't for search).

6. **Output summary line** (update line 1123 area).
   - Add Vane to the startup summary:
     ```bash
     $LOCAL_SEARCH_ENABLED && echo "    vane    : http://localhost:$VANE_PORT (AI research UI)"
     ```

### Files

- `start-agent.sh` (constants, container lifecycle, rebuild cleanup, summary output)

### Testing

```bash
# Fresh start
start-agent.sh --rebuild
# Expect: both searxng and vane containers running
docker ps | grep -E 'searxng|vane'

# Web UI accessible
curl -sf http://localhost:3000 | head -c 200
# Expect: HTML response (Vane UI)

# Container uses external SearXNG
docker logs vane 2>&1 | grep -i searxng
# Expect: references to searxng:8080

# Disable stops both
start-agent.sh --disable-search
docker ps | grep -E 'searxng|vane'
# Expect: no output (containers removed)
```

---

## Phase 3: Update documentation

### Steps

1. **Update CLAUDE.md** (Key decisions section, around line 97).
   - Add a new section after SearXNG-backed websearch:
     ```
     **Vane runs alongside SearXNG by default.** Vane (formerly Perplexica) is an
     AI-powered research interface exposed on port 3000. It uses SearXNG as its
     search backend and the configured local LLM (Ollama/omlx) for AI features.
     `--disable-search` turns off both SearXNG and Vane. Data persists in
     `~/.claude-agent/vane-data/`.
     ```
   - Update the `--enable-local-search` reference to `--disable-search`.

2. **Update README.md usage section** (if present).
   - Document `--disable-search` flag.
   - Add Vane access URL (`http://localhost:3000`).

3. **Update the usage heredoc in start-agent.sh** (already covered in Phase 1 step 4).

### Files

- `CLAUDE.md`
- `README.md` (if usage documented there)
- `start-agent.sh` (usage text)

### Testing

- Review docs manually for accuracy.

---

## Notes

- **Vane data persistence**: The `~/.claude-agent/vane-data/` directory persists settings across container recreations. Users configure the LLM endpoint once via the web UI; it survives `--rebuild` (only the container is deleted, not the data dir).

- **Port conflict risk**: Port 3000 is common for dev servers. If users have conflicts, they can stop Vane manually (`docker stop vane`) or request a `--vane-port` flag in the future.

- **Image pull timing**: First run will pull `itzcrazykns1337/vane:slim-latest` from Docker Hub. This may take a minute on slow connections. The image is cached in the Colima VM's docker runtime.

- **SearXNG JSON format**: Already enabled in the seeded `settings.yml` (`formats: [html, json]`). No changes needed for Vane compatibility.

- **Ollama network path**: Vane → Ollama uses the same `$HOST_IP:$INFERENCE_PORT` route as OpenCode. The existing iptables RETURN rule for inference traffic covers this path.
