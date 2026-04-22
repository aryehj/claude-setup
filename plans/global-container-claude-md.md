# Global Container CLAUDE.md Auto-Injection

## Status

- [ ] Phase 1: Draft and commit the shared CLAUDE.md template
- [ ] Phase 2: Wire seeding into `start-claude.sh` and `start-agent.sh`
- [ ] Phase 3: Mirror for OpenCode agents in `start-agent.sh` (after Phase 1 + 2 verified)

## Context

Both `start-claude.sh` (Apple Containers) and `start-agent.sh` (Colima + docker)
mount the host directory `~/.claude-containers/shared/` to `/root/.claude/` inside
the container. The Claude Code harness auto-injects `~/.claude/CLAUDE.md` into
every session's system prompt if that file exists. Today it doesn't, so models
running inside these containers routinely form wrong hypotheses about the
environment — e.g., probing `/Users/aryehj/.claude/` instead of `/root/.claude/`
because the cwd path suggests a macOS layout, or retrying failed fetches of
`github.com` without realizing a hostname allowlist is rejecting them.

The project `CLAUDE.md` at repo root already documents these decisions but is
only auto-injected when the session's cwd is under this repo. Projects opened
elsewhere inside the same container get none of this context. A global
`~/.claude/CLAUDE.md` fills that gap.

Key environment facts that trip models up:

- **Paths:** `$HOME=/root`; cwd path (`/Users/<name>/...`) is a bind mount, not
  a Mac FS. No `/Users/<name>/.claude` exists.
- **Egress (start-agent only):** HTTP proxy + hostname allowlist; `github.com`
  is NOT allowed by default, but `codeload.github.com` and
  `raw.githubusercontent.com` are. `HTTPS_PROXY`/`HTTP_PROXY` + `NODE_USE_ENV_PROXY=1`
  are pre-set.
- **Local inference (start-agent only):** Ollama or omlx on the macOS host,
  reachable via `$OLLAMA_HOST` / `$OMLX_HOST`. OpenCode is pre-wired.
- **UV / venv (both):** `UV_PROJECT_ENVIRONMENT=$TMPDIR/.venv` redirects project
  venvs away from any host-created `.venv/` (macOS binaries, unusable on Linux).
  `uv sync` is expected once per fresh session.
- **Sandbox (start-claude only):** bubblewrap active for bash; `/tmp` and
  `/root/.cache` are read-only at the mount layer; `$TMPDIR` is the writable
  scratch path.

Per the /plan conversation, the file will lead with `start-agent.sh`-relevant
content (the more constrained case) and note `start-claude.sh` exceptions at the
end. Claude handles "default: X, except when Y" framing better than smaller
models handle "if A then X else Y".

Deployment shape: store the template as a file in the repo; both scripts
seed-if-missing at `~/.claude-containers/shared/CLAUDE.md` at startup. Do not
clobber user edits. Dynamic regeneration (current backend, current allowlist
contents, etc.) is explicitly out of scope for this iteration.

## Goals

- A single file `~/.claude-containers/shared/CLAUDE.md` exists on the host after
  running either script for the first time, auto-injected into every Claude
  Code session in either container type.
- Content is concise (target ~60 lines of actual prose, excluding headings and
  blank lines) and structured as: common facts → start-agent specifics →
  start-claude exceptions.
- Seeding is idempotent and non-destructive: running the script again, or after
  the user has edited the file, leaves existing content alone.
- A `--reseed-global-claudemd` flag on each script forcibly overwrites, for
  picking up template updates.
- A one-line ADR entry (or new ADR) in `ADR.md` records the decision.

## Unknowns / To Verify

- **Does Claude Code honor `~/.claude/CLAUDE.md` as a memory file?** The
  claude-code-guide agent earlier in the conversation confirmed `CLAUDE.md` is
  auto-loaded by walking up from cwd, and cited the docs for `CLAUDE.local.md`,
  but didn't explicitly confirm the `~/.claude/CLAUDE.md` global path the way
  the docs cover `/Library/Application Support/ClaudeCode/CLAUDE.md` for
  org-wide config. Verify before Phase 1 by checking
  https://code.claude.com/docs/en/memory.md or by touching a sentinel file and
  observing whether it appears in the `<claudeMd>` block of a fresh session.
  If `~/.claude/CLAUDE.md` is NOT auto-loaded, fall back to
  `/root/.claude/CLAUDE.local.md` or an equivalent confirmed path. This is
  load-bearing for Phases 1 and 2.

- **How does OpenCode auto-load global instructions?** Needed for Phase 3.
  OpenCode's docs (https://opencode.ai/docs/) describe its agent/rules
  loading conventions — verify whether it reads `AGENTS.md`, a per-user
  `~/.config/opencode/AGENTS.md`, an `instructions` field in `opencode.json`,
  or something else. Confirm before Phase 3 whether OpenCode walks up from
  cwd like Claude Code does, and whether there is a global equivalent. The
  answer determines whether Phase 3 is "drop a file at path X" or "inject an
  `instructions` block into `opencode.json`" or both.

---

## Phase 1: Draft and commit the shared CLAUDE.md template

### Steps

1. Create `templates/global-claude.md` at the repo root (create `templates/`
   if absent). This file will be copied verbatim into
   `~/.claude-containers/shared/CLAUDE.md` by both scripts.

2. Populate it with the structure below. Keep prose tight — one or two
   sentences per fact. Use imperative voice ("Use `$TMPDIR` for scratch work")
   rather than descriptive ("Scratch work should use `$TMPDIR`"). No code
   blocks longer than 2 lines.

   Sections, in order:

   1. **One-paragraph preamble** explaining: this file is the global container
      CLAUDE.md; project-level CLAUDE.md overrides; the container is one of
      two sibling environments (`claude-agent` via Colima, or `claude-dev` via
      Apple Containers).

   2. **Filesystem (applies to both).** `$HOME=/root`. The cwd path is a bind
      mount from the macOS host — there is no `/Users/<name>/.claude`; all
      user config lives at `/root/.claude/`. `~/.claude/` is itself a
      host-side bind mount shared across every container.

   3. **Python & uv (applies to both).** `uv` is in `/usr/local/bin`; prefer
      `uv run` / `uv pip` / `uv venv`. `UV_PROJECT_ENVIRONMENT` redirects
      project venvs to `$TMPDIR/.venv`, so any on-disk `.venv/` in the project
      root is **ignored, not used** (it often contains macOS binaries).
      Running `uv sync` once per fresh session is normal.

   4. **Network egress (start-agent).** Egress flows through an in-VM HTTP
      proxy with a hostname allowlist at `~/.claude-agent/allowlist.txt` on
      the host. `HTTPS_PROXY` / `HTTP_PROXY` are pre-set; Node honors them via
      `NODE_USE_ENV_PROXY=1`. **`github.com` is not on the default allowlist**
      (proxy filters by hostname, so it can't be read-only) — for code reads,
      use `codeload.github.com` or `raw.githubusercontent.com`, which are
      allowed. A `403`/connection-refused on an unusual hostname most likely
      means the allowlist is rejecting it; ask the user to edit
      `~/.claude-agent/allowlist.txt` and run `start-agent.sh
      --reload-allowlist` rather than retrying blindly.

   5. **Local inference (start-agent).** A local model server runs on the
      macOS host, reachable via `$OLLAMA_HOST` (Ollama, port 11434) or
      `$OMLX_HOST` (omlx, port 8000) depending on `--backend`. OpenCode is
      already wired to it via `opencode.json` — no configuration needed.
      Route local-model calls there, not through the public proxy.

   6. **`start-claude.sh` differences (exceptions at the end).** A short
      bulleted list:
      - No network proxy / allowlist — full egress.
      - No local inference server — `$OLLAMA_HOST` / `$OMLX_HOST` are unset.
      - Bubblewrap sandbox is active for bash commands; `/tmp` and
        `/root/.cache` are **read-only** at the sandbox mount layer. Use
        `$TMPDIR` for scratch work (uv is already configured to respect it).
        A `read-only file system` error on `/tmp` means you're in the sandbox
        — retarget to `$TMPDIR`, don't escalate.

3. Review the draft for length; if it exceeds ~80 lines of prose, cut. Every
   sentence should prevent a concrete observed failure mode, not educate.

### Files

- `templates/global-claude.md` (new)
- `templates/` (new directory)

### Testing

- Render the file and read it top-to-bottom as if you were a Haiku-class
  model that just attached to the container. Ask: would this content have
  prevented the specific failure modes listed in Context? If any item doesn't
  map to a failure, drop it.
- `wc -l templates/global-claude.md` — confirm under ~100 lines total
  including headings.

---

## Phase 2: Wire seeding into both scripts

### Steps

1. In `start-claude.sh`, add a seeding block immediately after the existing
   logic that ensures `~/.claude-containers/shared/` exists (grep for
   `shared` to locate). Behavior:

   - Compute the target: `~/.claude-containers/shared/CLAUDE.md`.
   - If the target does not exist, copy `templates/global-claude.md` from the
     repo (resolve path relative to the script's own location via
     `${BASH_SOURCE[0]}`) into the target.
   - If the target exists, do nothing.
   - Print a one-line status (`Seeded global CLAUDE.md` or
     `Global CLAUDE.md already present, skipping`).

2. Mirror the same block in `start-agent.sh` at the equivalent location.
   Factor to a shared shell function only if the copy is literally identical;
   otherwise duplicate — the scripts already duplicate similar small bits and
   cross-script helper modules are out of pattern.

3. Add a `--reseed-global-claudemd` flag parser branch in both scripts. When
   set, the seeding block overwrites the target unconditionally and prints
   `Reseeded global CLAUDE.md from template`. Does NOT touch any other
   `~/.claude-containers/` contents.

4. Update `README.md` under the usage / flags section to document the new
   flag in one line each for both scripts.

5. Add a short entry to `ADR.md` (next ADR number, consistent with existing
   style): decision is "seed `~/.claude/CLAUDE.md` from repo template on first
   run of either script; seed-if-missing semantics; explicit
   `--reseed-global-claudemd` for updates." Rationale mirrors the Context
   section of this plan.

6. Update `CLAUDE.md` (the repo-root one) with a short bullet under the
   existing "Key decisions" list, cross-referencing the new ADR.

### Files

- `start-claude.sh` (edit)
- `start-agent.sh` (edit)
- `README.md` (edit)
- `ADR.md` (edit — append new ADR)
- `CLAUDE.md` (edit — add one bullet)

### Testing

- On a fresh host with no `~/.claude-containers/`: run `start-claude.sh`, exit,
  check that `~/.claude-containers/shared/CLAUDE.md` now exists and matches
  the template byte-for-byte (`diff templates/global-claude.md
  ~/.claude-containers/shared/CLAUDE.md` → empty).
- Modify the file manually (`echo '# user edit' >> ...`), re-run
  `start-claude.sh`, confirm the user edit survives.
- Run `start-claude.sh --reseed-global-claudemd`, confirm the file is back to
  the template and the `# user edit` line is gone.
- Repeat all three with `start-agent.sh`.
- Attach to the container, start `claude`, ask "what does your CLAUDE.md say
  about github.com?" — confirm the global content is present in the
  `<claudeMd>` block of the system prompt (visible via `/status` or by
  observing the model's answer reflects the template).

---

## Phase 3: Mirror for OpenCode agents in `start-agent.sh`

Only begin this phase after Phases 1 and 2 are implemented and verified end-
to-end — i.e., after confirming the global CLAUDE.md content actually lands in
Claude Code's system prompt inside a running container and materially changes
model behavior on the failure modes in Context. There is no point replicating
the pattern for OpenCode before we know the content itself is useful.

### Steps

1. **Resolve the Unknown for OpenCode loading.** Before any edits, read
   OpenCode's docs (start at https://opencode.ai/docs/) and determine the
   canonical way to supply global agent instructions. Likely candidates, in
   rough order of ergonomic fit:
   - A per-user file OpenCode auto-loads (e.g., `~/.config/opencode/AGENTS.md`
     or similar) analogous to Claude Code's `~/.claude/CLAUDE.md`.
   - An `instructions` / `system` / `rules` field inside `opencode.json`.
   - A per-project `AGENTS.md` OpenCode walks up from cwd.

   Document the chosen mechanism in a one-line comment at the seeding site.

2. **Reuse the same content source as Phase 1** — do NOT fork the template.
   Either:
   - (a) Symlink/copy `templates/global-claude.md` into the OpenCode-expected
     location at container startup. The file is already written in a neutral
     voice ("you are running inside…") that applies equally to either agent.
   - (b) If OpenCode requires the content inline in `opencode.json`, read the
     template at script runtime and inject its contents as a string into the
     `instructions` field, next to the existing provider / model-mode edits
     in the `opencode.json` generator block.

   Prefer (a) if available — one source of truth, no JSON escaping, and
   template edits take effect without regenerating `opencode.json`.

3. **Seed-if-missing semantics match Phase 2.** Don't clobber existing user
   edits to the OpenCode-side file or config block. Extend
   `--reseed-global-claudemd` to also reseed the OpenCode copy, or introduce
   `--reseed-global-agentmd` if the filenames diverge and separate control
   makes sense. Single flag is preferred.

4. **`start-claude.sh` is unaffected by this phase** — it doesn't run
   OpenCode. Do not touch that script.

5. **Update README.md and ADR.md** to note the OpenCode mirror, either as a
   one-line addendum under the existing entry from Phase 2 or as a sibling
   note, depending on which reads more cleanly.

### Files

- `start-agent.sh` (edit)
- `README.md` (edit — one-line addition)
- `ADR.md` (edit — extend the Phase 2 ADR or add a short sibling entry)

### Testing

- Launch `start-agent.sh` on a host with no existing OpenCode instruction
  file: confirm the file/config lands at the verified path and the contents
  match the template source.
- Start `opencode` inside the container and prompt: "what does your global
  instructions file say about `github.com`?" Confirm the model's answer
  reflects the template content (i.e., the instructions are in its context).
- Modify the OpenCode file manually, re-run `start-agent.sh`, confirm edits
  survive. Then run the reseed flag, confirm overwrite.
- Cross-check: open both Claude Code and OpenCode in the same container and
  ask the same question. The answers should describe the same constraints
  (modulo wording) since they read from the same template.

---

## Notes

- **Why not bake into the Dockerfile instead of host-side seeding?** The
  `~/.claude-containers/shared/` bind mount overrides `/root/.claude/` in the
  image, so anything written there at build time is hidden at runtime. Host
  seeding is the only path that's actually read.
- **Template drift over time.** With seed-if-missing semantics, users who ran
  the script months ago won't pick up template updates without explicitly
  passing `--reseed-global-claudemd`. Acceptable for v1. A future iteration
  could compare checksums and prompt to update, akin to the existing skills
  sync — but the skills sync clobbers per-subdirectory, which is harsher than
  we want for a file the user may have edited.
- **Static content means claims can go stale.** e.g., if `github.com` is
  later added to the default allowlist seed, the template still says it's
  blocked. Any changes to allowlist defaults, backends, or sandbox behavior
  must update the template too. Low-risk for now given how rarely those
  change.
- **Alternative considered: per-script content.** Rejected per the conversation
  that produced this plan — one file with a clear "exceptions at the end"
  pattern is easier to maintain and reads well for Claude.
