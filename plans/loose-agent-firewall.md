# Loosen claude-agent egress firewall to mirror research.py

## Status

- [x] Phase 1: branch + denylist state machinery
- [ ] Phase 2: replace tinyproxy with Squid in start-agent.sh
- [ ] Phase 3: rewire CLI flags, legacy guard, container env
- [ ] Phase 4: smoke-verify the loosened path
<!-- mark [x] as phases complete during implementation. -->

## Context

`start-agent.sh` enforces a default-deny egress firewall for the `claude-agent` container: tinyproxy with `FilterDefaultDeny Yes` (lines 762–779) plus an iptables `CLAUDE_AGENT` chain that REJECTs everything except a few RETURN carve-outs (lines 833–845). The allowlist file (`~/.claude-agent/allowlist.txt`) is seeded as a long inline heredoc (lines 237–524).

`research.py` solves the same problem with a default-allow denylist instead: Squid + a composed denylist (`(cached upstream feeds ∪ additions) − overrides`) + iptables `RESEARCH` chain shaped almost identically to `CLAUDE_AGENT` but with a hashlimit-based rate-limit rule (`render_iptables_apply_script`, `research.py:486–549`). State lives under `~/.research/` with refresh/reload/reseed flags. See ADR-018, ADR-021, ADR-023 for the rationale.

User's goal for this branch: drive `plans/local-research-harness.md` from inside `claude-agent`, with Claude able to exercise SearXNG and follow arbitrary search-result URLs without curating the allowlist for every domain. The cleanest path is to port research.py's denylist firewall onto start-agent.sh while keeping the rest of the script intact (Colima VM, container build, OpenCode wiring, SearXNG sidecar).

Decisions confirmed up front:
- Mirror `research.py` exactly: Squid + composed denylist + rate-limit rule + refresh/reload/reseed CLI flags.
- State lives at `~/.claude-agent/denylist-*` (parallel to `~/.research/denylist-*`, never shared).
- Templates in `templates/` are reused as-is (`research-denylist-sources.txt`, `research-denylist-additions.txt`) — they are general-purpose denylist seed material; nothing in them is research.py-specific.
- This is a branch-only change. Don't merge to main; don't rewrite ADR-010 in place.

## Goals

- `start-agent.sh` (on the new branch) runs Squid in the Colima VM with a composed denylist; tinyproxy is gone from the script.
- Container's `HTTP(S)_PROXY` points at Squid on `$BRIDGE_IP:8888`. From inside the container, a previously-disallowed domain (e.g. `https://example.com`, `https://huggingface.co`) returns 200; a denylisted domain (any entry in the composed denylist) returns 403.
- `--reload-denylist`, `--refresh-denylist`, `--reseed-denylist` flags work the same way they do in `research.py`.
- Legacy `~/.claude-agent/allowlist.txt`, if present, triggers a hard-exit error message that tells the user how to migrate (matching `_check_legacy_allowlist` in `research.py:216`).
- The cross-VM isolation test (`tests/test-cross-vm-isolation.sh`) still passes — claude-agent ↔ research VMs remain mutually unreachable.
- The static "no host port published" check (`tests/test_agent_sh.py`) still passes.

## Approach

Keep the file shape and orchestration of `start-agent.sh`. Replace only the firewall-and-proxy section: tinyproxy install + config + filter generation → Squid install + config + denylist composition; the allowlist heredoc + `generate_filter_file` Python helper get deleted entirely. The iptables `CLAUDE_AGENT` chain logic is already structurally a clone of `RESEARCH` — add the hashlimit probe + rate-limit rule and rename `TINYPROXY_PORT`→`SQUID_PORT` for consistency. CLI flag pairs (`--reload-allowlist`/`--reseed-allowlist`) become (`--reload-denylist`/`--refresh-denylist`/`--reseed-denylist`).

Avoid the temptation to extract a shared library between `start-agent.sh` (bash) and `research.py` (Python). They share design, not code; deduplication would slow this branch's change down with no payoff. Each script keeps its own copies of `compose_denylist`, `denylist_to_squid_acl`, `render_squid_conf`, `render_iptables_apply_script`. In `start-agent.sh`, these become bash + a small inlined `python3 -` block (matching the script's existing style).

Risk: Squid in the VM is heavier than tinyproxy (more deps, longer first-run apt step). Acceptable — research.py already pays this cost.

## Unknowns / To Verify

1. **Squid install footprint inside the existing claude-agent VM.** `research.py` runs in its own VM where Squid is the only egress proxy; in `start-agent.sh`'s VM, tinyproxy is currently installed and presumably listening on 8888. Verify during Phase 2 that `apt-get install -y squid` followed by stopping/disabling tinyproxy doesn't leave both daemons fighting for port 8888. Plan: explicitly `systemctl disable --now tinyproxy 2>/dev/null || true` and `apt-get purge -y tinyproxy` before installing squid.
2. **xt_hashlimit availability in Colima's kernel.** `research.py:vm_has_hashlimit()` probes for it and falls back to `-m limit`. Same probe should work here — verify with a one-shot `colima ssh -p claude-agent -- sudo iptables -m hashlimit --help` before relying on the hashlimit branch.
3. **Whether SearXNG's `outgoing.proxies` setting needs updating.** Currently points at `http://$BRIDGE_IP:$TINYPROXY_PORT` (line 745). After rename → `$BRIDGE_IP:$SQUID_PORT` (same port number, 8888, so settings.yml content is byte-identical). Existing `~/.claude-agent/searxng/settings.yml` files will continue to work; no drift fix needed. Confirm by reading the existing settings.yml after a `--rebuild` run.

---

## Phase 1: branch + denylist state machinery

Establish the branch and add all the host-side denylist helpers without touching the in-VM proxy yet. After this phase, the script still uses tinyproxy; it just *also* knows how to seed/compose denylist files.

### Steps

1. Create and switch to a feature branch:
   ```
   git checkout -b loose-agent-firewall
   ```
   All subsequent commits land on this branch. Don't merge to main.
2. Add new constants near the existing ones (`start-agent.sh:198–214`):
   - `DENYLIST_DIR="$HOME/.claude-agent"` (reuses the existing `ALLOWLIST_DIR`)
   - `DENYLIST_SOURCES_FILE="$DENYLIST_DIR/denylist-sources.txt"`
   - `DENYLIST_ADDITIONS_FILE="$DENYLIST_DIR/denylist-additions.txt"`
   - `DENYLIST_OVERRIDES_FILE="$DENYLIST_DIR/denylist-overrides.txt"`
   - `DENYLIST_CACHE_DIR="$DENYLIST_DIR/denylist-cache"`
   - `SQUID_PORT=8888` (in addition to or replacing `TINYPROXY_PORT`; keep both during the transition)
   - `TEMPLATE_DENYLIST_SOURCES="$(cd "$(dirname "$0")" && pwd)/templates/research-denylist-sources.txt"`
   - `TEMPLATE_DENYLIST_ADDITIONS="$(cd "$(dirname "$0")" && pwd)/templates/research-denylist-additions.txt"`
3. Add a `seed_denylist_files()` bash function that mirrors `research.py:seed_denylist_files`:
   - Creates `DENYLIST_DIR` and `DENYLIST_CACHE_DIR`.
   - Copies the two repo templates into place when missing (or always, when `--reseed-denylist` is set).
   - Creates a stub `denylist-overrides.txt` if missing (with the same comment header research.py writes; never overwritten by `--reseed`).
4. Add `refresh_denylist_cache()` — fetch each non-comment URL in `denylist-sources.txt` to `denylist-cache/<basename>` via `curl -fsSL`. Write to a `.tmp` file then `mv`; on failure, leave the existing cached copy in place and continue. On first-run bootstrap (cache empty), abort the script if any feed fails — same defense as `research.py:refresh_denylist_cache(abort_on_any_failure=True)`.
5. Add a small `python3 -` heredoc helper that takes `cache-dir + additions + overrides` and writes the composed denylist to a host-side temp file. Reuse the parsing logic from `research.py:_read_domain_lines` and `_prune_subdomains` (Squid 6 will reject a dstdomain ACL that lists both `example.com` and `sub.example.com`). Output format: one `.example.com` per line — same as `research.py:denylist_to_squid_acl`.
6. Add `prune_orphan_cache_files()` — delete `*.txt` files in `denylist-cache/` whose basename is no longer derivable from a URL in `denylist-sources.txt`. Mirror `research.py:prune_orphan_cache_files`. Called from refresh and reload paths.
7. Add a legacy guard: if `~/.claude-agent/allowlist.txt` exists when the script starts (and we're not running `--reload-denylist` post-rename), print a migration error matching `research.py:_check_legacy_allowlist` (steps: `rm -rf ~/.claude-agent/allowlist.txt ~/.claude-agent/allowlist*.txt; start-agent.sh --rebuild`) and exit 1.

### Files

- `start-agent.sh` (new constants, new helper functions, legacy guard)

### Acceptance criteria

- `bash -n start-agent.sh` passes.
- Running `./start-agent.sh --rebuild` on a fresh `~/.claude-agent/` (with `~/.claude-agent/allowlist.txt` removed) seeds the three denylist files, populates `denylist-cache/` from the upstream feeds, and prints the composed-entry count.
- Running with the legacy `~/.claude-agent/allowlist.txt` file in place exits 1 with the migration message.

---

## Phase 2: replace tinyproxy with Squid in start-agent.sh

Now do the surgery. After this phase, the script no longer references tinyproxy.

### Steps

1. Delete the inline allowlist heredoc (`start-agent.sh:236–530`) and the `generate_filter_file` Python helper (lines 535–547). The seed step for the allowlist file is also gone — the first-run bootstrap is now `seed_denylist_files()` (Phase 1) plus `refresh_denylist_cache()` if the cache is empty (mirroring `research.py:main` lines 1051–1054).
2. Replace the tinyproxy-install block (lines 751–756) with a Squid install + tinyproxy purge:
   ```
   if vm_ssh dpkg -s tinyproxy >/dev/null 2>&1; then
     vm_ssh sudo systemctl disable --now tinyproxy 2>/dev/null || true
     vm_ssh sudo apt-get purge -y tinyproxy
   fi
   if ! vm_ssh sh -c 'command -v squid' >/dev/null 2>&1; then
     vm_ssh sudo apt-get update -qq
     vm_ssh sudo apt-get install -y squid
     vm_ssh sudo systemctl stop squid 2>/dev/null || true
   fi
   ```
3. Replace the `tinyproxy.conf` heredoc (lines 762–779) with a Squid config block matching `research.py:render_squid_conf`:
   ```
   http_port $BRIDGE_IP:$SQUID_PORT
   visible_hostname claude-agent-squid
   acl denylist dstdomain "/etc/squid/denylist.txt"
   acl CONNECT method CONNECT
   acl SSL_ports port 443
   acl Safe_ports port 80 443
   http_access deny denylist
   http_access deny CONNECT !SSL_ports
   http_access deny !Safe_ports
   http_access allow all
   access_log /var/log/squid/access.log
   cache deny all
   ```
4. Replace the filter-generation step with the composed-denylist generation (call the Phase 1 `python3 -` helper); push both `squid.conf` and `denylist.txt` into the VM via `vm_put_file` to `/etc/squid/squid.conf` and `/etc/squid/denylist.txt`.
5. Replace the tinyproxy `systemctl` reload/restart block (lines 787–794) with a Squid equivalent. On reload-fast-path use `sudo squid -k reconfigure 2>/dev/null || sudo systemctl restart squid`; on first-up use `sudo systemctl enable --now squid` then `sudo systemctl restart squid`. Also surface the journal tail on failure (mirror `research.py:apply_firewall` lines 819–830).
6. Update the iptables block (`firewall-apply.sh` heredoc, lines 800–846):
   - Rename `TINYPROXY_PORT` → `SQUID_PORT` throughout. Variable substitution into the heredoc stays correct since both names just hold `8888`.
   - Add the rate-limit rule from `research.py:render_iptables_apply_script` lines 503–518. Probe for `xt_hashlimit` once at the top of the firewall block (a small bash `vm_has_hashlimit()` mirroring `research.py:vm_has_hashlimit`) and emit the appropriate variant (hashlimit per-srcip vs. plain `-m limit`).
   - Comments in the chain: replace any "tinyproxy" mentions with "squid".
7. Update the bare-script comments at the file head (lines 7–8) and the `usage()` text (lines 52–79) to describe the denylist model. Match the wording of `research.py`'s argparse epilog (research.py:130–146) — "default-allow denylist; the composed denylist is (cached upstream feeds ∪ additions) − overrides; all three files in `~/.claude-agent/`."
8. Update the final "==> Creating container" log line (line 1281) from `(allowlist: ...)` to `(denylist: $DENYLIST_SOURCES_FILE)`.

### Files

- `start-agent.sh` (the firewall section between roughly lines 200 and 850, plus the head comment + usage block)

### Acceptance criteria

- After `./start-agent.sh --rebuild`, `colima ssh -p claude-agent -- systemctl is-active squid` returns `active`; `which tinyproxy` returns nothing in the VM.
- From inside the resulting container: `curl -sI https://example.com -o /dev/null -w '%{http_code}\n'` returns `200`. `curl -sI https://<some-domain-listed-in-the-denylist> -o /dev/null -w '%{http_code}\n'` returns `403`.
- `colima ssh -p claude-agent -- sudo iptables -L CLAUDE_AGENT -v -n` shows the rate-limit rule near the bottom of the chain (just before the final REJECT).

---

## Phase 3: rewire CLI flags, legacy guard, container env

Update the user-facing surface to match `research.py` and prove the fast paths work.

### Steps

1. Replace flag parsing for `--reload-allowlist` / `--reseed-allowlist` (lines 101–102) with three new flags matching `research.py`:
   - `--reload-denylist` → recompose from local files, push ACL, `squid -k reconfigure`. No network.
   - `--refresh-denylist` → re-fetch upstream feeds, then implies `--reload-denylist`.
   - `--reseed-denylist` → overwrite `denylist-sources.txt` and `denylist-additions.txt` from the repo templates; `denylist-overrides.txt` is never overwritten.
   Set internal flags: `RELOAD_DENYLIST`, `REFRESH_DENYLIST`, `RESEED_DENYLIST`. Removing the old `RELOAD_ALLOWLIST`/`RESEED_ALLOWLIST` variables and their fast-path block (lines 851–858) is required — the new fast-path is "Phase 1's compose + Phase 2's `squid -k reconfigure`".
2. Update the `usage()` text (lines 38–95) and the help text in argparse-style block to document the new flags. Match `research.py`'s help language for consistency.
3. Container env in `DOCKER_ENV_ARGS` (lines 1169–1182) — leave `HTTP_PROXY`/`HTTPS_PROXY` as-is. They already point at `$BRIDGE_IP:$TINYPROXY_PORT`, which is the same port number Squid now listens on. Just rename the variable in-script for readability:
   ```
   -e "HTTPS_PROXY=http://$BRIDGE_IP:$SQUID_PORT"
   -e "HTTP_PROXY=http://$BRIDGE_IP:$SQUID_PORT"
   ```
   `NO_PROXY` is unchanged.
4. SearXNG `settings.yml` seed block (lines 715–749) — change the literal `$TINYPROXY_PORT` to `$SQUID_PORT`. Existing seeded files keep working since the integer is the same; no drift fix needed.
5. Comments scattered through the file that mention tinyproxy or "allowlist" — sweep and replace with squid / denylist. Notable: `start-agent.sh:7`, `:11`, `:74-79`, `:534`, `:698-699`, `:751`, `:824`, `:841`, `:851`, `:894-900`, `:1199`, `:1281`. Don't get sucked into rewriting ADR-010 — that's a main-branch concern.

### Files

- `start-agent.sh`

### Acceptance criteria

- `./start-agent.sh --reload-denylist` from a state where `denylist-additions.txt` was edited:
  - takes <5s,
  - prints "Denylist reloaded (N entries)",
  - does not restart the container, and
  - the new entry is enforced (`curl -I` returns 403 for the newly-added domain inside the container without re-attaching).
- `./start-agent.sh --refresh-denylist` re-fetches upstream feeds (visible by mtime change in `~/.claude-agent/denylist-cache/`) and reloads.
- `./start-agent.sh --reseed-denylist` overwrites the two seed files but leaves `denylist-overrides.txt` alone.
- `./start-agent.sh --reload-allowlist` (the old flag) is rejected by `getopts`-style fall-through and the user gets the usage block. (Acceptable to leave as a no-op deprecation warning if simpler — call your shot.)

---

## Phase 4: smoke-verify the loosened path

No new tests to write; just exercise the changed surface to make sure the loose path holds and the existing tests still mean what they say.

### Steps

1. Run `python3 tests/test_agent_sh.py` — must still pass (no `docker run -p` should have been added). This is a static guard.
2. Run `tests/test-cross-vm-isolation.sh` (with both VMs up). Cross-VM isolation should be unaffected — neither the firewall direction nor the bridge isolation has changed.
3. Manual end-to-end inside the container:
   - `curl -sI https://example.com -w '%{http_code}\n' -o /dev/null` → `200`
   - `curl -sI https://github.com -w '%{http_code}\n' -o /dev/null` → `200` (was previously denied by the omit-write-hosts policy in the allowlist; the denylist treats it as allowed, which is the whole point of the loosening)
   - Add `evilcorp.example` to `~/.claude-agent/denylist-additions.txt`, run `./start-agent.sh --reload-denylist`, then from inside the container `curl -sI https://evilcorp.example` → `403`.
   - From inside the container, `curl -s "http://searxng:8080/search?q=test&format=json"` returns a JSON results envelope.
4. Sanity-check that `tests/test-agent-firewall.sh` is now broken or stale — it tests allowlist behavior and will not pass against a denylist. Document this in a one-liner comment at the top of the file (`# NOTE: this branch (loose-agent-firewall) loosens the firewall to a denylist; this script tests the legacy allowlist behavior and will fail.`) rather than rewriting it. The user has signaled this branch is not for merging.

### Acceptance criteria

- Manual tests above all pass.
- `test_agent_sh.py` and `test-cross-vm-isolation.sh` still pass.
- `test-agent-firewall.sh` is annotated, not deleted.

---

## Notes

**Templates are shared with research.py.** Both scripts now read from `templates/research-denylist-sources.txt` and `templates/research-denylist-additions.txt`. If the user later wants the two scripts to evolve different denylists (e.g., the agent should permit `github.com` writes while research never should), split the templates at that point — don't preempt the divergence.

**Why not preserve a two-tier mode?** Tempting to keep the allowlist code under a `--strict` flag and add the denylist as `--loose`. Don't. The whole point of the branch is the loose mode; carrying both inflates the script with no real user-visible benefit, and the allowlist heredoc + filter generator account for nearly 300 lines of code that the loosening removes.

**ADR drift is acknowledged.** ADR-010 documents the default-deny allowlist as the security boundary. This branch contradicts ADR-010. Don't rewrite it on this branch — if and when this work is promoted toward main, write a follow-up ADR (something like ADR-XXX: "claude-agent egress firewall: allowlist → denylist"). Same goes for the `start-agent.sh key decisions` block in CLAUDE.md.

**`tests/test-agent-firewall.sh` is left as-is, annotated.** A full rewrite to match denylist semantics is real work (the existing test exercises six "should-be-denied" cases per `README.md`). On a non-merging branch, that effort is wasted. If the loosening graduates toward main, rewrite the test alongside the ADR.

**Cross-VM isolation is preserved by accident.** The CLAUDE_AGENT and RESEARCH iptables chains are independent; loosening one doesn't affect the other. `tests/test-cross-vm-isolation.sh` will keep working as the regression guard against future changes that *do* couple them.

**Out of scope on this branch:**
- Sharing `~/.research/` denylist state across both scripts.
- Refactoring out a shared denylist library between bash and Python.
- Updating `README.md` and CLAUDE.md key-decisions; these stay correct for `main`.
- Rewriting `tests/test-agent-firewall.sh`.
