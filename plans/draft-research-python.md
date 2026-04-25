# research.sh as Python (research.py)

## Status

- [x] Phase 1: Single-file `research.py`, stdlib-only, feature-parity with the bash plan
- [x] Phase 2: Decide whether the result is good enough to port `start-agent.sh` later — see ADR-018

## Context

`plans/research-vm-isolation.md` proposes ~400-500 lines of new bash that largely mirrors `start-agent.sh`'s tinyproxy + iptables + Colima orchestration. Two questions arose during plan review:

1. Would shared abstractions reduce that duplication? → Bash libraries are awkward; targeted helpers cap out around 80 lines and don't justify the indirection cost.
2. Would moving to Python make this easier to reason about? → Probably yes, *and* the existing codebase is already showing bash-friction tells (`python3 -c` heredocs for JSON manipulation, `awk` for YAML drift detection, doubly-escaped variables in `colima ssh` heredocs).

**This plan proposes writing `research.py` from the start as a probe.** It is the smallest of the three host-side scripts, has no migration cost (greenfield), and answers the language question with evidence rather than speculation. If the result is clearly cleaner than the bash equivalent, that becomes the case for porting `start-agent.sh` later. If not, the lesson is cheap.

This plan does **not** propose porting `start-agent.sh` or `start-claude.sh`. Those are separate decisions that depend on Phase 2's evaluation.

## Goals

- `research.py` achieves feature parity with the `research.sh` proposal in `plans/research-vm-isolation.md`
- Stdlib-only — no `pip install`, no `requirements.txt`, no virtualenv
- Single file, runnable as `./research.py` via shebang
- Top-to-bottom readable: someone unfamiliar with the project can read it linearly and understand what it does
- Honest comparison: at the end of Phase 1, `research.py` and a hypothetical `research.sh` should be diff-able for length, readability, and bug-density

## Unknowns / To Verify

1. **Python version on macOS.** `/usr/bin/python3` ships with Xcode CLT (3.9+ as of recent macOS). Confirm minimum target is 3.9 — affects whether `match` statements (3.10+) and PEP 604 union syntax (3.10+) are usable. **Recommendation**: target 3.9 as floor; use `Optional[X]` over `X | None`.
2. **Are there platform-specific subprocess gotchas with Colima?** `start-agent.sh` uses `colima ssh -p PROFILE -- sh -c '...'` extensively. Python's `subprocess.run([...], capture_output=True)` should handle quoting more cleanly than bash's nested heredocs, but verify with a quick test of `vm_ssh("sudo iptables -F CLAUDE_AGENT")` before committing.
3. **JSON/YAML handling.** opencode.json is JSON (stdlib `json` covers it). settings.yml is YAML (no stdlib YAML support). **Decision needed**: either (a) keep settings.yml as a string template (same as bash today), (b) take a dependency on PyYAML (breaks stdlib-only goal), or (c) write a minimal hand-rolled parser for the narrow shape we need. **Recommendation**: option (a) — string template, same as bash. The schema is shallow and we already write it whole.

---

## Phase 1: Build research.py, stdlib-only

### File layout

A single file: `research.py` at the repo root, ~400-500 lines, organized top-to-bottom as:

```
1. Shebang + module docstring
2. Imports (stdlib only)
3. Dataclasses for config (CLI args, derived state)
4. Pure helpers (allowlist → regex, settings.yml template, iptables chain template)
5. Subprocess wrappers (vm_ssh, vm_put_file, docker_run)
6. Phase functions (one per logical step in the bash plan)
7. main() — argparse → dispatch
8. if __name__ == "__main__": main()
```

This mirrors how a competent reader would write the bash version, but with real data structures and real arg parsing.

### Step-by-step structure

1. **CLI parsing (`argparse`)**:
   - Replaces ~30 lines of bash case-statement with ~15 lines of argparse declaration
   - `--rebuild`, `--reload-allowlist`, `--backend={ollama,omlx}`, `--memory=`, `--cpus=`, `--port=` (the one called out in critique #7 of the bash plan review)
   - Help text generated for free; bash hand-maintains it in two places

2. **Constants and paths (`@dataclass`)**:
   - `class Paths`: `allowlist_file`, `searxng_settings`, `vane_data_dir`, `mcp_dir`
   - `class VmConfig`: `profile_name`, `memory_gib`, `cpus`, `bridge_ip`, `host_ip`, `bridge_cidr`, `research_net_cidr`
   - Centralizes what bash scatters across 30 lines of `VAR=value`

3. **Pure helpers** (testable in isolation):
   - `def allowlist_to_regex_filter(lines: list[str]) -> str`: takes allowlist lines, returns the tinyproxy filter file body. Anchors patterns properly (addressing critique #2 of the bash plan review).
   - `def render_searxng_settings(bridge_ip: str, tinyproxy_port: int, secret: str) -> str`: returns the settings.yml body as a string.
   - `def render_iptables_apply_script(config: VmConfig, allow_intra_8080: bool) -> str`: returns the firewall-apply.sh body. **Critically**: this is a Python f-string templating a shell script, NOT a Python heredoc-inside-a-bash-heredoc. The variable interpolation happens at template-render time, so the resulting shell script has no `\$VAR` escaping fragility.

4. **Subprocess wrappers**:
   - `def vm_ssh(cmd: str, *, profile: str, check: bool = True) -> CompletedProcess`: wraps `subprocess.run(["colima", "ssh", "-p", profile, "--", "sh", "-c", cmd], ...)`. Handles the quoting that bash's `colima ssh` calls do by hand.
   - `def vm_put_file(local_path: Path, remote_path: str, *, profile: str)`: replaces the bash `vm_put_file` helper.
   - `def docker_run(name: str, image: str, **kwargs)`: thin wrapper over `subprocess.run(["docker", "run", "-d", ...])`. kwargs map to `-e`, `-v`, `-p`, `--network`, `--add-host`.

5. **Phase functions** (one per logical step from the bash plan):
   - `def ensure_colima_vm(config: VmConfig)`: profile create + start
   - `def discover_network(config: VmConfig) -> VmConfig`: bridge IP, host IP, CIDRs (returns updated config)
   - `def install_tinyproxy(config: VmConfig)`: idempotent apt-get install via vm_ssh
   - `def apply_firewall(config: VmConfig, allowlist: list[str])`: render + push + apply
   - `def ensure_docker_network(name: str)`
   - `def ensure_searxng_container(...)`, `def ensure_vane_container(...)`
   - `def reload_allowlist_fast_path(...)`: the SIGHUP-only path
   - `def rebuild_teardown(config: VmConfig, *, also_vm: bool)`

6. **main() dispatch**: 5-10 lines. Argparse → choose the right top-level path (`--reload-allowlist` early-exit, `--rebuild` teardown then continue, default = full bring-up).

### What Python gives that bash doesn't

- **Real arg parsing.** `argparse` generates `--help`, validates types, rejects unknown flags. Bash hand-rolls all of this and the help text drifts.
- **Real data structures.** `VmConfig` is one source of truth; bash has `BRIDGE_IP`, `HOST_IP`, `BRIDGE_CIDR`, `AGENT_NET_CIDR` as scattered globals.
- **No nested-heredoc escaping.** `render_iptables_apply_script(config)` returns a string with all variables already interpolated; the shell script written to disk has no `\$VAR` escaping. This is the single biggest reduction in subtle-bug surface.
- **Testable pure helpers.** `allowlist_to_regex_filter()`, `render_searxng_settings()`, etc. become unit-testable with `pytest tests/test_research.py`. Bash has no equivalent — the regex generator can only be tested by running the whole script.
- **Stronger error messages.** `subprocess.run(check=True)` raises `CalledProcessError` with the failed command and stderr. Bash's `set -e` aborts with no context.
- **Type hints + a linter.** `mypy research.py` catches "I forgot to update this in the second place" bugs that bash leaves to runtime. (Optional — not load-bearing for the script to work.)

### What Python *doesn't* fix

- This is still mostly a script that shells out to colima/docker/iptables. The orchestration logic doesn't get fundamentally simpler — it gets cleaner ergonomics around the orchestration.
- Greenfield Python is its own bug surface. The bash version benefits from being copy-paste-modify of an already-working script.
- Users editing `research.py` need basic Python comfort. The audience for this repo is technical, but it's still a wider ask than "edit the bash script."
- "Edit the script and re-run" remains literal — Python's edit-run loop is ~as fast as bash. No build step needed.

### Files

- `research.py` (new, ~400-500 lines, stdlib-only, executable via shebang)
- `~/.research/allowlist.txt`, `~/.research/searxng/settings.yml`, `~/.research/vane-data/` (host-side state, identical to bash plan)

### Testing

Mirrors `plans/research-vm-isolation.md` Phase 1 testing, plus:

1. **Pure-helper unit tests** (`tests/test_research.py`, ~50 lines):
   - `allowlist_to_regex_filter` produces anchored regex for known-good inputs
   - `render_searxng_settings` produces parseable YAML (use `yaml.safe_load` if available, else regex spot-check)
   - `render_iptables_apply_script` produces a shell script with no unescaped `${...}` left over
2. **Integration tests** (manual on first run, automated per `plans/draft-tests.md` later):
   - Cold start: `./research.py` from fresh state → Vane reachable at `localhost:3000`
   - `--reload-allowlist` updates filter without restarting containers
   - `--rebuild` recreates containers, optionally tears down VM with confirmation

---

## Phase 2: Evaluate and decide

After `research.py` is working, do a side-by-side comparison against what the bash equivalent would have looked like (use the existing `plans/research-vm-isolation.md` line estimates as a stand-in).

### Evaluation criteria

1. **Line count** (lower is not necessarily better, but a 2x bash-vs-Python ratio in either direction is signal).
2. **Readability**: can a new contributor understand `research.py` faster than they could understand `start-agent.sh`? Ask one.
3. **Bug-density during development**: how many "oh, that's a subtle bash quoting issue" moments did you hit while writing it, versus the equivalent in Python? Track this informally.
4. **Edit-run ergonomics**: does iterating on `research.py` feel meaningfully slower than iterating on a bash script? Should be ~the same.
5. **Testability**: did you actually write the unit tests in Phase 1's testing step? If yes, they're paying for themselves. If no, that's evidence Python's testability advantage is theoretical for this domain.

### Possible outcomes

- **Python clearly cleaner** → Open a follow-up plan to port `start-agent.sh`. The case is now empirical, not speculative.
- **Python comparable** → Leave `research.py` as Python (sunk cost, working code), but don't port the others. The two-language repo is awkward; document the rationale in an ADR.
- **Python worse** → Rewrite `research.py` in bash following the original `plans/research-vm-isolation.md`. Cost: a few days. Lesson: bash is right for this domain. Document in an ADR.

### Files

- An ADR documenting the outcome regardless of direction (added to `ADR.md`)

---

## Notes

- **Why not just extract a bash library?** Covered in the conversation that produced this plan: bash libraries cap at ~80 lines of useful sharing before the indirection cost exceeds the duplication cost. The lib-extraction option remains on the table if Phase 2 concludes Python isn't worth it; the two options are not mutually exclusive but they are mutually substitutable.
- **Why stdlib-only?** Adding `requirements.txt` to a host-bootstrapping script creates a chicken-and-egg problem (need `pip install` before you can run the thing that sets up your dev environment). Stdlib-only keeps the "just run it" property the bash version has.
- **Why not Go or Rust?** Compiled binaries lose the "edit and re-run" property. Distribution becomes "build for each target arch" or "ship a universal binary," neither of which is appropriate for a script that lives in the repo. Python is the closest dynamic language to bash on this axis (interpreted, single-file, no build step) while gaining real data structures.
- **Relationship to `plans/draft-tests.md`:** the test plan's Phase 4 (settings-injection tests) is exactly the kind of pure-function logic that becomes unit-testable in Python. If both plans land, `research.py`'s Phase 1 testing step naturally seeds a `tests/test_research.py` that grows alongside the integration tests.
- **Migration path for existing users:** `research.py` is greenfield, so there's no migration. If `start-agent.sh` is later ported, that's a separate plan with its own migration concerns.
- **Out of scope:** `start-claude.sh` (Apple Containers, totally different runtime), Dockerfile changes, OpenCode/Claude Code config changes. This plan touches the host-side orchestration script for Vane only.
