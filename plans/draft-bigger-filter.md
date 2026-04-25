# Replace tinyproxy with a filter that scales past ~400k entries

## Status

- [ ] Phase 1: Pick a replacement (Squid the leading candidate)
- [ ] Phase 2: Swap research.py over; keep start-agent.sh on tinyproxy

## Context

The Phase-2 denylist migration discovered a hard ceiling: tinyproxy with
`FilterExtended Yes` compiles every line of `/etc/tinyproxy/filter` into a POSIX
extended regex, and the in-memory NFA blows up superlinearly with entry count.

Observed on the research VM (default 2 GiB):
- ~409k entries (hagezi `pro` + `fake`) → OOM-killed at startup.
- Empty filter → tinyproxy comes up fine; confirmed regex compile is the cause.
- ~14k entries (`fake` alone) is comfortable but throws away the broad ad /
  tracking / scam coverage that motivates the denylist in the first place.

The current workaround is `--rebuild --memory=4` (or larger). That works but is
a recurring tax: any user who wants `pro` *and* `tif` (~1.56M combined) has to
size the VM at 4-8 GiB full-time, and the regex compile is still O(seconds) on
every reload. We've also pinned `tif` commented-out in the shipped template,
which means the default install ships with much weaker threat-intel coverage
than the upstream feed offers.

## The structural fix

tinyproxy is the wrong tool for million-entry deny lists. It was built for
small per-deployment block lists (dozens to a few thousand entries), and its
filter is a regex engine, not a domain matcher. Domain-set matching is O(n)
memory and O(1) lookup; regex compilation is neither.

Replacements that handle large domain sets natively:

- **Squid** with `acl ... dstdomain "/etc/squid/denylist.txt"` — domains are
  hashed, not regex-compiled. Million-entry lists are routine. Mature,
  well-documented HTTP CONNECT proxy. Heavier than tinyproxy but that's the
  point. Most-likely landing spot.
- **3proxy** — lighter than Squid but the ACL syntax is less obviously suited
  to giant external lists. Worth a look if Squid feels overweight.
- **DNS-level filtering** (dnsmasq with `addn-hosts`, or unbound + `local-data`)
  — moves filtering out of the proxy entirely. Faster lookups, but loses the
  per-request HTTP semantics tinyproxy/Squid give us (e.g., distinguishing
  CONNECT from GET, returning a real 403 to curl). Probably not the right
  shape for this codebase.

## Goals

- research.py can compose denylists in the millions of entries without OOM
  on a default-sized VM.
- `pro` + `fake` + `tif` ship uncommented in the template (no per-user
  uncommenting required to get full hagezi coverage).
- Filter reload stays sub-second so `--reload-denylist` remains a fast path.
- start-agent.sh stays on tinyproxy. Its allowlist is ~280 entries; the regex
  approach is fine at that size and tinyproxy is simpler. No reason to drag
  Squid into the agent path.
- Threat-model framing is unchanged — denylist still composes from
  `(cache ∪ additions) − overrides`; only the matcher backend changes.

## Unknowns / To Verify

1. **Squid memory footprint at 1.5M dstdomain entries.** Squid loads the list
   into a hash table; expected ~150-300 MB but not measured. May still need
   --memory=4, just not for the regex compile.
2. **Whether Squid's `dstdomain` does the suffix-match we want by default.**
   It does for `.example.com` (with leading dot); bare `example.com` is exact.
   The compose layer would need to emit dotted form. Verify behavior matches
   the current `(^|\.)example\.com$` regex.
3. **CONNECT-only mode for HTTPS.** Squid is conventionally an HTTP proxy that
   also handles CONNECT for HTTPS, same as tinyproxy. Should be drop-in for the
   tinyproxy listener role, but the systemd unit / config shape is different
   enough that the install_tinyproxy / render_tinyproxy_conf paths in
   research.py both need rewriting.
4. **Reload semantics.** tinyproxy reloads its filter on SIGHUP. Squid uses
   `squid -k reconfigure`; verify it's just as cheap and doesn't drop in-flight
   connections.

## Notes

- This plan is a *consequence* of the Phase-2 migration, not a regression — the
  old allowlist (~280 entries) didn't strain tinyproxy. Moving to a denylist
  is what blew past its scale.
- Keep the file-layout contract stable: `~/.research/denylist-{sources,
  additions,overrides}.txt` and `~/.research/denylist-cache/` should look the
  same to the user. Only the in-VM proxy daemon changes.
- ADR worth writing once Phase 2 lands: "research.py uses Squid; start-agent.sh
  stays on tinyproxy; here's why the asymmetry is correct."
