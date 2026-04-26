# Vane research-pipeline debug session — 2026-04-26

Picking back up after the user identified that **two Vane containers were
running on host port 3000** (one from `start-agent.sh`, one from `research.py`)
and only one wins the host port forward — meaning the browser at
`localhost:3000` was talking to whichever container grabbed the port,
not necessarily the one we thought we were configuring.

Most of the prior debugging may be suspect because of that identity confusion.

## The triggering observation

User added a custom system prompt to Vane asking the model to list the URLs
it scraped vs. failed to scrape. Got back: *"Note: As an AI, I synthesized
the provided search results directly. No external URLs were successfully
scraped outside of the provided context snippets. Failed to Scrape: None."*

Concern: research tool isn't actually scraping.

## The actual root cause (just identified)

`start-agent.sh` and `research.py` each create their own Vane container,
each binding host port 3000 (`-p 3000:3000`). Only one can hold that
host port at a time; the browser at `localhost:3000` reaches whichever
container won the binding. UI changes the user thought were affecting
`research-vane` may have been hitting the `start-agent.sh` Vane (which has
a separate SQLite config DB at `~/.claude-agent/vane-data/`, separate
SearXNG sibling named `searxng` not `research-searxng`, and a separate
docker network it's attached to).

This explains:
- The Vane UI's "URL field" reading `http://searxng:8080` (that's
  start-agent.sh's container name, not research.py's).
- "Fixing" it to `http://research-searxng:8080` causing hangs in the
  start-agent.sh Vane — because that hostname doesn't resolve on
  `claude-agent-net`.
- The diagnostics we ran inside the `research` Colima profile showing
  everything healthy — because the research VM's components were fine;
  we just weren't reaching them from the browser.

## Action user wants to take next session

Factor out the Vane instance in `start-agent.sh` (or otherwise prevent
the port collision). After that, verify which findings below still hold.

Options for the dedup:
1. Remove Vane from `start-agent.sh` entirely (rationale: research.py
   is the dedicated AI-research environment; one-tool-per-purpose).
2. Use different host ports per script (e.g., research.py keeps 3000,
   start-agent.sh moves to 3001).
3. Make Vane optional in both, gated by a flag, with the user choosing
   which one to run at any given time.

Option 1 is cleanest given the user's recent threat-model concerns
about Vane being on the same docker bridge as `claude-agent`
(see also the security framing earlier in this session — Vane as a
prompt-injection vector if a coding agent can reach it).

## In-flight code changes (committable on their own merits)

These changes are coherent regardless of how the dedup is resolved.
They affect only `research.py`'s Vane, not `start-agent.sh`'s.

1. **`research.py` — `ensure_vane_container`**: drop `HTTP_PROXY`, keep
   `HTTPS_PROXY` + `NO_PROXY`. (Rationale below in "What we learned that
   probably still holds".)
2. **`ADR.md` — ADR-027** rewritten with the journey: original "no proxy"
   bug → first attempt set both `HTTP_PROXY`+`HTTPS_PROXY` → that broke
   Vane's HTTP client → resolution is HTTPS-only.
3. **`CLAUDE.md`** — matching paragraph synced.
4. **`tests/probe-vane-egress.sh`** — checks `HTTPS_PROXY` present,
   `HTTP_PROXY` *absent*, `NO_PROXY` covers research-searxng + host-gateway,
   sidecar HTTPS round-trip via Squid succeeds.

Picking up the change requires:
```
docker rm -f research-vane && ./research.py
tests/probe-vane-egress.sh
```

## What we learned that probably still holds

These observations were on the *research* VM directly, not via the
browser/Vane UI, so they should be valid regardless of the dedup.

- Squid is healthy: bound to `172.17.0.1:8888`, returns `200` in ~250ms
  for a sidecar curl. `dst-domain` ACL works for denylist matching.
- SearXNG is healthy: returns full results in <1s for direct queries
  (e.g. `?q=Chicago+population+trends&format=json` → 200, 11.9KB).
  `outgoing.proxies` is correctly pointed at Squid; SearXNG → Squid →
  upstream works.
- omlx is healthy: embedding model `nomicai-modernbert-embed-base-bf16`
  returns vectors in 0.67s, chat model `gemma-4-26b-a4b-it-8bit`
  returns completions in 0.87s. Both reachable from research-net at
  `http://192.168.5.2:8000` with `Bearer $OMLX_API_KEY`.
- L3 firewall (`RESEARCH` iptables chain) works for cross-bridge egress:
  a sidecar curl from research-net to `https://example.com` *without*
  proxy returns `000` (REJECTed), confirming the chain is in force.
- `br_netfilter` is **not** loaded in the research VM. Same-bridge
  container-to-container traffic skips iptables entirely. The chain's
  same-bridge "allow" rules are decorative; everything works because
  the same-bridge traffic was never going to be filtered. Not a security
  hole (rules are allows, not denies) but worth knowing for future
  iptables changes.

## What we learned about the original `HTTP_PROXY`+`HTTPS_PROXY` regression

When both proxy env vars were set on `research-vane`:
- Vane's UI hung at "searching N queries" indefinitely.
- HAR showed `/api/chat` opened an SSE stream, emitted 2 events
  (research block + sub-steps with 3 decomposed queries), then nothing.
- Squid `cache.log` showed no DNS failures.
- `ss` showed zero TCP connections from Vane to Squid:8888.
- Sidecar curl with the *exact same* env vars worked fine.

→ Vane's HTTP client mishandles `HTTP_PROXY` for `http://research-searxng:8080`
in a way that swallows the `fetch()` silently, despite `NO_PROXY` being
set correctly. Not investigated to root cause (would require digging into
Vane / Perplexica / langchain HTTP-client setup). Workaround: drop
`HTTP_PROXY`. Cost: HTTP-only scrape targets aren't reachable. Acceptable
given the modern web is overwhelmingly HTTPS.

**Caveat in light of the dedup discovery**: this regression may have
actually been observed in the *start-agent.sh* Vane (which doesn't have
the proxy env vars at all, and whose SearXNG sibling is `searxng`). The
hang on "searching" might have been start-agent.sh's Vane unable to
resolve `research-searxng:8080` after the user changed the UI URL field.
Re-verify after the dedup whether `research.py`'s Vane (which we never
actually confirmed the browser was reaching) hangs the same way with
`HTTP_PROXY` set. If it doesn't, ADR-027's rationale weakens — but the
HTTPS-only configuration is still defensible as "minimum surface area".

## What we learned about Vane's optimization mode

From the HAR request body: `"optimizationMode":"speed"`.

Perplexica/Vane modes:
- **speed**: snippet-only synthesis, **does not scrape pages**.
- **balanced**: light scraping of top results.
- **quality**: full scrape + embedding rerank + scraped-content synthesis.

In `speed` mode, the original "no URLs scraped" message is *correct
behavior*, not a bug. The custom system prompt asking the model to
enumerate scraped URLs encourages confabulation in any mode but
especially in `speed` mode where there are zero scrapes to enumerate.

Action items (after dedup):
- Decide which Vane mode the user actually wants. Likely `quality` or
  `balanced` to actually exercise the scraping pipeline + denylist.
- Remove or rework the custom system prompt — the LLM has no
  introspection into Vane's pipeline; asking it to list scrapes will
  always produce confabulation.

## What's still slow/broken regardless of dedup

`research.py`'s SearXNG had Squid log entries showing CONNECTs to upstream
search engines that took ~240s each (Brave, Wikipedia, Google). Direct
sidecar tests against the same engines were <1s. So either:
- SearXNG → Squid → upstream is genuinely slow under parallel load
  (Squid single-worker bottleneck), OR
- The 240s is tunnel-keepalive duration, not request time.

We didn't conclusively distinguish. Probable fix shape if it's real
slowness: add `outgoing.request_timeout: 5.0` and
`outgoing.max_request_timeout: 8.0` to `render_searxng_settings`,
plus drop chronically-slow engines (`brave` returns "too many requests"
constantly) from `keep_only`.

Independent of dedup, but worth re-testing after dedup to know whether
it's actually causing user-visible problems.

## Suggested first steps next session

1. Decide on dedup strategy (remove Vane from start-agent.sh is my
   recommendation given the threat-model thread earlier).
2. After dedup, verify the browser at `localhost:3000` hits
   `research-vane` specifically: `docker inspect research-vane --format
   '{{.NetworkSettings.Ports}}'` should show port 3000 mapped, and
   `curl -s http://localhost:3000/api/providers` should return a
   provider list whose IDs match what's configured in
   `~/.research/vane-data/`.
3. Re-test a Vane query in `quality` mode without the custom system
   prompt. If it works (slow but works), our HTTPS-only fix is doing
   its job. If it still hangs, dig into Vane logs with verbose mode
   enabled.
4. Decide whether to commit the in-flight changes as-is, or rework
   ADR-027 once the dedup clears up which Vane the regression actually
   happened in.
