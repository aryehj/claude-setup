# Replace tinyproxy with Squid in research.py

## Status

- [ ] Phase 1: Swap tinyproxy for Squid in research.py (clean cut)
- [ ] Phase 2: Re-enable hagezi `tif` by default + docs/ADR

## Context

The Phase-2 denylist migration (`plans/allowlist-denylist-migration.md`) hit a
hard ceiling: tinyproxy with `FilterExtended Yes` compiles every line of
`/etc/tinyproxy/filter` into a POSIX extended regex, and the in-memory NFA
blows up superlinearly with entry count.

Observed on the research VM (default 2 GiB), with the Phase-2 layered denylist
loaded:

| Filter shape                          | Behavior                          |
| ------------------------------------- | --------------------------------- |
| Empty                                 | Starts. Serves requests fine.     |
| ~14k entries (`fake` only)            | Starts. Serves requests fine.     |
| ~409k entries (`pro` + `fake`)        | **Starts**, dies on first non-matching CONNECT. Requests that match early in the regex (e.g. `pastebin.com` → 403) succeed; non-matches walk the whole list and OOM-kill the process. |
| ~1.56M entries (`pro` + `fake` + `tif`) | OOM-kills tinyproxy at startup.   |

The critical finding from the Phase-2 testing session: **the failure is
per-request, not just startup.** Bumping the VM to 4 GiB might let the
process start at full coverage, but every non-matching CONNECT still walks
the regex state machine — so RAM headroom shrinks with concurrent requests
and the failure mode becomes "works until it doesn't." That's worse than
the current "fails immediately" because it'll surface as flakey research
sessions rather than a hard error.

The shipped template now has `tif` commented out as a stopgap, but the
right answer is to stop using a regex engine for domain matching.

## The replacement: Squid

Squid's `acl ... dstdomain "/path/to/list"` does literal domain matching
backed by a hash table. Lookup is O(1) per request, memory is O(n) entries
(not O(regex-state)), and million-entry lists are routine for it.

This is what Squid was designed for. tinyproxy was not.

Other options were considered and rejected:
- **3proxy**: lighter than Squid but the ACL syntax is less obviously suited
  to giant external lists; documentation and operational track record at this
  scale are thinner.
- **DNS-level filtering** (dnsmasq / unbound): faster lookups, but loses the
  HTTP-layer 403 semantics tinyproxy/Squid give us. Clients would see
  NXDOMAIN-shaped failures rather than a real "filtered" response.

## Goals

- research.py runs the layered denylist (cache ∪ additions − overrides) with
  full hagezi coverage (`pro` + `fake` + `tif`, ~1.56M entries) on the default
  2 GiB VM, without OOM, with sub-second filter reload.
- Drop-in for callers: `~/.research/denylist-{sources,additions,overrides}.txt`
  + `denylist-cache/` keep the same shape and same compose semantics. The
  user-visible CLI (`--reload-denylist`, `--refresh-denylist`,
  `--reseed-denylist`) is unchanged.
- start-agent.sh stays on tinyproxy. Its allowlist is ~280 entries; the regex
  approach is fine at that size and the agent path doesn't need a Squid
  dependency. The asymmetry is intentional.
- Clean cut in research.py: no `--proxy=tinyproxy|squid` toggle, no fallback
  code paths, no migration-period bilingualism. The old tinyproxy code is
  deleted in this plan, not left behind a flag.

## Unknowns / To Verify

1. **Squid `dstdomain` suffix-match semantics.** Squid matches `.example.com`
   (with leading dot) as "example.com and any subdomain", and bare
   `example.com` as exact-match. The current `denylist_to_regex_filter()`
   anchors with `(^|\.)example\.com$` which is the suffix form. The new
   compose-to-Squid path needs to emit `.example.com` (dotted) for every
   entry. *Affects: Phase 1 step 4 (compose output format).*

2. **Squid memory footprint at ~1.56M dstdomain entries.** Expected ~150-300 MB
   resident for the ACL table; not measured. If it lands above 1 GiB on the
   2 GiB VM, falling back to `--memory=4` is acceptable but worth knowing
   before claiming "default-VM works." *Affects: Phase 1 step 7 (testing).*

3. **Reload semantics.** Squid uses `squid -k reconfigure`. Verify it's
   sub-second for our list size and doesn't drop in-flight CONNECTs (it
   shouldn't — reconfigure rotates the ACL table, in-flight tunnels are not
   re-checked). *Affects: Phase 1 step 6 (reload fast path).*

4. **systemd unit shape.** Debian's `squid` package ships a unit; verify it
   reads the conf from `/etc/squid/squid.conf` and that we can drop a
   minimal config that only sets the listener, the dstdomain ACL, and
   logging — not the kitchen-sink default config. *Affects: Phase 1 step 5
   (config render).*

5. **CONNECT-port restriction.** tinyproxy has `ConnectPort 443` /
   `ConnectPort 80`. Squid's equivalent is `acl SSL_ports port 443` plus
   `http_access deny CONNECT !SSL_ports`. Verify the equivalent denies
   plaintext CONNECT to non-443 by default. *Affects: Phase 1 step 5.*

6. **iptables RESEARCH chain compatibility.** The current chain whitelists
   `bridge_ip:8888` (tinyproxy port). Squid's default port is 3128. We can
   either keep 8888 (override Squid's listener) or move the chain to 3128.
   Keeping 8888 is fewer moving parts — SearXNG's `outgoing.proxies` config
   in `~/.research/searxng/settings.yml` already points at 8888 and a port
   change forces drift cleanup. *Affects: Phase 1 step 5 (Squid listener
   port) + step 6 (no iptables changes needed).*

---

## Phase 1: Swap tinyproxy for Squid in research.py

Single-PR clean cut. tinyproxy install/config/reload paths are deleted, not
gated.

### Steps

1. **Drop the deprecated `FilterExtended` warning baseline.** This isn't a
   step, it's a sanity note: the old tinyproxy was warning `line 13:
   deprecated option FilterExtended, use FilterType` at startup. We never
   address it because the daemon is being removed. Mention in PR description.

2. **Rename `install_tinyproxy()` → `install_squid()`.** Idempotent
   `apt-get install -y squid` via vm_sh. Drop tinyproxy. Squid's package
   pulls in a default `/etc/squid/squid.conf` and starts a service we'll
   immediately overwrite, so `systemctl stop squid` after install is part of
   this step.

3. **Drop `denylist_to_regex_filter()`.** Replace with
   `denylist_to_squid_acl(domains: List[str]) -> str` that emits one
   `.example.com` per line (dotted-suffix form). No regex escaping.

4. **Update compose path.** `compose_denylist()` is unchanged — it still
   returns `List[str]` of bare domains. The conversion to Squid format
   happens at write-out time in `apply_firewall()` /
   `reload_denylist_fast_path()`.

5. **Replace `render_tinyproxy_conf()` with `render_squid_conf()`.** Minimal
   config (target ~25 lines), explicitly *not* including the default Debian
   `/etc/squid/squid.conf.default`. Sketch:

   ```
   http_port {bridge_ip}:8888
   visible_hostname research-squid

   acl denylist dstdomain "/etc/squid/denylist.txt"
   acl SSL_ports port 443
   acl Safe_ports port 80 443

   http_access deny denylist
   http_access deny CONNECT !SSL_ports
   http_access deny !Safe_ports
   http_access allow all

   access_log /var/log/squid/access.log
   cache deny all
   ```

   `cache deny all` is load-bearing: this is a forward proxy for filtering,
   not a caching proxy. Disabling the cache also avoids needing to size
   `cache_dir`.

6. **Rewrite `apply_firewall()` and `reload_denylist_fast_path()`.**
   - `apply_firewall()`: write `/etc/squid/squid.conf` and
     `/etc/squid/denylist.txt`, then `systemctl restart squid`. Iptables
     rules unchanged (RESEARCH chain still whitelists `bridge_ip:8888`).
   - `reload_denylist_fast_path()`: write only `/etc/squid/denylist.txt`,
     then `squid -k reconfigure`. Don't restart unless reconfigure fails.

7. **Update entry-count printout and docstrings.** All references to
   "tinyproxy" / "allowlist regex" in `research.py` (including the comment
   in `denylist_to_regex_filter`'s old docstring, now removed). Module
   docstring should say "Squid + iptables RESEARCH chain" instead of
   "tinyproxy + iptables RESEARCH chain."

### Files

- `research.py` — extensive edits to install / config render / apply / reload
  paths. CLI surface unchanged.
- `tests/test_research.py` — replace `test_denylist_filter_*` tests
  (regex-filter shape) with `test_denylist_squid_acl_*` tests (dotted
  suffix-form output). Add tests for the new `denylist_to_squid_acl` helper.
  iptables/SearXNG tests untouched.

### Testing

```bash
# Unit tests pass:
uv run --with pytest pytest tests/test_research.py -v

# Fresh bring-up on default 2 GiB VM, full hagezi coverage:
./research.py --rebuild
# Say no to VM-delete prompt the first time; size is fine.
# Confirm Squid is up:
colima ssh -p research -- systemctl is-active squid

# Filter loaded:
colima ssh -p research -- wc -l /etc/squid/denylist.txt
# Expect ~1.56M lines once tif is uncommented in Phase 2.

# Block enforced (matching domain):
colima ssh -p research -- bash -c '
  BIP=$(docker network inspect bridge -f "{{(index .IPAM.Config 0).Gateway}}")
  docker run --rm curlimages/curl:latest -x http://$BIP:8888 -I https://pastebin.com
'
# Expect 403 from Squid.

# Allow path (non-matching domain) — this is the regression test for the
# tinyproxy OOM-on-non-match failure:
colima ssh -p research -- bash -c '
  BIP=$(docker network inspect bridge -f "{{(index .IPAM.Config 0).Gateway}}")
  docker run --rm curlimages/curl:latest -x http://$BIP:8888 -I https://example.com
'
# Expect 200, and squid still active afterward:
colima ssh -p research -- systemctl is-active squid

# Reload is fast and doesn't break in-flight requests:
echo 'example.com' >> ~/.research/denylist-overrides.txt
time ./research.py --reload-denylist
# Expect <2s end-to-end. 'example.com' should now succeed (override removes it).
```

---

## Phase 2: Re-enable `tif` by default + docs/ADR

### Steps

1. **Uncomment `tif` in `templates/research-denylist-sources.txt`.** Update
   the inline note to point at this plan's resolution rather than the OOM
   warning. The "verify Squid handles 1.56M entries" gate is Phase 1's
   Testing section; if that passes, this is mechanical.

2. **Add ADR-023**: "research.py uses Squid; start-agent.sh stays on
   tinyproxy." Frame around:
   - Why the asymmetry: scale of the lists, not script personality.
   - Why we didn't unify: start-agent.sh's ~280 entries don't need it, and
     pulling Squid into the agent path adds a dependency for no benefit.
   - The dropped alternatives (3proxy, DNS-level) and the reason for each.
   - Pointer to the Phase-1 perf data (the "Filter shape / Behavior" table
     from this plan's Context section).

3. **Update CLAUDE.md.** The "research.py allowlist seed" / "layered denylist
   model" section needs to swap "tinyproxy" → "Squid" wherever it appears in
   the research.py-specific paragraphs. The start-agent.sh paragraphs are
   untouched — those still describe tinyproxy correctly.

4. **Update README.md** if it documents research.py's proxy daemon.

### Files

- `templates/research-denylist-sources.txt`
- `ADR.md` (add ADR-023)
- `CLAUDE.md`
- `README.md` (if applicable)

### Testing

```bash
# Verify no stale tinyproxy references remain in research.py-related docs:
grep -rn -i tinyproxy . --include="*.md" --include="*.py" --include="*.sh" \
  | grep -i research
# Output should be empty (start-agent.sh references are fine; research-related
# ones should all be gone).
```

---

## Notes

**Why clean cut and not a `--proxy=` toggle.** The toggle is appealing as a
safety net, but it doubles the test matrix forever and gives users a knob to
fall back into the broken state. The actual safety net is `git revert` of
the Phase-1 commit if Squid surprises us — that's a single operation and
restores a known-good codebase, vs. a flag we'd carry indefinitely.

**Why this isn't ADR-021.** ADR-021 is the Phase-2 denylist threat-model
decision. ADR-023 (this plan's) is a follow-on operational decision about
backend choice; the threat model and three-layer compose semantics from
ADR-021 are unchanged.

**What this plan does NOT change:**
- The compose semantics: `(cached-upstream ∪ additions) − overrides`.
- The CLI: `--reload-denylist`, `--refresh-denylist`, `--reseed-denylist`.
- The file layout under `~/.research/`.
- The iptables RESEARCH chain (Squid keeps the same listener port 8888).
- The hagezi pinning policy (still SHA-pinned in
  `templates/research-denylist-sources.txt`).
- start-agent.sh — entirely out of scope.

**Likely follow-ups, deliberately out of scope:**
- Caching cleanup of stale denylist entries (Squid's `acl dstdomain` doesn't
  need a TTL, but `--refresh-denylist` cadence guidance in docs is worth
  revisiting once tif is back).
- Per-source (per-feed) accounting / metrics. Useful for "is hagezi pro
  pulling its weight?" type questions but not load-bearing for this plan.
- A perf regression test that asserts request latency stays under, say,
  100ms p99 on the full denylist. Belongs in `plans/draft-tests.md` if/when
  that plan moves forward.
