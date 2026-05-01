# Redesign start-agent.sh around a per-sandbox trust boundary

## Status

- [ ] Phase 1: Sandbox detection + `--init-sandbox PATH`
- [ ] Phase 2: Repoint host-state constants under `$SANDBOX/state/`
- [ ] Phase 3: Per-sandbox Colima profile + narrow VM mount
- [ ] Phase 4: Restructure `docker run` bind-mounts (allowlist `:ro`)
- [ ] Phase 5: Help text, log messages, `--rebuild` prompt
- [ ] Phase 6: Update CLAUDE.md + add ADR-033
- [ ] Phase 7: Update README user walkthrough
- [ ] Phase 8: Validate (syntax, static test, reference sweep)

## Context

`start-agent.sh` today scatters its host-side state across several `$HOME` paths and relies on Colima's default `$HOME` virtiofs mount. Concretely:

- `start-agent.sh:191-200` sets `CLAUDE_CONFIG_DIR=$HOME/.claude-containers/shared`, `CLAUDE_JSON_FILE=$HOME/.claude-containers/claude.json`, `OPENCODE_CONFIG_DIR=$HOME/.claude-agent/opencode-config`, `OPENCODE_DATA_DIR=$HOME/.claude-agent/opencode-data`, `ALLOWLIST_FILE=$HOME/.claude-agent/allowlist.txt`, `SEARXNG_DIR=$HOME/.claude-agent/searxng`.
- `start-agent.sh:546-555` calls `colima start` with no `--mount` flag, so the default mounts apply (`$HOME` and `/tmp/colima` into the VM).
- `start-agent.sh:1267-1271` bind-mounts five host paths into the container: the project dir, plus the four state dirs above. All RW.
- `start-agent.sh:186` uses a single shared Colima profile (`claude-agent`) for all projects.

This shape has three concrete consequences. (1) An agent process that escapes the container into the VM has read access to all of `$HOME` via Colima's default virtiofs mount — SSH keys, browser data, dotfiles, Keychain-adjacent state. (2) The `allowlist.txt` is RW from inside the container (tinyproxy reloads only on host-driven `--reload-allowlist`, but the on-disk file is reachable). (3) State is shared across every project, so `~/.claude-containers/shared/.credentials.json` has the same blast radius for every run.

The user wants a redesign where the entire host-side trust surface is exactly one directory. Multiple sandboxes give them a coarse-grained isolation knob; everything inside a sandbox is mutually trusting by design.

Design discussion settled the following before this plan was written:
- Per-sandbox Colima VM (`sandbox-<name>` profile) — full VM-level isolation between sandboxes, at the cost of one image rebuild per sandbox.
- `.sandbox` marker file at the sandbox root — empty, basename-as-name. Detection walks up from `$(pwd)`.
- One layout (option B): `state/claude/` (dir) and `state/claude.json` (file) as siblings; `state/opencode/{config,data}` both RW; `state/allowlist.txt` mounted `:ro` at `/etc/claude-agent/allowlist.txt`; `state/searxng/settings.yml` (existing pattern).
- Source code lives under `$SANDBOX/repos/<repo>/`. PROJECT_DIR must resolve inside `$SANDBOX/repos/`.
- The container does **not** bind-mount `$SANDBOX` as a whole. That's what makes the `:ro` on the allowlist load-bearing — the agent has no other writable path to it.
- No automated migration from `~/.claude-containers/` and `~/.claude-agent/`. CLAUDE.md gets a manual `cp` recipe.
- `start-claude.sh` and `research.py` are out of scope for this plan.

## Goals

- `$SANDBOX_ROOT` is the only host-side path the VM or container can see. Everything outside it is invisible to both.
- Each sandbox gets its own Colima VM (`sandbox-<name>`), launched with `--mount $SANDBOX_ROOT:w` (no default `$HOME` mount).
- The container's allowlist is bind-mounted `:ro` at a stable in-container path; the agent can read but cannot rewrite which URLs are permitted.
- Sandboxes are detected by an explicit `.sandbox` marker, not by env vars or directory conventions. `--init-sandbox PATH` creates one.
- Running outside a sandbox is a hard error with a clear remediation message.
- No backwards-compatibility shims for the old `$HOME`-scattered layout. Legacy users follow a documented manual migration.

## Approach

The trust boundary moves from "scattered host paths under `$HOME`, plus whatever Colima's default mount drags along" to "exactly `$SANDBOX_ROOT`." Two architectural moves carry the change. First, `colima start --mount $SANDBOX_ROOT:w` replaces the default `$HOME` virtiofs mount, so the VM literally cannot see anything else on the host. Second, the `docker run` mount block stays granular — separate path-renamed bind-mounts for each in-container destination — so we can keep `:ro` on the allowlist meaningful. If we collapsed to a single `$SANDBOX:$SANDBOX` mount instead, the agent would have a writable path to the allowlist via the union view and the `:ro` line elsewhere wouldn't help.

Sandbox detection uses an explicit marker file rather than `$CLAUDE_SANDBOX` env or cwd-based heuristics. Marker files are easy to grep for, hard to forget, and let `cd` into any depth of the sandbox tree work transparently. Sandbox name is the basename of the marker's directory; the only namespacing it drives is the Colima profile (`sandbox-<name>`). Two sandboxes with colliding basenames would share a profile — documented user responsibility, not engineered around.

Per-sandbox VMs mean one image build per sandbox (each VM has its own docker daemon). That's a known cost, mitigated by the fact that sandboxes are coarse-grained — you'd typically have a small number, not one per repo. If image-rebuild cost becomes annoying, `docker save | docker load` across VMs is a follow-up; not in scope here.

## Unknowns / To Verify

1. **Does `colima start --mount` replace or augment the default `$HOME` mount?** The whole "narrow trust surface" claim depends on `--mount` being a replacement, not additive. If it augments, we'd need an explicit way to suppress defaults. Verify by reading `colima start --help` output for the version on the user's machine, or by post-launch inspection: `colima ssh -p sandbox-<name> -- mount | grep virtiofs` should show only `$SANDBOX_ROOT`, not `$HOME` or `/Users/...`. *Affects: Phase 3 step 2; if defaults are additive, the phase needs an extra `--mount-inherit=false` (or equivalent) flag, which may not exist on all Colima versions.*

2. **Does `docker run -v $SANDBOX/repos:$SANDBOX/repos` work without `$SANDBOX` itself being a mount target?** Docker normally creates intermediate parent dirs for mount targets, but verify behavior on Colima's daemon version specifically. Quick check: launch a throwaway container with `-v /a/b:/a/b` where `/a` doesn't exist on the host and is a fresh path; confirm `ls /a` inside the container shows only `b/`. *Affects: Phase 4 step 1.*

3. **Is `/etc/claude-agent/` writable in the image as it stands?** The Dockerfile at `dockerfiles/claude-agent.Dockerfile` may or may not create this directory. Bind-mounting a file at `/etc/claude-agent/allowlist.txt` requires the parent dir to be writable enough that docker can create the mount point — usually fine on `tmpfs`-style overlays but worth checking. If not, add a `RUN mkdir -p /etc/claude-agent` to the Dockerfile. *Affects: Phase 4 step 1; possibly Dockerfile.*

4. **Does OpenCode tolerate `state/opencode/config` being writable but containing a host-side path that may have been edited mid-session?** Pre-redesign behavior is identical (RW), so the answer is "yes, this is current behavior." Not a real unknown — flagged only to confirm we're not regressing anything. No verification needed.

---

## Phase 1: Sandbox detection + `--init-sandbox PATH`

### Steps

1. Add `--init-sandbox PATH` to the argparse loop at `start-agent.sh:92-120`. Treat it as a one-shot operation: when set, perform the init and `exit 0` before any VM/container logic runs.

2. Implement `init_sandbox(target_path)`:
   - Reject if `target_path` already exists.
   - `mkdir -m 0700 -p "$target_path"`.
   - Create subdirs: `state/claude/`, `state/opencode/config/`, `state/opencode/data/`, `state/searxng/`, `repos/`.
   - `touch "$target_path/.sandbox"` (empty marker file).
   - Seed `state/claude.json` with `{}` so the file mount has a valid JSON target.
   - Seed `state/allowlist.txt` from the same default allowlist content used today at `start-agent.sh:222-511` — extract the heredoc into a helper function so init and the existing seed-on-first-run path share it. (This is technically a refactor that the simplify-start-agent plan also lists as optional Step 5; landing it here is fine.)
   - Print a "next step" message: `cd "$target_path/repos" && git clone <repo>`, then `start-agent.sh` from inside the cloned repo.

3. Implement `find_sandbox_root()`:
   - Walk up from `$(pwd)`: at each level, test for `-f .sandbox`. Stop at `/`.
   - On hit: echo the dir, return 0. On miss: return 1.

4. After arg parsing (around `start-agent.sh:122` where `PROJECT_DIR` is currently resolved), call `find_sandbox_root` unconditionally. If empty, print a clear error pointing at `--init-sandbox` and `exit 1`.

5. Set `SANDBOX_ROOT` and `SANDBOX_NAME=$(basename "$SANDBOX_ROOT")`. Validate `SANDBOX_NAME` matches `^[a-zA-Z0-9_-]+$` so it's safe as a Colima profile suffix.

### Acceptance criteria

- Running `start-agent.sh` outside any sandbox prints the remediation message and exits non-zero, without starting Colima.
- `start-agent.sh --init-sandbox /tmp/sb-test` creates the directory tree, marker, and seed files; running `start-agent.sh --init-sandbox /tmp/sb-test` again refuses (path exists).

---

## Phase 2: Repoint host-state constants under `$SANDBOX/state/`

### Steps

1. In the constants block at `start-agent.sh:185-201`, replace:
   - `CLAUDE_CONFIG_DIR` → `$SANDBOX_ROOT/state/claude`
   - `CLAUDE_JSON_FILE` → `$SANDBOX_ROOT/state/claude.json` (sibling, not nested)
   - `OPENCODE_CONFIG_DIR` → `$SANDBOX_ROOT/state/opencode/config`
   - `OPENCODE_DATA_DIR` → `$SANDBOX_ROOT/state/opencode/data`
   - `ALLOWLIST_DIR` (drop)
   - `ALLOWLIST_FILE` → `$SANDBOX_ROOT/state/allowlist.txt`
   - `SEARXNG_DIR` → `$SANDBOX_ROOT/state/searxng`
   - `SEARXNG_SETTINGS_FILE` → `$SANDBOX_ROOT/state/searxng/settings.yml`

2. Audit downstream uses of these constants (state-dir creation at `start-agent.sh:907`, `mkdir -p` calls, the SearXNG seed at `start-agent.sh:695-728`, the global CLAUDE.md / AGENTS.md seed at `start-agent.sh:910-949`, the OpenCode config write at `start-agent.sh:978-1116`, the skills-sync `dest=$CLAUDE_CONFIG_DIR/skills` at `start-agent.sh:1121`). All should still resolve correctly with the new values; no logic changes needed beyond confirming the path strings.

3. Replace the old `mkdir -p "$ALLOWLIST_DIR"` at `start-agent.sh:222` with the new state-dir-creation block (most of which moves to `init_sandbox` from Phase 1; the runtime path can assume the dirs already exist and just `mkdir -p` defensively).

4. Validate `PROJECT_DIR` (resolved at `start-agent.sh:122-123` from `${POSITIONAL[0]:-$(pwd)}`):
   - After resolving to an absolute path, require `$PROJECT_DIR` to be inside `$SANDBOX_ROOT/repos/` (string-prefix check). Reject otherwise with: "PROJECT_DIR ($PROJECT_DIR) must be a subdirectory of $SANDBOX_ROOT/repos/."
   - This forecloses running from `$SANDBOX_ROOT` itself, from `$SANDBOX_ROOT/state/`, or from anywhere outside the sandbox tree.

### Acceptance criteria

- All `$HOME/.claude-containers/` and `$HOME/.claude-agent/` references in `start-agent.sh` are gone (verify by grep).
- Running `start-agent.sh` from `$SANDBOX_ROOT/state/` rejects with a clear message; running from `$SANDBOX_ROOT/repos/foo` proceeds.

---

## Phase 3: Per-sandbox Colima profile + narrow VM mount

### Steps

1. Change `COLIMA_PROFILE` (currently `claude-agent` at `start-agent.sh:186`) to `sandbox-$SANDBOX_NAME`. Container name (`CONTAINER_NAME=claude-agent`) stays — each VM has its own docker namespace, so reuse is fine.

2. Modify `start_colima_vm()` at `start-agent.sh:546-555` to add `--mount "$SANDBOX_ROOT:w"` to the `colima start` invocation. This replaces the default `$HOME` mount (assuming Unknown #1 resolves as expected; if not, also add whatever flag suppresses default mounts).

3. Update the `--rebuild` VM-deletion confirm prompt at `start-agent.sh:594-601`. The prompt text should reflect that deletion is now per-sandbox-scoped, not global ("Also delete and recreate the Colima VM for sandbox '$SANDBOX_NAME' (`sandbox-$SANDBOX_NAME`)?"). Less scary language is appropriate.

4. Update `docker context use "colima-$COLIMA_PROFILE"` at `start-agent.sh:622` — this already templates on the profile name, so no change needed beyond confirming the new value flows through.

### Acceptance criteria

- `colima list` after a fresh `start-agent.sh` shows a profile named `sandbox-<name>`, not `claude-agent`.
- `colima ssh -p sandbox-<name> -- mount | grep virtiofs` shows only `$SANDBOX_ROOT` mounted; `$HOME` is not visible from inside the VM.

---

## Phase 4: Restructure `docker run` bind-mounts (allowlist `:ro`)

### Steps

1. Replace the mount block at `start-agent.sh:1267-1271` with:
   ```
   -v "$SANDBOX_ROOT/repos:$SANDBOX_ROOT/repos"
   -v "$SANDBOX_ROOT/state/claude:/root/.claude"
   -v "$SANDBOX_ROOT/state/claude.json:/root/.claude.json"
   -v "$SANDBOX_ROOT/state/opencode/config:/root/.config/opencode"
   -v "$SANDBOX_ROOT/state/opencode/data:/root/.local/share/opencode"
   -v "$SANDBOX_ROOT/state/allowlist.txt:/etc/claude-agent/allowlist.txt:ro"
   ```
   The first mount covers all repos and is RW. The next four are state mounts at the paths Claude Code and OpenCode expect. The last is the `:ro` allowlist — the agent can `cat /etc/claude-agent/allowlist.txt` to know what's permitted but cannot rewrite the source of truth. Crucially, do **not** add `-v "$SANDBOX_ROOT:$SANDBOX_ROOT"` — that would expose the allowlist as RW via the union view and defeat the design.

2. Update `attach_existing()` at `start-agent.sh:1175-1181` and the existing-container check at `start-agent.sh:1183-1198`. The current logic recreates the container if `$existing_mount != $PROJECT_DIR` because the project dir was the only mount that varied. With the new architecture, all containers in a sandbox share the same `$SANDBOX_ROOT/repos` mount — a project-dir change means re-`exec`'ing with a new `-w`, not recreating. Simplify the existing-container check accordingly: if the container exists and its `$SANDBOX_ROOT/repos` mount matches, just `docker start` and `docker exec -w "$PROJECT_DIR" …`.

3. If Unknown #3 confirms `/etc/claude-agent/` does not exist in the image, add `RUN mkdir -p /etc/claude-agent` to `dockerfiles/claude-agent.Dockerfile`. Otherwise leave the Dockerfile untouched.

### Acceptance criteria

- Inside the running container, `cat /etc/claude-agent/allowlist.txt` succeeds; `echo x >> /etc/claude-agent/allowlist.txt` fails with EROFS.
- Inside the running container, `ls $SANDBOX_ROOT` shows only `repos/` (not `state/` or `.sandbox`).

---

## Phase 5: Help text, log messages, `--rebuild` prompt

### Steps

1. Rewrite the `usage()` block at `start-agent.sh:37-90`:
   - Top-of-file comment: replace the "shared VM + shared container" framing with the per-sandbox model. Note the trust boundary explicitly.
   - USAGE: add the `start-agent.sh --init-sandbox PATH` line.
   - OPTIONS: add `--init-sandbox PATH`. Update the `--reload-allowlist` and `--reseed-allowlist` lines to reference `$SANDBOX_ROOT/state/allowlist.txt` (or a generic "the sandbox's allowlist file" since the path varies by sandbox).
   - ALLOWLIST: section: rewrite path references; the file is now sandbox-relative.
   - ENVIRONMENT: section: unchanged (none of these vars referenced the old paths).

2. Update the "Creating container" log block at `start-agent.sh:1252-1257` to include the sandbox name and root path:
   ```
   sandbox  : <name>  ($SANDBOX_ROOT)
   project  : $PROJECT_DIR
   proxy    : http://$BRIDGE_IP:$TINYPROXY_PORT  (allowlist: $ALLOWLIST_FILE, ro in container)
   inference: $INFERENCE_LABEL at http://$HOST_IP:$INFERENCE_PORT
   ```

3. Update the `--reload-allowlist` exit message at `start-agent.sh:832-838` to mention the sandbox name.

4. Update the seed/reseed messages around `start-agent.sh:512-517` (allowlist seed) — paths in the messages now reference the sandbox.

### Acceptance criteria

- `start-agent.sh --help` mentions `--init-sandbox`, the trust boundary, and `$SANDBOX_ROOT/state/allowlist.txt`.
- No help text or log message references `~/.claude-containers/` or `~/.claude-agent/`.

---

## Phase 6: Update CLAUDE.md + add ADR-033

### Steps

1. CLAUDE.md "start-agent.sh key decisions" block (`CLAUDE.md:69-89`):
   - Replace the "Colima, one shared VM + one shared container" line with the per-sandbox model.
   - Replace the "Allowlist file on the host, not in the repo" line; the allowlist now lives in `$SANDBOX_ROOT/state/allowlist.txt` and is RO from inside the container.
   - Replace the "Shared `~/.claude` state with `start-claude.sh`" line; sandboxes have their own `state/claude/` and there is no longer cross-script sharing. Note this is a deliberate trade-off (lose shared auth, gain trust boundary).
   - Add a one-liner for the `--init-sandbox` UX. Reference the new ADR.

2. Add ADR-033 to `ADR.md`. Title: "Per-sandbox VM and one-directory trust boundary for start-agent.sh". Body covers: the threat model (Colima default `$HOME` mount, scattered state, RW allowlist); the design (marker file, per-sandbox profile, `--mount $SANDBOX:w`, granular bind-mounts with `:ro` allowlist); rejected alternatives (per-project state under `$HOME`, sandbox-as-single-mount, dedicated macOS user); the cost (per-sandbox image rebuild, manual migration). Cross-link from ADR-006 and ADR-014 if relevant.

3. Add a short migration recipe to CLAUDE.md (under start-agent.sh's "Making changes" section, or a new "Migrating from legacy state" subsection):
   ```
   start-agent.sh --init-sandbox ~/sandboxes/default
   cp -r ~/.claude-containers/shared/* ~/sandboxes/default/state/claude/
   cp ~/.claude-containers/claude.json ~/sandboxes/default/state/claude.json
   cp -r ~/.claude-agent/opencode-config ~/sandboxes/default/state/opencode/config
   cp -r ~/.claude-agent/opencode-data   ~/sandboxes/default/state/opencode/data
   cp ~/.claude-agent/allowlist.txt      ~/sandboxes/default/state/allowlist.txt
   # Move repos in:  mv ~/Code/foo ~/sandboxes/default/repos/foo
   # Once verified:   rm -rf ~/.claude-containers ~/.claude-agent
   ```

### Acceptance criteria

- CLAUDE.md no longer claims state is shared with `start-claude.sh` from `start-agent.sh`'s perspective.
- ADR-033 exists and is the highest-numbered ADR.

---

## Phase 7: Update README user walkthrough

### Steps

1. Skim the existing README sections that reference start-agent.sh (search for "start-agent" and the legacy paths). Update any path references to be sandbox-relative.

2. Add a "First run" subsection under start-agent.sh's coverage, showing:
   - `start-agent.sh --init-sandbox ~/sandboxes/default` produces the directory tree.
   - A short tree diagram of `~/sandboxes/default/` with `.sandbox`, `state/`, `repos/`.
   - The expected workflow: `cd ~/sandboxes/default/repos && git clone <repo> && cd <repo> && start-agent.sh`.

3. If the README has a "Multiple projects" or "Per-project" subsection, replace it with a "Multiple sandboxes" note: each sandbox is its own VM, run one at a time, projects within a sandbox share auth/memory.

### Acceptance criteria

- README's start-agent.sh walkthrough starts with `--init-sandbox`.
- No README path references `~/.claude-containers/` or `~/.claude-agent/`.

---

## Phase 8: Validate (syntax, static test, reference sweep)

### Steps

1. `bash -n start-agent.sh` — must pass.

2. Run `python3 tests/test_agent_sh.py`. The static check confirms no `docker run` publishes a host port; the new mount block doesn't add `-p`, so this should still pass.

3. `grep -n -E '(\.claude-containers|\.claude-agent)' start-agent.sh CLAUDE.md README.md` — expect zero hits.

4. `grep -n -E 'CLAUDE_CONFIG_DIR|CLAUDE_JSON_FILE|OPENCODE_CONFIG_DIR|OPENCODE_DATA_DIR|ALLOWLIST_FILE|SEARXNG_DIR|SEARXNG_SETTINGS_FILE' start-agent.sh` — confirm all hits resolve to the new sandbox-relative paths.

5. End-to-end smoke test (manual, requires macOS host with Colima):
   - `start-agent.sh --init-sandbox /tmp/sb-test`.
   - `cd /tmp/sb-test/repos && git clone https://github.com/aryehj/start-claude.git && cd start-claude`.
   - `start-agent.sh` — VM comes up, image builds, container launches.
   - Inside the container: `cat /etc/claude-agent/allowlist.txt` (works), `echo x >> /etc/claude-agent/allowlist.txt` (fails EROFS), `ls $SANDBOX_ROOT` (only `repos/` visible), `ls $HOME` from outside the sandbox (host) is invisible from VM perspective via `colima ssh -p sandbox-sb-test -- ls $HOME` (path missing or empty).
   - `start-agent.sh --reload-allowlist` from the host updates tinyproxy without restarting the container.
   - `start-agent.sh --rebuild` from the host removes the per-sandbox VM with the updated prompt copy.

### Acceptance criteria

- All static checks pass.
- The smoke test confirms (a) VM mount narrowing actually narrows, (b) allowlist is RO from inside, (c) per-sandbox VM is independent of any other Colima profile on the box.

---

## Notes

- **Sandbox name collisions are user responsibility.** Two sandboxes with `basename` `personal` would resolve to the same Colima profile (`sandbox-personal`). Detection of "different `$SANDBOX_ROOT` for an existing profile" would require parsing Colima's per-profile config; deferring it. Document the constraint in the ADR.

- **Per-sandbox image rebuild cost.** Each sandbox VM has its own docker daemon, so `claude-dev:latest` is built once per sandbox. Acceptable; sandboxes are coarse-grained. If this becomes friction, a follow-up plan could `docker save | docker load` an image tarball cached at a known host path (outside any sandbox) — but that re-introduces a host path the VM mounts, so the design needs care.

- **start-claude.sh and research.py are out of scope.** `start-claude.sh` has a similar shared-`$HOME`-state pattern and could benefit from the same redesign; deferring. `research.py` already has its own Colima profile (`research`) but inherits the default `$HOME` mount; same redesign applies in principle. Both are separate plans.

- **No backwards-compatibility shim for `~/.claude-containers/` or `~/.claude-agent/`.** The script does not detect or migrate from those paths automatically. If a user runs the new script with the old paths still present, they're simply ignored; the manual migration recipe in CLAUDE.md is the supported path.

- **Marker file is empty by design.** Future use cases (per-sandbox name override, allowlist-template selection, default-VM-size override) could add structured fields, but adding now would be premature. Land empty; structure later only when something forces it.
