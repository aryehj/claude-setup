# Walk back the Vane-debug-saga compromises

## Status

- [x] Phase 1: Restore `HTTP_PROXY` on Vane + ADR/CLAUDE.md rewrite
- [ ] Phase 2: Drop redundant `google-analytics.com` addition (Haiku ok)
- [ ] Phase 3: Repo hygiene — rename plan, delete one-off script (Haiku ok)

## Context

Two Vane containers were holding host port 3000 simultaneously during a long debug session: one from `start-agent.sh` and one from `research.py`. The browser at `localhost:3000` reached whichever container won the bind, so several conclusions reached during that period were not necessarily about the container we thought we were debugging. ADR-028 (commit `7392409`) extracted Vane out of `start-agent.sh`, leaving `research.py` as the sole Vane lifecycle. After the dedup, observations are unambiguous.

Empirical re-verification was performed via `tests/walkback-checks.py` and a recreate-vane-with-http-proxy.py one-off, with the following findings:

- **Feed swap** (`22963d1`, `domains/<x>.txt` → `wildcard/<x>-onlydomains.txt`): justified. Two of three probe domains (`doubleclick.net`, `googleadservices.com`) had subdomains-but-no-apex in upstream `domains/pro.txt`, which `denylist_to_squid_acl`'s dotted-suffix form (`research.py:425`) would have leaked. **Keep.**
- **TIF re-enable** (`22963d1`): justified. 1.15M-entry composed denylist, ~180 MB Squid RSS against a 2 GiB VM. **Keep.**
- **Six explicit Google ad apex additions** (`22963d1`): 5 of 6 are real coverage gaps absent from the cached hagezi feeds. **`google-analytics.com` is already in `pro-onlydomains.txt`** and is the one redundancy to clean up.
- **HTTPS_PROXY+NO_PROXY on Vane** (`2e23df3`): justified, load-bearing. The RESEARCH iptables chain (`research.py:486-548`) REJECTs research-net→external; without the proxy env vars, scrapes have no path out.
- **`HTTP_PROXY` omission on Vane** (`2e23df3` / ADR-027): **not justified.** With `HTTP_PROXY` added on a recreated `research-vane`, queries succeed and Squid `access.log` shows 80 fresh CONNECTs from 172.18.0.3 (Vane) — Springer/wikipedia/psychologytoday/substack/etc. — plus 7 `TCP_DENIED/403` denylist hits including `pagead2.googlesyndication.com`, `stats.g.doubleclick.net`, `securepubads.g.doubleclick.net`, `analytics.google.com`. The "Vane silently swallows fetch() with HTTP_PROXY set" claim does not reproduce against research-vane post-dedup; it was a wrong-Vane artifact.

This plan is the walk-back. The currently running `research-vane` already has `HTTP_PROXY` set (recreated by `tests/recreate-vane-with-http-proxy.py`), so the source change in Phase 1 catches up to reality — `./research.py` after the change is a no-op for the live stack.

## Goals

- `ensure_vane_container` (`research.py:895`) passes `HTTP_PROXY` alongside `HTTPS_PROXY` and `NO_PROXY` so the live state on next `--rebuild` matches the verified-working configuration.
- `tests/probe-vane-egress.sh` asserts `HTTP_PROXY` **present** instead of absent.
- ADR-027 is preserved as a historical record, marked Superseded; ADR-029 is the current decision and explains why the prior observation was unreliable.
- The `research.py key decisions` paragraph in `CLAUDE.md` mirrors ADR-029 instead of ADR-027.
- `templates/research-denylist-additions.txt` no longer lists `google-analytics.com` (already covered by `pro-onlydomains.txt`).
- `tests/walkback-checks.py`'s hardcoded `ADDITIONS` list mirrors the template.
- `plans/vane-debug-2026-04-26.md` is renamed to follow the `implemented-` naming convention used elsewhere in `plans/`.
- `tests/recreate-vane-with-http-proxy.py` is removed (saga-specific one-off; the maintained probe scripts cover the regression-check role).

---

## Phase 1: Restore `HTTP_PROXY` on Vane + ADR/CLAUDE.md rewrite

### Steps

1. In `research.py`, function `ensure_vane_container` (around line 895), add `HTTP_PROXY` to the env-var list passed to `docker run`, immediately before the existing `HTTPS_PROXY` line. Use the same value form (`http://{config.bridge_ip}:{SQUID_PORT}`). Resulting block should pass three `-e` flags: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`. The `print` line that says "configure LLM at …" stays unchanged.

2. In `tests/probe-vane-egress.sh`, replace the block at lines 33–38 (which currently FAILs if `HTTP_PROXY` is set) with the same shape as the existing `HTTPS_PROXY` check at lines 40–48 — assert non-empty and well-formed `http://host:port`. Keep the `NO_PROXY` check as-is. Update the file header comment (lines 4–7) to drop the "HTTP_PROXY is NOT set" line and replace with one that says all three proxy env vars must be present.

3. In `ADR.md`, edit the existing ADR-027 (lines 1470–1567):
   - Change `**Status:** Accepted` → `**Status:** Superseded by ADR-029`.
   - Append a short trailing paragraph under a new `### Update (post-dedup)` heading. Two-to-four sentences. Note that the dedup (ADR-028) revealed the original observation could not be reliably attributed to research-vane; subsequent direct testing with `HTTP_PROXY` set on research-vane succeeded, with Squid `access.log` confirming scrape CONNECTs and denylist enforcement on Vane's egress.

4. In `ADR.md`, append a new `## ADR-029: research.py: route Vane through Squid via both HTTP_PROXY and HTTPS_PROXY` after the current end of the file. Body sections: `**Date:** 2026-04-26`, `**Status:** Accepted (supersedes ADR-027)`, then `### Context`, `### Decision`, `### Consequences`. Required points:
   - **Context**: ADR-027 documented an HTTPS-only configuration based on a "queries hang at 'searching N queries'" observation. ADR-028's dedup revealed that during the period of that observation, two Vane containers were sharing host port 3000, and the browser was likely reaching `start-agent.sh`'s Vane (which had different env, different SearXNG sibling name `searxng`, and different network). Re-testing post-dedup with `HTTP_PROXY=http://{bridge_ip}:8888` set on research-vane shows queries succeed and Squid logs scrape CONNECTs through it — the regression does not reproduce.
   - **Decision**: `ensure_vane_container` passes `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY`. Justification is purely structural: the `RESEARCH` iptables chain (`research.py:486`) REJECTs research-net→external, so any scrape needs a proxy path; `NO_PROXY` exempts research-searxng and the host LLM endpoint, which are direct-bridge destinations.
   - **Consequences**: Both HTTP and HTTP**S** scrape targets are routed through Squid; denylist applies uniformly. `tests/probe-vane-egress.sh` updated to assert all three env vars present. ADR-027 marked Superseded. The deeper "why did the original observation look like Vane swallowing fetch()" question is not investigated further — most likely explanation per ADR-028 is that the `start-agent.sh` Vane could not resolve `research-searxng` after the user mutated its UI URL field, but that's not load-bearing for the current decision.

5. In `CLAUDE.md`, locate the paragraph beginning **`research.py` Vane container is wired through Squid via `HTTPS_PROXY` only.** in the `## research.py key decisions` section. Replace it with a paragraph titled **`research.py` Vane container is wired through Squid via `HTTP_PROXY` and `HTTPS_PROXY`.** Body should: (a) state that `ensure_vane_container` passes all three env vars, (b) point at the structural reason (RESEARCH iptables chain REJECTs research-net→external), (c) note `NO_PROXY` exempts in-network direct-bridge destinations, (d) cite ADR-029 (and that it supersedes ADR-027 after the ADR-028 dedup invalidated the original observation). Keep it the same length as the existing paragraph; this section is reference, not history.

### Files

- `research.py` (function `ensure_vane_container`, ~line 908)
- `tests/probe-vane-egress.sh`
- `ADR.md` (edit ADR-027 status + trailing update; append ADR-029)
- `CLAUDE.md` (paragraph in `## research.py key decisions`)

### Testing

- `python3 -c "import ast; ast.parse(open('research.py').read())"` parses clean.
- `bash -n tests/probe-vane-egress.sh` parses clean.
- Run `tests/probe-vane-egress.sh` against the currently-running `research-vane` (which already has `HTTP_PROXY` set). Expect three `ok` lines (HTTP_PROXY, HTTPS_PROXY, NO_PROXY) and the sidecar HTTPS round-trip `200`. The script returning exit 0 confirms Phase 1 step 2 is correctly inverted.
- `python3 tests/test_research.py` (if present) and `pytest tests/` — neither should regress; the iptables / denylist / squid / searxng helpers are not touched in this phase.
- Spot-check `git diff CLAUDE.md` reads as a same-shape paragraph swap, not an architectural addition.

### Commit

One commit covering all four files. Suggested subject: `vane: restore HTTP_PROXY on research-vane (rolls back ADR-027)`. Body explains the dedup invalidated the original observation and points at ADR-029. No `Co-Authored-By` line per `CLAUDE.md`'s commit-style note.

---

## Phase 2: Drop redundant `google-analytics.com` addition (Haiku ok)

### Steps

1. In `templates/research-denylist-additions.txt`, find the `=== Hagezi-omitted ad/tracking apexes ===` block (added by commit `22963d1`). Remove the single line `google-analytics.com`. Leave the comment header and the other 5 entries untouched. Optionally, append `# google-analytics.com is already in pro-onlydomains.txt; verified 2026-04-26` so the next reader sees why it's missing from this otherwise-Google-shaped list.

2. In `tests/walkback-checks.py`, remove `"google-analytics.com",` from the `ADDITIONS` list at the top of the file. The script will now report "5 real of 5" when invoked against the cleaned-up template, matching what the additions file actually claims to cover.

### Files

- `templates/research-denylist-additions.txt`
- `tests/walkback-checks.py`

### Testing

- `python3 -c "import ast; ast.parse(open('tests/walkback-checks.py').read())"` parses clean.
- After commit, on the host: `./research.py --reseed-denylist` to push the cleaned template to `~/.research/denylist-additions.txt`, then `./research.py --reload-denylist` to recompose Squid's ACL file. (Both flags exist; `--reseed-denylist` is documented at `research.py:166`, `--reload-denylist` at `research.py:154`.)
- `./tests/walkback-checks.py` should now report `5 real, 0 redundant of 5 additions` for T2.
- T4b should continue to show `TCP_DENIED` rows for `analytics.google.com` and `stats.g.doubleclick.net` after the reload — those match via the surviving `.doubleclick.net` and `pro-onlydomains.txt`-supplied `.google-analytics.com` suffixes, not via the removed bare entry.

### Commit

Single commit. Suggested subject: `denylist: drop google-analytics.com addition (already in pro-onlydomains)`. Body cites T2 of `tests/walkback-checks.py`.

---

## Phase 3: Repo hygiene — rename plan, delete one-off script (Haiku ok)

### Steps

1. Rename `plans/vane-debug-2026-04-26.md` to `plans/implemented-vane-debug-2026-04-26.md`. Use `git mv` so history follows the file. The other `implemented-` files in `plans/` use varied conventions (some have spaces around the dash, some don't); the `implemented-foo.md` form (no spaces) appears in the most recent ones (`implemented-allowlist-denylist-migration.md`, `implemented-research-vm-isolation.md`, `implemented-revise-research-vm-isolation.md`) — match that.

2. Delete `tests/recreate-vane-with-http-proxy.py`. It was a one-shot experiment for the dedup verification; `tests/walkback-checks.py` covers the regression-check role going forward. Use `git rm`.

### Files

- `plans/vane-debug-2026-04-26.md` → `plans/implemented-vane-debug-2026-04-26.md`
- `tests/recreate-vane-with-http-proxy.py` (deleted)

### Testing

- `git status` after the operations: one renamed file (showing as a rename, not a delete+add — confirms `git mv` was used) and one deletion.
- `ls plans/` shows the renamed file in expected position.
- `ls tests/` no longer lists `recreate-vane-with-http-proxy.py`; `walkback-checks.py` and the existing `probe-*.sh`/`test_*.py` files are intact.

### Commit

Single commit. Suggested subject: `cleanup: rename vane-debug plan as implemented; drop one-off recreate script`. Body one sentence.

---

## Notes

- The currently-running `research-vane` already has `HTTP_PROXY` set (recreated by `tests/recreate-vane-with-http-proxy.py` during the verification). Phase 1's `research.py` change brings the source into alignment with that state. On the next `./research.py --rebuild`, the rebuilt container will preserve `HTTP_PROXY`. No live-stack action is required during Phase 1 itself.
- Phase 2's denylist-additions cleanup is a behavior-no-op on the living denylist *until* `--reseed-denylist && --reload-denylist` is run — `templates/` is a seed file, not the live config (see `research.py:255` and `_seed_file`).
- ADR-027 is intentionally preserved (marked Superseded, with a tail note) rather than rewritten or deleted. The audit trail of the wrong-Vane confusion is itself useful institutional knowledge: the same shape of mistake (assuming a UI is talking to the container we configured) will likely happen again.
- `tests/walkback-checks.py` is being kept as a maintained probe. Its T1 / T2 claims are static (upstream feed contents + cache-dir lookup) and will continue to be falsifiable; T3 / T4 require the research VM to be running. If hagezi later changes feed paths or rolls the SHA forward, T1's hardcoded `HAGEZI_BASE` URL will need a corresponding bump — flag this in the script's docstring during Phase 2's edit if not already noted.
- No live tests of `--reseed-denylist` / `--reload-denylist` in `walkback-checks.py` were added during the saga; if a future regression makes that worthwhile, it would be an additive change, not in scope here.
