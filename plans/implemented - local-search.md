# Plan: local MCP search server for start-agent.sh

## Context

Phase 1 (webfetch allowed, websearch denied in `opencode.json`) is already shipped — see `start-agent.sh:790-792` and the "Web tools" bullet in `CLAUDE.md`. What remains is a no-third-party websearch backend: stand up a local MCP search server that can also back a Perplexica instance on the same host.

---

## Local MCP search server (SearXNG-backed, also usable by Perplexica)

### Why SearXNG

Perplexica is built on top of SearXNG — that's its documented search backend. If we stand up one SearXNG instance, we can:

- Point an MCP wrapper at SearXNG's JSON API (`/search?format=json`) to give opencode a `websearch` tool without any third-party gateway.
- Point a separate Perplexica deployment at the same SearXNG instance.
- Keep all query traffic on our own infra. SearXNG's fan-out to upstream engines (Google/Bing/DDG/Brave/etc.) goes through the same tinyproxy the rest of the stack uses (see 2a for why env-var proxying doesn't work and `outgoing.proxies` in `settings.yml` is required instead), so upstreams see SearXNG's requests but never a user-identifying session, and the egress allowlist governs which engines are reachable.

This is the only shortlisted option that cleanly satisfies "local gateway + Perplexica-compatible". Direct-DDG-scraping MCP servers don't meet the Perplexica reuse requirement; Brave-API MCP servers are still third-party gateways.

### 2a. Run SearXNG in the Colima VM — on the docker bridge, egress through tinyproxy

SearXNG ships an official docker image (`docker.io/searxng/searxng`). Add a second docker container alongside `claude-agent` on the same docker bridge inside the Colima VM:

- **Container name:** `searxng`. Bridge-attached (default docker network). Exposes `8080` inside the bridge only — no host port publish. `claude-agent` reaches it by container name (`http://searxng:8080`).
- **Config bind-mount (read-only):** `~/.claude-agent/searxng/settings.yml` on the host → `/etc/searxng/settings.yml:ro` in the container. SearXNG reads this at startup and never writes to it, so `:ro` is a free defense-in-depth measure — a compromised plugin or upstream-response-driven write path fails loudly instead of silently mutating config. Seeded on first run with: `search.formats: [html, json]`, a random `server.secret_key` (`openssl rand -hex 32`, persisted so sessions survive), and the proxy field described below. The secret is generated once and never rotated by `start-agent.sh` after that. If the official `searxng/searxng` image turns out to want runtime template expansion on its config (a known historical quirk), resolve it by pre-expanding the values into the host file rather than dropping `:ro`.
- **Host-persisted state survives `--rebuild`.** `~/.claude-agent/searxng/` is treated the same way `~/.claude-containers/shared/` is for `start-claude.sh`: the `--rebuild` flow tears down the image and container but does NOT touch this directory. The `secret_key` is stable across rebuilds; regenerating it is an explicit manual `rm -rf ~/.claude-agent/searxng/`.
- **Upstream fan-out via tinyproxy — the load-bearing security control.** Primary-source finding from SearXNG's `searx/network/client.py`: SearXNG builds its httpx client with an explicit `transport=` argument, which per httpx semantics disables `HTTPS_PROXY` env-var pickup entirely. Env vars would *silently* do nothing. The working knob is `outgoing.proxies` in `settings.yml`:
  ```yaml
  outgoing:
    proxies:
      all://: http://<tinyproxy-host>:8888
  ```
  `<tinyproxy-host>` is the bridge gateway IP (same address `claude-agent` already uses). With this set, every engine fetch becomes a CONNECT through tinyproxy, and tinyproxy's hostname allowlist governs which search engines SearXNG can reach — i.e., SearXNG inherits the same egress firewall as the agent container, rather than bypassing it.
- **Narrow engine set, explicit-disable the rest.** Upstream SearXNG defaults enable ~50+ engines spanning torrent trackers, social media, art sites, and general search. Most are off-topic for a dev/research agent and each adds an allowlist entry. Our seeded `settings.yml` instead enables a small curated set and explicitly disables everything else. Starting list: Google, Bing, DuckDuckGo, Brave, Qwant, Wikipedia, arXiv, GitHub (code), Stack Exchange. Everything else gets `disabled: true`. Users who want more engines edit `settings.yml` AND add the corresponding hostname to the allowlist — two-step on purpose, so "turn on a new engine" can't happen via a single-file change in either direction.
- **Allowlist additions** (matches the curated engine set):
  - `(.*\.)?google\.com` — regex entry, because SearXNG's google engine dynamically discovers ccTLDs via `/supported_domains`. Alternative: pin `language: en` and `country: US` in the engine config to constrain it to `www.google.com`.
  - `www.bing.com`
  - `duckduckgo.com`, `html.duckduckgo.com`, `lite.duckduckgo.com`
  - `search.brave.com`
  - `api.qwant.com`
  - `en.wikipedia.org` (or `(.*\.)?wikipedia\.org` if multi-language enabled)
  - `export.arxiv.org`
  - `api.github.com`
  - `api.stackexchange.com`

  Several of these (`api.github.com`, `en.wikipedia.org`, `export.arxiv.org`) are likely already on the permissive dev allowlist from existing `webfetch` usage — no change needed there; the search-engine-specific hosts are the net-new additions.
- **Firewall — two RETURN rules in the `CLAUDE_AGENT` chain:**
  1. `claude-agent` bridge IP → `searxng` bridge IP:8080 (MCP shim → SearXNG JSON API).
  2. `searxng` bridge IP → bridge gateway IP:8888 (SearXNG → tinyproxy for fan-out).
  Rule (2) is what preserves the allowlist as the single source of egress truth; without it, SearXNG on the bridge is default-deny at the CLAUDE_AGENT chain and search fails entirely. Both rules are re-applied idempotently on every `start-agent.sh` run via the existing `-F CLAUDE_AGENT && -A ...` wipe-and-replay pattern.
- **Lifecycle:** managed by `start-agent.sh` alongside `claude-agent`. `--rebuild` tears both down. Gated behind a `--enable-local-search` flag (env: `CLAUDE_AGENT_ENABLE_LOCAL_SEARCH=1`) — the MCP wiring in 2c keys off the same flag, not runtime container detection, so both sides of the decision travel together.
- **Parallelism sanity:** SearXNG defaults (`pool_connections: 100`) and tinyproxy's default `MaxClients: 100` are both fine for single-user fan-out (~10–20 parallel CONNECTs per query). Confirm tinyproxy's active `MaxClients` in the generated config — raise to 200 only if multi-user use is anticipated.

### 2b. MCP wrapper — custom Python FastMCP shim

**Decision: ship a ~40-line Python FastMCP shim. Do NOT use the community npm package.**

Survey result: the community default is `ihor-sokoliuk/mcp-searxng` (npm, in the official `modelcontextprotocol/servers` list, actively maintained, uses `SEARXNG_URL` exactly as our spec wants). Rejected because:

- It exposes a **second tool, `web_url_read`**, that html-to-markdowns arbitrary URLs. That duplicates opencode's native `webfetch`. Two tools for the same capability means the model picks ambiguously, and the MCP-provided one sits outside opencode's `permission.webfetch` control — a silent permissions bypass. opencode does not appear to support per-tool disable for a given MCP server, so disabling just `web_url_read` while keeping `searxng_web_search` is not a clean option.
- Bundles `express` + `cors` even when running in stdio mode — extra audit surface for no stdio-mode benefit.
- Missing `categories` param.

Neither `SecretiveShell/mcp-searxng` (stale since Jan 2025) nor `Sacode/searxng-simple-mcp` (prefixed env var, low adoption) is a compelling alternative.

**Custom shim shape.** Install location: `/opt/searxng-mcp/server.py` (owned by root, `COPY`d in by the Dockerfile). Deps added to the image: `mcp[cli]` and `httpx`. Interface:

```python
from mcp.server.fastmcp import FastMCP
import httpx, os

mcp = FastMCP("searxng")
URL = os.environ["SEARXNG_URL"]

@mcp.tool()
async def websearch(query: str, categories: str = "general",
                    time_range: str | None = None, language: str = "en") -> list[dict]:
    """Search the web via a local SearXNG instance. Returns top results as {title, url, content}."""
    params = {"q": query, "format": "json", "categories": categories, "language": language}
    if time_range: params["time_range"] = time_range
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{URL}/search", params=params)
        r.raise_for_status()
        return [{"title": x["title"], "url": x["url"], "content": x.get("content", "")}
                for x in r.json().get("results", [])[:10]]

if __name__ == "__main__":
    mcp.run()
```

The shim runs as a subprocess opencode spawns via the `mcp.searxng.command` entry in `opencode.json` (see 2c) — no separate container, no network exposure, no auth layer. Errors (non-200 from SearXNG, timeout, connection refused) bubble as MCP tool errors, which is what opencode expects. The shim has no state and is replaceable at will; if a better community server emerges later, swap the `command` in `opencode.json`.

**Why not Node.** Python wins here only because FastMCP produces meaningfully shorter code (~40 lines vs. ~60–90 for the Node SDK with tsconfig overhead). The `claude-agent` image already has Python via `uv`. No ongoing Node/TS build surface.

### 2c. Wire into opencode.json

In the Python heredoc, gated on the same `--enable-local-search` flag that controls the container lifecycle (passed in as an extra CLI arg from the shell wrapper, same pattern as `backend`, `runtime_url`), add an `mcp` block and flip websearch back on. Sketch:

```python
if local_search_enabled:
    mcps = data.setdefault('mcp', {})
    mcps.setdefault('searxng', {
        'type': 'local',
        'command': ['python3', '/opt/searxng-mcp/server.py'],
        'environment': {'SEARXNG_URL': 'http://searxng:8080'},
    })
    perms['websearch'] = 'allow'
```

Reachability uses the container name (`searxng`) rather than a bridge IP — docker's embedded DNS resolves siblings on the default bridge by name, and the name is stable across restarts. The MCP binary itself is installed into the image via `dockerfiles/claude-agent.Dockerfile`. Flag-gated (not container-detection) keeps config and lifecycle in lockstep: one knob drives firewall, container, and JSON block together.

### 2d. Dockerfile

`dockerfiles/claude-agent.Dockerfile`: add one COPY for the shim and one install layer for its deps.

- `COPY dockerfiles/searxng-mcp/server.py /opt/searxng-mcp/server.py` (new source file checked into the repo at `dockerfiles/searxng-mcp/server.py`).
- Install `mcp[cli]` and `httpx` into the image. `uv` is already present, so: `RUN uv pip install --system mcp[cli] httpx`. `--system` avoids creating a per-project venv inside the image.
- No npm/node install for this shim. Keeps the Node build surface limited to what opencode itself already needs.

### 2e. Perplexica reuse

Perplexica is out of scope for this plan as a deployed service, but the SearXNG instance from 2a is the pre-requisite. When someone later wants Perplexica, they deploy it as another container on the same docker bridge and point its `config.toml`'s `SEARXNG` field at `http://searxng:8080` (docker embedded DNS). They'll also need to add a CLAUDE_AGENT RETURN rule for perplexica→searxng:8080 (same shape as the claude-agent→searxng rule in 2a). No changes to the opencode stack are needed to accommodate that.

### 2f. Allowlist

Two kinds of traffic, two different rules:

- **`claude-agent` → `searxng:8080`**: container-to-container on the bridge; does NOT go through tinyproxy. Governed by the firewall RETURN rule in 2a, not by the tinyproxy allowlist. The MCP shim's env needs `NO_PROXY=searxng,<searxng-bridge-ip>` so Node's `NODE_USE_ENV_PROXY=1` doesn't try to route this through tinyproxy (which would reject it — tinyproxy filters by hostname and `searxng` isn't on the allowlist, nor should it be).
- **`searxng` → upstream engines**: goes through tinyproxy via `outgoing.proxies` in `settings.yml` (see 2a). This IS governed by the tinyproxy allowlist, and engine hostnames are added to it as part of this plan. This is the whole point of running SearXNG on the bridge instead of host netns.

### 2g. CLAUDE.md / ADR

Add a new ADR (candidate: ADR-014) documenting the load-bearing decisions — these are the ones a future reader has to understand before touching any of this code, and they're the ones most likely to be undone by well-meaning "simplification":

- **Why a local search gateway at all** (query privacy, no third-party dependency, keeps the allowlist as single source of egress truth).
- **Why SearXNG specifically** (Perplexica compatibility, JSON API, mature project).
- **Why SearXNG on the docker bridge, not host netns.** Host netns would bypass the CLAUDE_AGENT firewall for SearXNG's fan-out — breaks the allowlist-as-single-control property. Bridge + tinyproxy keeps it intact.
- **Why `outgoing.proxies` in settings.yml and NOT `HTTPS_PROXY` env vars.** Primary-source finding in SearXNG's `searx/network/client.py`: the httpx client is built with an explicit `transport=`, which disables env-var proxy pickup. A future contributor who sees the container inherit HTTPS_PROXY from the claude-agent env and assumes "proxy's already wired" would be wrong — the ADR names the failure mode so that mistake doesn't get made.
- **Why a custom ~40-line Python shim over `ihor-sokoliuk/mcp-searxng`.** The npm package bundles a `web_url_read` tool that collides with opencode's native `webfetch` and sits outside its permission model. Rejecting a more popular option on security-posture grounds is the kind of decision that gets silently reversed during a "let's use the standard package" refactor — document it.
- **Why a narrow curated engine set + matching allowlist (two-lock pair).** Enabling a new engine requires changing both `settings.yml` AND `allowlist.txt` on purpose. Deliberate friction, not oversight.

Also a short "SearXNG-backed websearch" bullet under `start-agent.sh` in `CLAUDE.md` pointing at ADR-014 for the full reasoning.

### Phase 2 verification

1. `start-agent.sh --enable-local-search --rebuild`; `docker ps --filter name=searxng` shows it running.
2. **SearXNG reachable from `claude-agent`:** inside the container, `curl -s 'http://searxng:8080/search?q=test&format=json' | jq '.results | length'` returns > 0.
3. **Firewall egress proof (the one that matters):** temporarily edit `~/.claude-agent/allowlist.txt` to remove `www.google.com`, `--reload-allowlist`, then run a SearXNG query that only hits Google. Google results are empty / timed out in the JSON response; tinyproxy logs show the CONNECT rejected. Restore the allowlist, re-reload, query succeeds. This is the test that the allowlist actually governs SearXNG fan-out.
4. **Env-var-only proxy does nothing (negative test):** `docker exec searxng env | grep -i proxy` — confirm `HTTPS_PROXY` is NOT set, or if it is (from inheritance), unset it and `docker restart searxng`. Search still works because `settings.yml` is what's actually wired. If search breaks when env vars are unset, `settings.yml` wasn't applied — fix before shipping.
5. **MCP shim uses SearXNG, not Exa.** In an opencode session, run a websearch. Opencode's tool-call log shows the `searxng` MCP tool. `mcp.exa.ai` is not in the allowlist; `OPENCODE_ENABLE_EXA` is not in the container env.
6. **Engine-bypass audit:** `docker exec searxng grep -rn -E '^import (requests|aiohttp)|httpx\.(get|post|AsyncClient\()' /usr/local/searxng/searx/engines/` returns no results for the engines being used. Any hit is an engine that bypasses `outgoing.proxies` and must be either patched, disabled, or explicitly blessed.
7. **Disable path:** run `start-agent.sh` without `--enable-local-search`. `opencode.json` has `permission.websearch: "deny"` and no `mcp.searxng` block. `searxng` container is absent.

---

## Critical files

- `/Users/aryehj/Repos/start-claude/start-agent.sh` — new `searxng` container lifecycle block (create/start/rm, paralleling the existing `claude-agent` block), `--enable-local-search` flag parsing + `CLAUDE_AGENT_ENABLE_LOCAL_SEARCH` env plumbing, two new RETURN rules in the CLAUDE_AGENT chain setup (claude-agent→searxng:8080, searxng→gateway:8888), first-run seeding of `~/.claude-agent/searxng/settings.yml` (secret_key generation, engine curation, `outgoing.proxies.all://`), `NO_PROXY=searxng,<searxng-bridge-ip>` addition to DOCKER_ENV_ARGS for claude-agent, extra arg + branch in the Python heredoc that writes `opencode.json`.
- `/Users/aryehj/Repos/start-claude/dockerfiles/claude-agent.Dockerfile` — `COPY` of the MCP shim to `/opt/searxng-mcp/server.py`, `RUN uv pip install --system mcp[cli] httpx`.
- `/Users/aryehj/Repos/start-claude/dockerfiles/searxng-mcp/server.py` — new, the custom FastMCP shim from 2b.
- `/Users/aryehj/Repos/start-claude/CLAUDE.md` — new "SearXNG-backed websearch" key-decision bullet under `start-agent.sh`, pointing at ADR-014.
- `/Users/aryehj/Repos/start-claude/ADR.md` — ADR-014 covering the decisions listed in 2g.
- `~/.claude-agent/allowlist.txt` — add the curated engine hostnames from 2a (most search-engine-specific hosts are net-new; dev-research hosts likely already present).
- `~/.claude-agent/searxng/settings.yml` — host-side state, seeded on first run, bind-mounted `:ro`, survives `--rebuild`.
