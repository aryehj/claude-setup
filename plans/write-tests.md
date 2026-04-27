# Test Suite for start-claude / start-agent / research

## Status

- [x] Phase 1: In-container firewall smoke tests (5 of 6 README cases, plus inter-container port isolation)
- [x] Phase 2: Pure-helper unit tests for `research.py` (denylist composition, Squid ACL, iptables script rendering)
- [x] Phase 3: Cross-VM isolation test (claude-agent ↔ research)
- [ ] Phase 4: Allowlist hot-reload test (the 6th README case)
- [ ] Phase 5: Lifecycle + idempotency tests
- [ ] Phase 6: Settings/config-file injection tests for `start-agent.sh`
- [ ] Phase 7: Single entry point + CI hookup

## Context

Test infrastructure already exists; this plan is about closing remaining gaps, not building from scratch. What's in `tests/` today:

- `test-agent-firewall.sh` — runs inside `claude-agent`, asserts default-deny, allow-via-proxy, deny-via-proxy, Ollama/omlx carve-out, env wiring, and inter-container port isolation. Skips the README's 6th case (host-side allowlist hot-reload).
- `test_research.py` — pytest unit tests for `research.py`'s pure helpers: `compose_denylist`, `denylist_to_squid_acl`, `_prune_subdomains`, `prune_orphan_cache_files`, `render_searxng_settings`, `render_iptables_apply_script`.
- `test_agent_sh.py` — static check that no `docker run` in `start-agent.sh` publishes a host port.
- `probe-denylist.sh` — host-driven probe of Squid's denylist with allow/deny URLs.
- `probe-vane-egress.sh` — verifies the running `research-vane` has the right `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` env vars and that a sidecar HTTPS round-trip through Squid succeeds.
- `walkback-checks.py` — historic debug-saga verifier; not a steady-state test.
- `vane-eval/` — research-quality eval harness with its own pytest suite (separate concern from infra correctness).

The remaining gaps are: cross-VM isolation, allowlist hot-reload, lifecycle/idempotency, settings-file injection in `start-agent.sh`, and a single entry point. Everything else is either done or out of scope (see Notes).

## Goals

- Catch regressions in load-bearing invariants the existing tests don't cover: cross-VM isolation, hot-reload effect, idempotency on re-run, settings-merge correctness.
- Runnable locally on a developer Mac with no setup beyond what the scripts already require.
- Tests safe to run alongside an active dev environment — they must not stomp the user's `~/.claude-agent/` or `~/.research/` state, and must restore any state they mutate.
- A single entry point that can run all phases; individual scripts still runnable in isolation.

## Resolved unknowns

- **bats vs. plain bash vs. Python.** Already settled by the existing tests: bash for in-VM/in-container probes, pytest for pure-Python helpers. Continue this split — don't introduce a third framework.
- **CI.** GitHub Actions macOS runners can't nest-virtualize Colima reliably and boot is multi-minute per VM. Treat CI integration as out of scope until/unless we find a path; tests are local-developer-runnable today.

---

## Phase 3: Cross-VM isolation test (claude-agent ↔ research)

The two-Colima-profile architecture (ADR-018, ADR-019, plans/implemented - research-vm-isolation.md) rests on the assumption that `claude-agent` and `research` cannot reach each other. This is enforced today by VM separation + iptables chains + no shared docker network. A future change ("let the agent reach localhost:3000 for some new service") could quietly break it.

**What to assert:**

- From inside `claude-agent`, the `research-searxng` and `research-vane` containers are unreachable: by container name (DNS), by `host.docker.internal:3000` (Vane's host bind), and by direct bridge IP if discoverable.
- From inside `research-searxng` (it has a shell; Vane does not), `claude-agent` and `searxng` (the agent's) are unreachable by name and by any host port the agent might have bound. Today the agent binds nothing — `test_agent_sh.py` enforces that — so a regression in either direction trips this test.
- Both VMs reach the macOS host's inference endpoint (`OLLAMA_HOST` / `OMLX_HOST`). This is intentional shared-host access; assert it still works to confirm the negative tests above aren't over-blocking.

**Constraints:**

- Skip cleanly if either VM isn't running (clear message, non-fail). Don't auto-start them.
- Print enough diagnostics on failure that "claude-agent could reach vane" is unambiguous — this is a security-relevant assertion.

**Files:** new test script under `tests/` (bash, host-driven via `colima ssh -p {profile}` and `docker exec`).

---

## Phase 4: Allowlist hot-reload test

The 6th README smoke test — mutate `~/.claude-agent/allowlist.txt`, run `start-agent.sh --reload-allowlist`, observe that previously-blocked hosts now resolve through tinyproxy without restarting the container — is the only one not yet automated. It's host-driven, which is why `test-agent-firewall.sh` skips it.

**What to assert:**

- Pick a host not in the default allowlist; confirm tinyproxy denies it.
- Add the host, run `--reload-allowlist`, confirm it now passes.
- Remove the host, run `--reload-allowlist`, confirm it's denied again.
- Container PID is unchanged across the reload (no restart).

**Constraints (sensitive):**

- The test mutates a file the user owns. Wrap the body in a trap that restores the original `allowlist.txt` byte-for-byte on any exit path, including SIGINT. Take the snapshot before the first mutation.
- Skip if `claude-agent` isn't running.

**Files:** likely fits as a sibling to `test-agent-firewall.sh` rather than inside it (different driving environment — host vs. in-container).

---

## Phase 5: Lifecycle + idempotency tests

Catches first-run bugs and second-run regressions. Less urgent than Phase 3/4 because `--rebuild` is the user-visible recovery, but bugs here cost the most onboarding time.

**What to assert:**

- Re-running `start-agent.sh` against an existing setup is fast (re-attach, not rebuild) and produces a working container.
- `start-agent.sh --reload-allowlist` updates the in-VM tinyproxy filter and SIGHUPs without restarting the container (overlaps Phase 4 — share the assertion).
- `start-agent.sh --rebuild` removes the image and container, then recreates them, while leaving `~/.claude-agent/` data dirs (allowlist, opencode-config, opencode-data, searxng) intact.
- Same for `research.py` and `research.py --rebuild`.
- Cold start from no Colima profile produces a working container. Slow (~5 min); the only way to catch first-run bugs.

**Constraints (sensitive):**

- Cold-start tests must not destroy a user's existing profile. Either gate behind a flag the user opts into, or run against an isolated profile name. Do not silently `colima delete claude-agent` from a test.
- `--rebuild` is destructive of the named container/image but not user data; document this and skip if the user hasn't opted in.

**Files:** one or more new scripts under `tests/`.

---

## Phase 6: Settings/config-file injection tests for `start-agent.sh`

`start-agent.sh` contains Python heredocs that merge JSON into `~/.claude/settings.json`, `~/.claude/settings.local.json`, and `opencode.json`. These have edge cases (existing keys, missing files, malformed JSON) and are currently only exercised end-to-end by running the whole script.

**What to assert (per merge target):**

- File missing → file created with required keys.
- File present, no conflict → required keys added, user keys preserved.
- File present, conflicting key → script's chosen resolution (whatever it is — pin behavior in the test, don't legislate it here).
- File present, malformed JSON → script errors clearly; does not silently overwrite.

**Implementation note (sensitive):**

- These are easiest to test if the inline Python is extracted to a standalone script invoked by `start-agent.sh`. That refactor is in scope here — the testability win is the reason. Don't try to pytest a heredoc.

**Files:** new standalone injection script(s) (location/name implementor's call) plus pytest cases.

---

## Phase 7: Single entry point + CI hookup

A single runner that invokes everything, prints PASS/FAIL/SKIP totals, exits non-zero on any fail. Each phase remains independently runnable.

CI hookup is deferred until the local story is solid. When tackled, scope it to the parts that don't require a Colima VM (pytest suites, static checks, `bash -n` syntax checks). Anything VM-bound stays local-only unless a working runner pattern emerges.

---

## Notes

- **No mocking of the firewall/proxy/iptables surface.** A mocked test of "we called the right `iptables` command" verifies almost nothing — the real bugs live in how rules interact, not in command issuance. Keep these as integration probes against real Colima.
- **Tests are slow on purpose.** A correct firewall test waits for a curl timeout. A correct isolation test crosses two VMs. Optimize for "did we catch the regression," not for runtime.
- **Out of scope:** Claude Code / OpenCode UI behavior, model output quality (covered by `vane-eval/`), `start-claude.sh` parity (Apple Containers; gets its own smaller suite if/when it grows enough surface area to warrant one).
- **`walkback-checks.py` and the probe scripts are not steady-state tests.** They're investigative; leave them alone but don't wire them into the runner.
- **Ordering rationale:** Phase 3 (cross-VM isolation) is the highest-value remaining work — it's the one architectural invariant nothing currently asserts. Phase 4 is small and finishes the README parity story. Phases 5-6 are quality-of-life; do them when something breaks that they would have caught.
