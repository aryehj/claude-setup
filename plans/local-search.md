# Plan: Enable OpenCode web access in start-agent.sh

## Context

`start-agent.sh` writes an `opencode.json` for the container (lines 652–760) but doesn't configure OpenCode's built-in web tools. Per OpenCode's docs (https://opencode.ai/docs/tools/):

- `webfetch` and `websearch` are both governed by a top-level `permission` block (`"allow" | "ask" | "deny"`).
- `webfetch` is a local tool: opencode makes HTTPS calls directly from the container to whatever URL it's given. Those calls already flow through tinyproxy and are constrained by the existing allowlist.
- `websearch` is an MCP client that calls Exa AI's hosted service at `https://mcp.exa.ai/mcp`. Queries leave the machine and are handled by a third party. It also requires `OPENCODE_ENABLE_EXA=1` when we aren't using the OpenCode inference provider.

The user wants web access for the agent without routing queries through a third-party gateway. Goal: enable `webfetch` now (phase 1), and — when we need ranked query-style search — stand up a local MCP search server that can also back a Perplexica instance on the same host (phase 2).

---

## Phase 1 — `webfetch` only, no Exa

### 1a. `start-agent.sh` — opencode.json injection (~line 751)

In the Python heredoc that writes `opencode.json`, just before the `compaction` line, add:

```python
perms = data.setdefault('permission', {})
perms.setdefault('webfetch', 'allow')
perms.setdefault('websearch', 'deny')
```

`setdefault` preserves any explicit user choice in an existing config. `websearch: "deny"` is set explicitly (not left unset) so that the tool is a hard no until phase 2 wires up a replacement — prevents opencode from quietly calling Exa if a future default changes.

### 1b. `DOCKER_ENV_ARGS` (line 793)

**Do not** add `OPENCODE_ENABLE_EXA`. Without it, the Exa MCP client isn't registered at all.

### 1c. Allowlist

No change. `mcp.exa.ai` stays *off* the allowlist. DuckDuckGo/Google/Bing remain allow-listed, so the agent can use `webfetch` on `https://duckduckgo.com/html/?q=…` as a no-gateway workaround when it needs discovery-style search.

### 1d. CLAUDE.md

Add a short "key decisions" bullet under `start-agent.sh`:

> **Web tools: webfetch allowed, websearch denied.** `opencode.json` is generated with `permission.webfetch: "allow"` and `permission.websearch: "deny"`. webfetch egress is constrained by the existing tinyproxy allowlist — an injected page can't redirect fetches to hosts that aren't already trusted. websearch is off because its default backend (Exa MCP) would route queries through a third-party gateway; a self-hosted replacement is tracked as a phase-2 item.

### Phase 1 verification

1. `mv ~/.claude-agent/opencode-config/opencode.json{,.bak}` then `start-agent.sh --rebuild`.
2. Inside container: `cat ~/.config/opencode/opencode.json | jq .permission` shows both keys.
3. From an opencode session: ask it to fetch `https://arxiv.org` — success. Ask it to fetch `https://example.com` — fails at the proxy (unallowlisted).
4. Ask opencode to "search DuckDuckGo for X"; confirm it uses `webfetch` against `duckduckgo.com/html/?q=…` rather than calling a websearch tool.
5. Re-run `start-agent.sh` without `--rebuild` after hand-editing `opencode.json` to set `permission.webfetch: "ask"`. Confirm the edit survives (setdefault semantics).

---

## Phase 2 — local MCP search server (SearXNG-backed, also usable by Perplexica)

### Why SearXNG

Perplexica is built on top of SearXNG — that's its documented search backend. If we stand up one SearXNG instance, we can:

- Point an MCP wrapper at SearXNG's JSON API (`/search?format=json`) to give opencode a `websearch` tool without any third-party gateway.
- Point a separate Perplexica deployment at the same SearXNG instance.
- Keep all query traffic on our own infra; SearXNG fans out to upstream engines (Google/Bing/DDG/Brave/etc.) from the host, so upstreams see SearXNG's requests but never a user-identifying session.

This is the only shortlisted option that cleanly satisfies "local gateway + Perplexica-compatible". Direct-DDG-scraping MCP servers don't meet the Perplexica reuse requirement; Brave-API MCP servers are still third-party gateways.

### 2a. Run SearXNG in the Colima VM

SearXNG ships an official docker image (`docker.io/searxng/searxng`). Add a second docker container alongside `claude-agent` in the same Colima VM:

- Container name: `searxng`.
- Bind: `127.0.0.1:8080` inside the VM, reachable from the `claude-agent` container via the bridge gateway IP (same path the container already uses to reach tinyproxy).
- Config bind-mount: `~/.claude-agent/searxng/settings.yml` on the host → `/etc/searxng/settings.yml` in the container. Seed this file on first run with JSON output enabled (`search.formats: [html, json]`) and a random `server.secret_key`.
- Lifecycle: managed by `start-agent.sh`, same pattern as `claude-agent` — start on launch, `--rebuild` tears it down, idempotent re-entry.
- Firewall: add a `CLAUDE_AGENT` chain RETURN rule allowing `claude-agent` → SearXNG's bridge IP:8080. SearXNG itself needs outbound to upstream engines; it runs in the VM's default netns (not behind our container-bridge rules), so it inherits normal VM egress. If we want to constrain SearXNG's upstreams too, that's a follow-up — out of scope for this phase.

### 2b. MCP wrapper

Two realistic shapes:

- **Community server**: check registry.modelcontextprotocol.io and awesome-mcp-servers for a maintained "searxng-mcp" package at implementation time. If one exists with active commits, prefer it.
- **Thin custom server**: ~60 lines. A Node or Python MCP stdio server exposing one tool (`websearch` with `query` and optional `categories`/`time_range` args) that POSTs to `http://searxng:8080/search?format=json` and returns the JSON results. Runs inside the `claude-agent` container as a subprocess opencode spawns — no extra container, no network exposure, no auth layer needed.

Decision for the plan: pick the community server if one is healthy at implementation time; otherwise write the custom shim. Either way, the interface opencode sees is identical.

### 2c. Wire into opencode.json

In the Python heredoc, under a new branch that only fires when a `searxng` container is detected running, add an `mcp` block and flip websearch back on. Sketch:

```python
if searxng_available:  # set by the shell before invoking python
    mcps = data.setdefault('mcp', {})
    mcps.setdefault('searxng', {
        'type': 'local',
        'command': ['node', '/opt/searxng-mcp/index.js'],  # or python -m ...
        'environment': {'SEARXNG_URL': 'http://<bridge-ip>:8080'},
    })
    perms['websearch'] = 'allow'
```

`searxng_available` is passed in as an extra CLI arg from the shell wrapper (same pattern as `backend`, `runtime_url`). The MCP binary itself is installed into the image via `dockerfiles/claude-agent.Dockerfile`.

### 2d. Dockerfile

`dockerfiles/claude-agent.Dockerfile`: add a RUN step that installs the MCP wrapper (either `npm install -g <community-pkg>` or `COPY` of the custom shim). This is a trivial extra layer.

### 2e. Perplexica reuse

Perplexica is out of scope for this plan as a deployed service, but the SearXNG instance from 2a is the pre-requisite. When someone later wants Perplexica, they deploy it as another container pointing its `config.toml`'s `SEARXNG` field at the same `http://<bridge-ip>:8080`. No changes to the opencode stack are needed to accommodate that.

### 2f. Allowlist

SearXNG itself is not fetched from the container — the MCP wrapper talks to it over the local bridge, not via tinyproxy. No allowlist change required. SearXNG's own outbound traffic to upstream engines happens in the VM's default netns and doesn't touch the `CLAUDE_AGENT` chain.

### 2g. CLAUDE.md / ADR

Phase 2 warrants a new ADR (candidate: ADR-014) documenting:
- Why a local search gateway (query privacy, no third-party dependency).
- Why SearXNG specifically (Perplexica compatibility, JSON API, mature project).
- The MCP-wrapper-as-subprocess choice (vs. standing up yet another network service).

### Phase 2 verification

1. `start-agent.sh --rebuild`; confirm `searxng` container is running: `docker ps --filter name=searxng`.
2. From the host: `curl 'http://<colima-vm-ip>:8080/search?q=test&format=json' | jq '.results | length'` returns > 0.
3. From `claude-agent` container: `curl 'http://<bridge-ip>:8080/search?q=test&format=json' | jq '.results | length'` returns > 0 (confirms firewall carve-out).
4. Inside opencode: ask it to run a websearch. Confirm it uses the `searxng` MCP tool (visible in opencode's tool-call log), not Exa. Exa must remain un-registered.
5. Confirm `mcp.exa.ai` is still NOT in the allowlist and that `OPENCODE_ENABLE_EXA` is still unset — search works without either.
6. Stop the SearXNG container manually, re-run `start-agent.sh` (no `--rebuild`). Confirm `opencode.json` falls back to `websearch: "deny"` (or the `mcp.searxng` block is omitted) rather than silently breaking.

---

## Critical files

- `/Users/aryehj/Repos/start-claude/start-agent.sh` — phase 1: lines 707–755 (Python config writer), 793 (DOCKER_ENV_ARGS). Phase 2: new SearXNG lifecycle block (paralleling the existing `claude-agent` container lifecycle), new firewall rule in the `CLAUDE_AGENT` chain setup, extra args to the Python heredoc.
- `/Users/aryehj/Repos/start-claude/dockerfiles/claude-agent.Dockerfile` — phase 2: install MCP wrapper.
- `/Users/aryehj/Repos/start-claude/CLAUDE.md` — phase 1 bullet, phase 2 bullet.
- `/Users/aryehj/Repos/start-claude/ADR.md` — phase 2: new ADR for the local-search-gateway decision.
- `~/.claude-agent/searxng/settings.yml` (host-side, seeded on first run) — phase 2 only.
