# Allowlist/Denylist Migration

## Status

- [ ] Phase 1: Expand start-agent.sh allowlist
- [ ] Phase 2: Convert research.py to denylist + rate limiting
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

Current state:
- `start-agent.sh`: ~150-entry inline heredoc allowlist (lines 221-370)
- `research.py`: ~280-entry allowlist in `templates/research-allowlist.txt`
- Both use `FilterDefaultDeny Yes` (allowlist mode)

## Goals

- start-agent.sh gains all READ-ONLY entries from research.py's allowlist
- research.py switches to denylist mode (`FilterDefaultDeny No`) so Vane can scrape arbitrary URLs
- research.py blocks known exfiltration hosts (paste sites, webhook capture, etc.)
- research.py adds iptables rate limiting as defense-in-depth against bulk exfil
- Keep start-agent.sh on allowlist (it runs Claude Code + OpenCode with more capabilities)

## Unknowns / To Verify

1. **iptables hashlimit module availability in Colima VMs** — the rate limiting
   implementation assumes `xt_hashlimit` is loaded. Verify with `lsmod | grep hashlimit`
   inside a running research VM before finalizing Phase 2. If unavailable, fall back to
   the basic `-m limit` module (less granular but always available).

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

## Phase 2: Convert research.py to denylist + rate limiting

This is the security-sensitive phase. Switch from allowlist to denylist mode and add
compensating controls.

### Steps

1. **Create denylist template** at `templates/research-denylist.txt`. Populate with:

   Known exfil hosts (from research-allowlist.txt's EXPLICITLY EXCLUDED section):
   ```
   # === Anonymous paste/upload ===
   pastebin.com
   hastebin.com
   paste.ee
   ix.io
   dpaste.com
   sprunge.us
   termbin.com
   transfer.sh
   file.io
   0x0.st
   catbox.moe
   litterbox.catbox.moe
   uguu.se
   
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
   
   # === Code hosting (write paths) ===
   # Block push/PR/issue creation; reads still work via raw.githubusercontent.com
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

2. **Update research.py constants and paths**:
   - Add `TEMPLATE_DENYLIST = Path(__file__).parent / "templates" / "research-denylist.txt"`
   - Update `Paths` class: rename `allowlist_file` → `denylist_file` property
   - Update path to `~/.research/denylist.txt`

3. **Update tinyproxy config** in `render_tinyproxy_conf()`:
   - Change `FilterDefaultDeny Yes` to `FilterDefaultDeny No`
   - Keep `Filter`, `FilterExtended Yes`, `FilterURLs No` unchanged

4. **Rename functions for clarity**:
   - `allowlist_to_regex_filter()` → `denylist_to_regex_filter()` (logic unchanged —
     still converts domain list to regex patterns)
   - `seed_allowlist()` → `seed_denylist()`
   - `reload_allowlist_fast_path()` → `reload_denylist_fast_path()`

5. **Update CLI flags**:
   - `--reload-allowlist` → `--reload-denylist`
   - `--reseed-allowlist` → `--reseed-denylist`
   - Update help text and epilog

6. **Add iptables rate limiting** to `render_iptables_apply_script()`. Insert before
   the final REJECT rule:

   ```
   # Rate limit: max 30 new connections/sec per source IP (burst 50).
   # Defense-in-depth against bulk exfil attempts.
   iptables -A RESEARCH -m conntrack --ctstate NEW -m hashlimit \
     --hashlimit-above 30/sec --hashlimit-burst 50 \
     --hashlimit-mode srcip --hashlimit-name research_newconn \
     -j DROP
   ```

   Add a fallback check: if `hashlimit` isn't available, use basic `-m limit`:
   ```
   # Fallback if hashlimit unavailable: global 100/sec limit
   iptables -A RESEARCH -m conntrack --ctstate NEW -m limit \
     --limit 100/sec --limit-burst 150 -j RETURN
   iptables -A RESEARCH -m conntrack --ctstate NEW -j DROP
   ```

   The Python code should probe for hashlimit availability and select the appropriate
   rule set.

7. **Update all references** in research.py:
   - Line 11: docstring mentions allowlist.txt
   - Line 17-18: usage examples
   - Line 110-113: epilog ALLOWLIST section
   - Line 535, 572, 575: apply_firewall references
   - Line 720, 721, 738: reload fast path
   - Line 757-783: main() allowlist handling
   - Line 809: status printout

8. **Handle migration**: Users with existing `~/.research/allowlist.txt` need a path
   forward. Add logic:
   - If `~/.research/allowlist.txt` exists and `~/.research/denylist.txt` doesn't,
     print a one-time migration notice explaining the change
   - The old allowlist file is ignored (not deleted) — user can reference it if needed
   - `--reseed-denylist` creates the new denylist from template

### Files

- `templates/research-denylist.txt` (new)
- `research.py` (extensive edits)
- `tests/test_research.py` (update test names and assertions)

### Testing

```bash
# Unit tests pass with renamed functions:
uv run pytest tests/test_research.py -v

# Start fresh research environment:
rm -rf ~/.research
./research.py --rebuild

# Verify denylist mode:
cat ~/.research/denylist.txt | head -20  # should show blocked hosts

# Verify Vane can scrape arbitrary URLs (not on denylist):
# In Vane UI, search for something and click a result — should load

# Verify denylist blocks exfil hosts:
docker exec research-vane curl -x http://$BRIDGE_IP:8888 https://pastebin.com -I
# Should fail with 403 Forbidden

# Verify rate limiting is active:
colima ssh -p research -- sudo iptables -L RESEARCH -v -n | grep hashlimit
# Should show the rate limit rule with packet counts

# Stress test (should trigger rate limit):
docker exec research-vane bash -c 'for i in $(seq 1 100); do curl -x http://$BRIDGE_IP:8888 https://example.com -s -o /dev/null & done; wait'
# Some requests should be dropped; check iptables counters
```

---

## Phase 3: Update documentation and ADRs

### Steps

1. **Add ADR-021** documenting the denylist decision:
   - Why denylist for research.py (Vane scraping utility)
   - Why allowlist remains for start-agent.sh (broader capability surface)
   - The hybrid defense model (denylist + rate limiting)
   - Known limitations (denylist can't enumerate all exfil channels)
   - The threat model tradeoff (human-supervised research vs automated agent)

2. **Update CLAUDE.md**:
   - Update the `research.py` description to mention denylist
   - Update the `--reseed-allowlist` → `--reseed-denylist` flag reference
   - Update the "research.py allowlist seed" section

3. **Update README.md** (if it documents research.py usage):
   - Denylist commands and flags
   - Security model explanation

4. **Update templates/global-claude.md**:
   - If it references research.py's allowlist, update to denylist

### Files

- `ADR.md`
- `CLAUDE.md`
- `README.md` (if applicable)
- `templates/global-claude.md` (if applicable)

### Testing

```bash
# Verify documentation is internally consistent:
grep -r "allowlist" . --include="*.md" | grep -i research
# Should only find historical references or explicit "formerly allowlist" notes
```

---

## Notes

**Security tradeoffs**:
- Denylist mode accepts that we cannot enumerate all possible exfil endpoints
- The compensating controls (known-bad-host denylist + rate limiting) raise the bar
  but don't eliminate risk
- This is acceptable for research.py because it's human-supervised (user watches Vane)
- start-agent.sh keeps allowlist because it runs Claude Code + OpenCode with autonomous
  tool use — higher capability means tighter egress control

**Rate limiting limitations**:
- iptables rate limiting is packet-based, not byte-based
- A slow trickle of data could still exfiltrate under the limit
- For true bandwidth limiting, would need `tc` (traffic control) — out of scope
- The 30/sec limit is a reasonable default; adjust in Phase 2 testing if needed

**Migration path**:
- Existing research.py users will need to run `--reseed-denylist` to get the new template
- Old `~/.research/allowlist.txt` is orphaned but not deleted — user can reference
  it to see what was previously allowed if debugging connectivity issues

**Denylist maintenance**:
- The denylist will need periodic updates as new exfil services emerge
- Consider adding a GitHub action or note to review denylist quarterly
