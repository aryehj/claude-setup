# Allowlist/Denylist Migration

## Status

- [x] Phase 1: Expand start-agent.sh allowlist (Haiku ok)
- [x] Phase 2: Convert research.py to layered denylist (Opus recommended)
  - Sub-phase deferred: migration notice (step 13) + new compose_denylist tests
- [ ] Phase 3: Update documentation and ADRs

## Context

Two related issues:

1. **start-agent.sh allowlist is too sparse** for OpenCode's web tools. The current
   ~150-entry allowlist (lines 221-370 in `start-agent.sh`) covers basic dev needs
   but lacks many research domains that `research.py` includes. When OpenCode fetches
   URLs, it hits 403s for domains not on the list.

2. **research.py's allowlist blocks Vane scraping**. Vane/Perplexica returns search
   results with arbitrary URLs, but the allowlist-based tinyproxy rejects fetches to
   unlisted domains. This severely limits research utility.

Threat model for research.py (revised — see ADR-021 in Phase 3):
- **Primary concern: research quality.** Filter out misinformation, content farms,
  AI-generated SEO slop, scam/fraud sites. This is what motivates moving to a denylist
  in the first place — Vane needs to reach arbitrary search-result URLs that an
  allowlist can't predict, but unfiltered results pollute research output.
- **Secondary concern: exfil hygiene.** Block legitimate-but-weaponizable services
  that prompt-injection payloads might reach for (pastebins, webhook capture,
  messaging APIs). Acknowledged limitation: a real adversary controls their
  destination domain, so this is hygiene, not a security guarantee. Human supervision
  of Vane is the actual exfil control.

Current state:
- `start-agent.sh`: ~150-entry inline heredoc allowlist (lines 221-370)
- `research.py`: ~280-entry allowlist in `templates/research-allowlist.txt`
- Both use `FilterDefaultDeny Yes` (allowlist mode)

## Goals

- start-agent.sh gains all READ-ONLY entries from research.py's allowlist (Phase 1)
- research.py switches to denylist mode (`FilterDefaultDeny No`) so Vane can scrape
  arbitrary URLs (Phase 2)
- research.py denylist composes from three sources (Phase 2):
  - Pinned upstream feeds (hagezi `multi.pro` + `fake` + `tif`) for quality + known-malicious
  - Local additions seed of ~50 curated exfil-capable services that upstream feeds won't include
  - Local overrides file as escape hatch for upstream false positives
- research.py adds iptables rate limiting as cheap defense-in-depth (Phase 2)
- Keep start-agent.sh on allowlist (it runs Claude Code + OpenCode with more autonomous
  tool use — higher capability means tighter egress control)
- Documentation reflects the new threat-model framing (Phase 3)

## Unknowns / To Verify

1. **Exact hagezi URL paths and list format.** The plan assumes raw GitHub URLs of the
   form `https://raw.githubusercontent.com/hagezi/dns-blocklists/<SHA>/domains/pro.txt`
   (and `fake.txt`, `tif.txt`). Verify the actual paths and that a domains-only format
   exists (vs hosts-file format requiring stripping `0.0.0.0` prefixes). Check
   <https://github.com/hagezi/dns-blocklists> README before pinning. *Affects: Phase 2
   step 1 (pinned URL list).*

2. **Hagezi pinning convention.** Verify whether hagezi cuts releases (tags) or whether
   commit SHAs are the only stable pin. Tags are more readable; SHAs are universal.
   Pick one and document it in the sources file format. *Affects: Phase 2 step 1.*

3. **Tinyproxy filter performance with ~165k entries.** Current allowlist is ~280
   regex patterns; the denylist will be three orders of magnitude larger. Verify
   tinyproxy load time and memory usage are acceptable. If filter compilation is slow,
   consider deduping aggressively or using a smaller hagezi tier. *Affects: Phase 2
   step 5; may motivate dropping `multi.pro` for a smaller tier if performance is bad.*

4. **iptables hashlimit module availability in Colima VMs.** Rate limiting assumes
   `xt_hashlimit` is loaded. Verify with `lsmod | grep hashlimit` inside a running
   research VM. If unavailable, fall back to basic `-m limit`. *Affects: Phase 2 step 6.*

---

## Phase 1: Expand start-agent.sh allowlist

Copy READ-ONLY entries from `templates/research-allowlist.txt` into start-agent.sh's
inline heredoc. This is a straightforward merge — add domains that exist in research
but not in start-agent.

### Steps

1. Read both allowlists and identify domains in research-allowlist.txt that are missing
   from start-agent.sh (lines 221-370).

2. Group the new entries by category (matching research-allowlist.txt's organization)
   and insert them into the appropriate sections of start-agent.sh's heredoc.

3. Skip UPLOAD-CAPABLE entries (commented section in research-allowlist.txt) — those
   remain excluded per the existing security model.

4. Preserve start-agent.sh's existing category comments and organization style.

### Files

- `start-agent.sh` (lines 221-370, allowlist heredoc)

### Testing

```bash
# After editing, verify the heredoc is syntactically valid:
bash -n start-agent.sh

# Start agent and verify expanded allowlist is seeded:
start-agent.sh --rebuild
cat ~/.claude-agent/allowlist.txt | wc -l  # should be ~280 lines

# Spot-check a newly-added domain works:
docker exec claude-agent curl -x http://$BRIDGE_IP:8888 https://arxiv.org -I
```

---

## Phase 2: Convert research.py to layered denylist

Three-layer model: pinned upstream feeds + local additions + local overrides.

```
Final denylist = (upstream-feeds ∪ additions) − overrides
```

### Steps

1. **Create the upstream sources template** at `templates/research-denylist-sources.txt`.
   Plain text, one entry per line. Each entry is a URL pinned to a specific commit SHA
   (or release tag, pending verification). Comments allowed.

   ```
   # research.py upstream denylist feeds — pinned by commit SHA.
   # Apply changes:  ./research.py --refresh-denylist
   # Format: <url>
   #
   # hagezi/dns-blocklists — verify URL paths against current README before pinning.
   # https://raw.githubusercontent.com/hagezi/dns-blocklists/<PINNED_SHA>/domains/pro.txt
   # https://raw.githubusercontent.com/hagezi/dns-blocklists/<PINNED_SHA>/domains/fake.txt
   # https://raw.githubusercontent.com/hagezi/dns-blocklists/<PINNED_SHA>/domains/tif.txt
   ```

   Implementer must replace `<PINNED_SHA>` with a real, verified SHA before shipping.
   Do not pin to `main` / `HEAD` — that lets upstream changes flow through unreviewed.

2. **Create the additions seed** at `templates/research-denylist-additions.txt`.
   These are the legitimate-but-exfil-capable services that hagezi feeds won't catch
   (because they're not malicious infrastructure):

   ```
   # research.py local denylist additions — hosts NOT in upstream feeds.
   # Edit on the macOS host; apply with --reload-denylist.
   #
   # These are legitimate services that prompt-injection payloads might
   # weaponize for exfil. Hagezi/StevenBlack/etc. won't include these
   # because they're not malicious infrastructure.

   # === Anonymous paste/upload ===
   pastebin.com
   hastebin.com
   paste.ee
   ix.io
   dpaste.com
   sprunge.us
   termbin.com
   rentry.co
   justpaste.it
   paste.gg
   transfer.sh
   file.io
   0x0.st
   catbox.moe
   litterbox.catbox.moe
   uguu.se
   gofile.io
   wetransfer.com

   # === Webhook capture / request inspection ===
   webhook.site
   requestbin.com
   pipedream.com
   hookbin.com
   beeceptor.com
   requestcatcher.com

   # === Messaging / DM delivery ===
   discord.com
   discordapp.com
   slack.com
   telegram.org
   api.telegram.org

   # === Reverse tunnels ===
   ngrok.io
   ngrok.app
   trycloudflare.com
   localhost.run
   serveo.net
   bore.pub

   # === Code hosting (write paths) ===
   # Reads still work via raw.githubusercontent.com / codeload.github.com
   github.com
   gitlab.com
   bitbucket.org

   # === Dataset/model upload hubs ===
   huggingface.co
   zenodo.org
   figshare.com
   osf.io
   datadryad.org
   kaggle.com
   ```

3. **Define the file layout** in `~/.research/`:
   - `denylist-sources.txt` — copied from template on first run; user-editable
   - `denylist-additions.txt` — copied from template on first run; user-editable
   - `denylist-overrides.txt` — empty by default; user adds entries here to remove
     domains from the final filter that are otherwise pulled in by upstream feeds (FP
     escape hatch). Same one-domain-per-line format.
   - `denylist-cache/` — fetched copies of each upstream feed, named after the URL's
     last path component (e.g., `pro.txt`, `fake.txt`, `tif.txt`). Refreshed by
     `--refresh-denylist`.

4. **Update research.py constants and Paths**:
   - Drop `TEMPLATE_ALLOWLIST`. Add:
     ```python
     TEMPLATE_DENYLIST_SOURCES = Path(__file__).parent / "templates" / "research-denylist-sources.txt"
     TEMPLATE_DENYLIST_ADDITIONS = Path(__file__).parent / "templates" / "research-denylist-additions.txt"
     ```
   - In `Paths`: drop `allowlist_file`. Add properties for `denylist_sources_file`,
     `denylist_additions_file`, `denylist_overrides_file`, `denylist_cache_dir`.

5. **Update tinyproxy config** in `render_tinyproxy_conf()`:
   - Change `FilterDefaultDeny Yes` to `FilterDefaultDeny No`.
   - Keep `Filter`, `FilterExtended Yes`, `FilterURLs No` unchanged.
   - **Verify performance** with the ~165k-entry filter. If load time exceeds ~5s or
     memory usage is excessive, drop `multi.pro` from the upstream sources and use
     `multi.normal` (smaller tier) instead, or dedupe more aggressively.

6. **Implement the compose function**:
   ```python
   def compose_denylist(paths: Paths) -> List[str]:
       """Build the final denylist as: (cached-upstream ∪ additions) − overrides."""
   ```
   - Reads every `*.txt` file in `denylist_cache_dir`, strips comments and whitespace.
   - Reads `denylist_additions_file`, strips comments and whitespace.
   - Reads `denylist_overrides_file`, strips comments and whitespace.
   - Returns sorted, deduped list with overrides removed.
   - Hagezi domains-format files are bare domains. If they ship as hosts-file format
     (`0.0.0.0 example.com`), strip the IP prefix here.

7. **Implement the fetch function**:
   ```python
   def refresh_denylist_cache(paths: Paths) -> None:
       """Download each URL in denylist_sources_file into denylist_cache_dir."""
   ```
   - Reads `denylist_sources_file`, fetches each URL with `urllib` (or `requests` if
     already a dep — check pyproject.toml).
   - Writes each response to `denylist_cache_dir/<basename>` atomically (write to
     `.tmp` then rename).
   - On fetch failure: warn loudly, leave existing cached file in place, continue with
     other URLs. Do not abort.
   - On first run with no cache and a fetch failure for *any* URL: abort with a clear
     error directing the user to check connectivity and re-run `--refresh-denylist`.
     Do not start a research VM with a partial denylist silently.

8. **Update CLI flags**:
   - Drop `--reload-allowlist`, `--reseed-allowlist`.
   - Add `--reload-denylist` — regenerate filter from local files (cache + additions −
     overrides). No network. Fast path; does not restart containers.
   - Add `--refresh-denylist` — fetch fresh upstream copies, then regenerate filter.
     Implies `--reload-denylist`.
   - Add `--reseed-denylist` — overwrite both `denylist-sources.txt` and
     `denylist-additions.txt` from templates. Use after pulling repo updates.
     `denylist-overrides.txt` is never overwritten (user state).

9. **Rename the regex helper**: `allowlist_to_regex_filter()` →
   `denylist_to_regex_filter()`. Logic is unchanged — it still converts a list of
   domains to anchored regex patterns. Keep the docstring updated.

10. **Add iptables rate limiting** to `render_iptables_apply_script()`. Insert before
    the final REJECT rule:

    ```
    # Rate limit: max 30 new connections/sec per source IP (burst 50).
    # Defense-in-depth against bulk exfil; secondary to denylist.
    iptables -A RESEARCH -m conntrack --ctstate NEW -m hashlimit \
      --hashlimit-above 30/sec --hashlimit-burst 50 \
      --hashlimit-mode srcip --hashlimit-name research_newconn \
      -j DROP
    ```

    Probe for hashlimit availability before generating the script. If absent, fall
    back to basic `-m limit`:
    ```
    iptables -A RESEARCH -m conntrack --ctstate NEW -m limit \
      --limit 100/sec --limit-burst 150 -j RETURN
    iptables -A RESEARCH -m conntrack --ctstate NEW -j DROP
    ```

11. **Bootstrap flow on first run** (no `~/.research/` yet):
    1. Create `~/.research/` and subdirs.
    2. Seed `denylist-sources.txt` and `denylist-additions.txt` from templates.
    3. Create empty `denylist-overrides.txt`.
    4. Fetch upstream feeds into `denylist-cache/`. Abort on any fetch failure (see
       step 7).
    5. Compose final denylist, render filter file, proceed with VM bring-up.

12. **Update all references** in research.py (line numbers approximate, current as of
    HEAD):
    - Line 11: docstring mentions allowlist.txt
    - Line 17-18: usage examples
    - Line 38: `TEMPLATE_ALLOWLIST` constant
    - Line 58-59: `Paths.allowlist_file` property
    - Line 110-113: epilog ALLOWLIST section
    - Line 128-138: argparse `--reload-allowlist` / `--reseed-allowlist`
    - Line 185-198: `seed_allowlist()`
    - Line 203-215: `allowlist_to_regex_filter()`
    - Line 263: tinyproxy `FilterDefaultDeny Yes`
    - Line 535, 572, 575: apply_firewall references
    - Line 716-738: `reload_allowlist_fast_path()`
    - Line 757-783: main() allowlist handling
    - Line 809: status printout

13. **Migration handling for existing users**:
    - On first run, if `~/.research/allowlist.txt` exists and the new files don't,
      print a one-time migration notice explaining the change and pointing to
      `--refresh-denylist`.
    - Do not delete the old allowlist file — leave it as a reference for the user to
      compare against.

14. **Delete the old template** `templates/research-allowlist.txt` once Phase 1 has
    consumed it (Phase 1 copies entries into start-agent.sh; nothing else references
    the file after that).

### Files

- `templates/research-denylist-sources.txt` (new — pinned URL list)
- `templates/research-denylist-additions.txt` (new — exfil-capable services seed)
- `templates/research-allowlist.txt` (delete after Phase 1)
- `research.py` (extensive edits)
- `tests/test_research.py` (rename tests, add tests for `compose_denylist` set algebra)

### Testing

```bash
# Unit tests pass:
uv run pytest tests/test_research.py -v
# Specifically: tests for compose_denylist that verify
# (cache ∪ additions) − overrides set algebra.

# Fresh bootstrap:
rm -rf ~/.research
./research.py --rebuild
# Should: seed sources, fetch upstream, compose filter, start containers.

# Verify denylist mode:
ls ~/.research/denylist-cache/  # pro.txt, fake.txt, tif.txt
wc -l ~/.research/denylist-cache/*.txt  # ~165k combined

# Verify Vane can scrape arbitrary unblocked URLs:
# (in Vane UI) search and click a result — should load.

# Verify exfil-additions block:
docker exec research-vane curl -x http://$BRIDGE_IP:8888 https://pastebin.com -I
# Should fail with 403 Forbidden.

# Verify upstream-feed block (pick a domain known to be in hagezi pro):
docker exec research-vane curl -x http://$BRIDGE_IP:8888 https://<known-blocked-host> -I
# Should fail with 403.

# Verify override escape hatch:
echo 'pastebin.com' >> ~/.research/denylist-overrides.txt
./research.py --reload-denylist
docker exec research-vane curl -x http://$BRIDGE_IP:8888 https://pastebin.com -I
# Should now succeed.

# Verify rate limit rule is present:
colima ssh -p research -- sudo iptables -L RESEARCH -v -n | grep -E 'hashlimit|limit:'

# Verify --refresh-denylist updates cache without restarting containers:
./research.py --refresh-denylist
# Should re-fetch and SIGHUP tinyproxy; containers stay up.
```

---

## Phase 3: Update documentation and ADRs

### Steps

1. **Add ADR-021** documenting the layered denylist decision. Frame around:
   - Why denylist for research.py: Vane scraping needs arbitrary URLs; allowlist
     blocks the search-result long tail; quality filtering is the headline motivation,
     not exfil defense.
   - Why allowlist remains for start-agent.sh: Claude Code + OpenCode have more
     autonomous tool surface than Vane; tighter control warranted.
   - The three-layer model: upstream feeds (hagezi pro + fake + tif), local additions
     (exfil-capable services), local overrides (FP escape hatch).
   - Pinning policy: commit SHAs (or release tags), never `HEAD`.
   - Known limitations: denylist can't catch attacker-controlled fresh domains
     (acknowledged residual risk; human supervision is the actual control).
   - Rate limiting as defense-in-depth, not primary control.

2. **Add ADR-022** for the start-agent.sh allowlist expansion. Brief — just record
   that READ-ONLY entries from research.py were folded in to support OpenCode usage.

3. **Update CLAUDE.md**:
   - Replace the "research.py allowlist seed" section with the layered-denylist model.
   - Update flag references: `--reload-allowlist` → `--reload-denylist`,
     `--reseed-allowlist` → `--reseed-denylist`. Add `--refresh-denylist`.
   - Note the file layout under `~/.research/` (sources, additions, overrides, cache).

4. **Update README.md** if it documents research.py usage:
   - Denylist commands and flags.
   - Threat-model framing (quality > exfil hygiene).
   - Refresh cadence guidance (weekly minimum; tif benefits from daily).

5. **Update templates/global-claude.md** if it references research.py's allowlist.

### Files

- `ADR.md` (add ADR-021, ADR-022)
- `CLAUDE.md`
- `README.md` (if applicable)
- `templates/global-claude.md` (if applicable)

### Testing

```bash
# Verify documentation is internally consistent — no stale "allowlist" references:
grep -rn "allowlist" . --include="*.md" --include="*.py" --include="*.sh" \
  | grep -i research | grep -v "former\|previous\|migrated\|ADR-019\|ADR-020"
# Output should be empty (or only intentional historical references).
```

---

## Notes

**Threat-model framing (revised from earlier draft)**:
- Primary motivation is *research quality*: filter misinformation, content farms, AI
  slop, scams. The bigness of upstream feeds is load-bearing for this — quality
  filtering benefits from broad coverage.
- Secondary motivation is *exfil hygiene*: block legitimate-but-weaponizable services.
  This is what the additions seed exists for; upstream feeds won't include
  github.com/discord.com/etc. because they're not malicious.
- A real adversary with a controlled domain bypasses both layers. The actual exfil
  control is human supervision of Vane, not the proxy. This is acceptable because
  research.py is interactive, not autonomous.

**Why three layers and not two**:
- Upstream feeds + additions alone would mean any false positive in upstream is
  unfixable without modifying the cache (which `--refresh-denylist` would clobber).
  The overrides file gives the user a stable escape hatch.

**Pinning policy**:
- Never auto-fetch `HEAD`. Upstream maintainers (or attackers compromising upstream)
  could push entries that block legitimate research domains, and a default-on
  auto-fetch would propagate that immediately. SHA pinning forces a human review of
  upstream changes before they take effect locally.
- `--refresh-denylist` does not change pins — it fetches the *currently pinned* SHA's
  content. To pick up upstream updates, the user runs `--reseed-denylist` (which
  overwrites `denylist-sources.txt` from the repo template) and then `--refresh-denylist`.

**Rate limiting limitations** (unchanged from earlier draft):
- iptables rate limiting is packet-based, not byte-based.
- A slow trickle of data could still exfiltrate under the limit.
- For true bandwidth limiting, would need `tc` (traffic control) — out of scope.

**Maintenance cadence**:
- `tif` (threat intel feed) benefits most from frequent refresh — entries rotate as
  threats are taken down. Weekly minimum, daily ideal.
- `pro` and `fake` are stable; monthly refresh is fine.
- The `denylist-additions.txt` seed is stable; updates flow through repo commits and
  are picked up via `--reseed-denylist`.

**Performance risk**:
- Tinyproxy with ~165k regex patterns is untested in this codebase. If load time or
  memory is bad, the first lever is dropping `multi.pro` for `multi.normal` (smaller
  tier). The second lever is moving filtering out of tinyproxy entirely (e.g., DNS-
  level via dnsmasq) — but that's a much larger change and out of scope for this plan.
