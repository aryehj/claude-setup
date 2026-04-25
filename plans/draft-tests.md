# Test Suite for start-claude / start-agent / research

## Status

- [ ] Phase 1: Automate the README's six firewall smoke tests
- [ ] Phase 2: Add cross-VM isolation test (claude-agent ↔ research)
- [ ] Phase 3: Add lifecycle + idempotency tests
- [ ] Phase 4: Add settings-injection tests
- [ ] Phase 5: Wire into a single entry point (`tests/run-all.sh` or equivalent)

## Context

There is no automated test suite today. The closest things to tests are:

- `README.md:311-370` — six manual `curl` commands for the egress allowlist
- `start-agent.sh:175-200` — preflight checks (colima/docker installed, etc.)
- `--rebuild` — operational reset, not a test

This works while the surface is small and one person maintains it. It will not scale to three scripts (start-claude, start-agent, research) with cross-cutting invariants like "claude-agent and research must not be able to reach each other."

The project does not need a unit-test framework. It needs **integration smoke tests**: scripts that bring up real Colima VMs, run real `docker run` commands, and assert observable behavior. Slow (minutes), occasionally flaky (Colima boot times), but correct — and that's the right trade for a tool whose entire job is shell-out orchestration.

## Goals

- Catch regressions in the load-bearing invariants: egress firewall, cross-VM isolation, idempotency, settings-merge correctness
- Runnable locally on a developer's Mac with no setup beyond what the scripts already require (Colima + docker, or Apple Containers)
- Single entry point that runs everything; individual tests runnable in isolation
- Output that's diff-able in CI (line-oriented PASS/FAIL with the failing assertion's expected/actual)
- Tests must be safe to run alongside an existing dev environment — they create their own VMs/containers under distinct names and clean up after themselves

## Unknowns / To Verify

1. **bats vs. plain bash vs. Python.** `bats-core` gives nicer assertions and per-test isolation but adds a dep. Plain bash + `set -e` + a tiny `assert_eq` helper covers ~80% of needs with zero deps. If `research.py` lands as Python (per `plans/draft-research-python.md`), `pytest` becomes a third option. **Recommendation**: start with plain bash; revisit if assertion bookkeeping gets painful.
2. **Where to run in CI.** GitHub Actions macOS runners can install Colima but boot is slow (~3-5 min per VM) and the runner may not nest-virtualize cleanly. **Recommendation**: tests run locally on developer machines first; CI integration is a follow-up after Phase 2.
3. **How to share fixtures between Phase 1 (claude-agent) and Phase 2 (research) tests.** Both need a Colima VM up and a known container running. Sharing a setup helper that brings up either profile on demand keeps tests independent.

---

## Phase 1: Automate the firewall smoke tests

The six tests in `README.md:311-370` are the most load-bearing assertions in the project — they verify ADR-010's egress policy. Today they are copy-paste-into-shell. Automating them is the highest-value first step.

### Steps

1. Create `tests/firewall.sh` that:
   - Asserts `start-agent.sh` has been run and `claude-agent` container is up (skip with clear message if not, rather than failing)
   - Runs each of the six README tests via `docker exec claude-agent <cmd>`
   - For each test, asserts the expected outcome (connection refused, 403, 200, JSON shape, env vars present)
2. Helper `tests/lib/assert.sh` provides:
   - `assert_eq EXPECTED ACTUAL MSG`
   - `assert_contains HAYSTACK NEEDLE MSG`
   - `assert_command_fails CMD MSG` (non-zero exit)
   - `assert_command_succeeds CMD MSG`
3. Hot-reload test (#6) needs to mutate `~/.claude-agent/allowlist.txt`, run `start-agent.sh --reload-allowlist`, assert effect, then revert. Wrap in a trap so a failed assertion still restores the allowlist.

### Files

- `tests/firewall.sh` (new, ~100 lines)
- `tests/lib/assert.sh` (new, ~40 lines)

### Why this first

Six tests, well-scoped, already documented as smoke tests, exercise the firewall path that the project's security story rests on. If anything regresses here, several ADRs are simultaneously broken.

---

## Phase 2: Cross-VM isolation test (claude-agent ↔ research)

Once `research.sh` lands, the two-VM design becomes load-bearing for the "claude-agent cannot talk to vane" invariant. The architecture (separate Colima profiles, separate iptables tables) enforces this for free *today* — but a future change ("let claude-agent reach the Mac's localhost:1234 for some new service") could quietly open a path. A test that runs after every `--rebuild` of either script catches that regression.

### Steps

1. `tests/isolation.sh` brings up both profiles (or asserts both are running) and verifies bidirectional unreachability.
2. **Direction A: claude-agent → vane.** From inside `claude-agent`:
   - Try `curl --noproxy '*' --max-time 3 http://host.docker.internal:3000` (vane's host port). Expected: connection refused or timeout — iptables CLAUDE_AGENT chain has no RETURN for `HOST_IP:3000`.
   - Try `curl --max-time 3 http://host.docker.internal:3000` (via tinyproxy). Expected: 403 (host.docker.internal not in allowlist).
   - Try `curl --max-time 3 http://research-searxng:8080` and `http://research-vane:3000`. Expected: DNS resolution failure (research-net is in a different VM, no DNS visibility).
3. **Direction B: vane → claude-agent.** Vane has no shell, but `research-searxng` does. From `docker exec research-searxng`:
   - Try to reach `claude-agent` by container name → DNS failure expected.
   - Try `host.docker.internal:<any-port-claude-agent-might-bind>` → claude-agent binds no host ports today, so connection refused. If a future change adds a host port to claude-agent, this test starts failing — exactly the regression we want to catch.
4. **Direction C: shared host services.** Both VMs reach the macOS host's Ollama/omlx port — that's intentional. Assert this still works to confirm the test isn't over-blocking.

### Files

- `tests/isolation.sh` (new, ~80 lines)

### Notes

- This is the test that justifies the two-VM architecture. If it ever passes when it should fail (or vice versa), the architectural assumption is invalid and needs re-examination.
- Include a `--verbose` flag that prints the expected-vs-actual for each direction. The failure mode "claude-agent could reach vane" has security implications; users should be able to read exactly what was tested.

---

## Phase 3: Lifecycle + idempotency tests

### Steps

1. `tests/idempotency.sh`:
   - Run `start-agent.sh` twice in succession; second run should be fast (re-attach, not rebuild) and produce a working container.
   - Run `start-agent.sh --reload-allowlist` while the container is running; assert tinyproxy filter file inside the VM was updated and tinyproxy was SIGHUP'd (no container restart).
   - Run `start-agent.sh --rebuild`; assert image and container are gone before recreate, and that `~/.claude-agent/` data dirs (allowlist, vane-data, searxng) survive.
2. `tests/lifecycle.sh`:
   - Fresh state (no Colima profile, no images, no `~/.claude-agent/`) → `start-agent.sh` completes successfully and produces a working container.
   - This is the "cold start" test. Slow (~5 min) but the only way to catch first-run bugs.

### Files

- `tests/idempotency.sh` (new, ~60 lines)
- `tests/lifecycle.sh` (new, ~40 lines)

### Caveats

- Cold-start tests will trash any existing Colima profile of the same name. Run with `CLAUDE_AGENT_PROFILE=test-claude-agent` env var override (requires the script to honor this; check before relying on it).

---

## Phase 4: Settings-injection tests

The scripts merge JSON into `~/.claude/settings.json`, `~/.claude/settings.local.json`, and `opencode.json`. These merges have edge cases (existing files with conflicting keys, missing files, malformed JSON). The current `start-agent.sh` includes Python heredocs to handle these — testable in isolation.

### Steps

1. `tests/settings-merge.sh`:
   - Set up a temp `$HOME` with a known-good settings file containing some user keys.
   - Run the relevant injection block (extracted from `start-agent.sh` if needed).
   - Assert: required keys are present, user keys are preserved, no malformed JSON.
2. Test cases:
   - File missing → file created with required keys
   - File present, no conflicts → required keys added, user keys untouched
   - File present, key conflict → expected resolution (e.g., script's value wins, or merge)
   - File present, malformed JSON → script errors clearly (does not silently overwrite)

### Files

- `tests/settings-merge.sh` (new, ~80 lines)

### Notes

- This is the place where extracting the inline Python from `start-agent.sh` into a standalone `scripts/inject-settings.py` would pay off — it becomes directly testable instead of needing a full container run.

---

## Phase 5: Single entry point

### Steps

1. `tests/run-all.sh`:
   - Sources `lib/assert.sh`
   - Runs each phase's tests in order, with a `--phase=N` filter
   - Prints a summary at the end: `PASS: 14, FAIL: 0` (or which tests failed, with their assertion output)
   - Exit code 0 on all-pass, 1 on any-fail
2. Document in `README.md` how to run the suite: `./tests/run-all.sh` or `./tests/firewall.sh` for a single phase.

### Files

- `tests/run-all.sh` (new, ~50 lines)
- `README.md` (modify — add a "Running the test suite" section pointing here)

---

## Notes

- **No mocking.** The whole point is to exercise real Colima + real docker + real iptables. A mocked test of "did we call the right `iptables` command" verifies almost nothing — the real bugs are in how the rules interact, not in whether the command was issued.
- **Tests are slow on purpose.** A correct firewall test takes 3-5 seconds (curl with `--max-time`). A correct isolation test requires both VMs running. Don't optimize for speed; optimize for "did we catch the regression."
- **What's intentionally out of scope:** UI / TUI testing of Claude Code itself, OpenCode behavior, model output quality, Apple Containers parity (`start-claude.sh` gets its own smaller suite later if needed). This plan focuses on the host-side orchestration and firewall — the parts this repo owns end-to-end.
- **Relationship to `plans/draft-research-python.md`:** if `research.sh` becomes Python, the Python parts (allowlist regex generation, settings.yml templating) become unit-testable in pytest, distinct from the integration tests above. That's additive, not a replacement.
- **Ordering.** Phase 1 is the highest-value-per-line-of-test-code. Phase 2 is the highest-value-per-architectural-decision-protected. Phases 3-5 are quality-of-life — write them when something breaks that they would have caught.
