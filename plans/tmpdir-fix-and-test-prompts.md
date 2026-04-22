# Fix TMPDIR and add test prompts

## Status

- [x] Phase 1: Add TMPDIR to container env
- [x] Phase 2: Create test prompts
- [ ] Phase 3: Verify fix

## Context

Sonnet 4.6 test transcript (`2026-04-22-163637-...txt`) revealed that `$TMPDIR` is unset in Bash tool shells inside both `claude-dev` (start-claude.sh) and `claude-agent` (start-agent.sh) containers. The model's command `mkdir -p "$TMPDIR/scratch-uv-proj"` expanded to `/scratch-uv-proj` at the filesystem root because the variable was empty.

Root cause: `TMPDIR`, `UV_CACHE_DIR`, and `UV_PROJECT_ENVIRONMENT` are set in `.bashrc` and `/etc/profile.d/`, which are not sourced by non-interactive, non-login shells — the mode Claude Code's Bash tool uses.

The global CLAUDE.md tells models to "use `$TMPDIR`" as if it's always set, but it isn't in Bash tool invocations.

**Affected files:**
- `start-claude.sh:47-55` — `CONTAINER_ENV` array
- `start-agent.sh:1014-1037` — `DOCKER_ENV_ARGS` array

## Goals

- `TMPDIR=/tmp` is present in all shells inside both container types, including Bash tool invocations
- Test prompts exist for both Claude Code and OpenCode, exercising the same 3 tasks
- Re-running the original test no longer creates `/scratch-uv-proj` at root

---

## Phase 1: Add TMPDIR to container env

### Steps

1. Edit `start-claude.sh` — add `-e "TMPDIR=/tmp"` to the `CONTAINER_ENV` array (after line 55, before the closing paren, or inline with other env vars).

2. Edit `start-agent.sh` — add `-e "TMPDIR=/tmp"` to the `DOCKER_ENV_ARGS` array (around line 1025, after the `NO_PROXY` entry).

3. Both scripts should set `TMPDIR=/tmp` unconditionally. The bubblewrap sandbox in `start-claude.sh` may override this to its own temp dir at runtime, which is fine — the `/tmp` fallback is only load-bearing when the sandbox doesn't run (e.g., `start-agent.sh` which has no bubblewrap).

### Files

- `start-claude.sh` (edit)
- `start-agent.sh` (edit)

### Testing

After `--rebuild`, inside either container:
```bash
bash -c 'echo TMPDIR=$TMPDIR'
```
Should print `TMPDIR=/tmp`, not empty.

---

## Phase 2: Create test prompts

### Steps

1. Create `tests/` directory at project root.

2. Create `tests/claude-code-prompt.md` with the original 3-task prompt:
   ```
   I need three quick things done in this container. Work through them in order, don't stop to ask me questions, just do it:

   1. Look at my Claude Code user settings — specifically, tell me the current value of `effortLevel` and `showThinkingSummaries`. Read them from disk.

   2. Fetch the README of https://github.com/astral-sh/uv (the top of the file is fine — first ~50 lines) and paste the first paragraph back to me.

   3. In a scratch workspace (NOT the current project dir), create a new Python project that depends on `requests`, install it, and run a one-liner that prints `requests.__version__`. Report the version you got.

   After you're done, at the very end of your reply, print a line:
     TOOL_CALL_COUNT: <N>
   where N is your best estimate of how many tool calls you made total (including ones that errored). Also list any tool calls that returned an error.
   ```

3. Create `tests/opencode-prompt.md` with an adapted version for OpenCode (local models via Ollama/omlx):
   ```
   I need three quick things done. Work through them in order, don't stop to ask me questions, just do it:

   1. Read the OpenCode config file at `~/.config/opencode/opencode.json` and tell me which inference provider is configured (look for the `provider` key).

   2. Fetch the README of https://github.com/astral-sh/uv (the top of the file is fine — first ~50 lines) and paste the first paragraph back to me.

   3. In a scratch workspace (use $TMPDIR or /tmp, NOT the current project dir), create a new Python project that depends on `requests`, install it, and run a one-liner that prints `requests.__version__`. Report the version you got.

   After you're done, print a line:
     TOOL_CALL_COUNT: <N>
   where N is how many tool calls you made total.
   ```

   Notes on OpenCode adaptation:
   - Task 1 changed from Claude Code settings to OpenCode config (Claude settings don't exist in OpenCode context)
   - Task 3 adds explicit `$TMPDIR or /tmp` guidance since local models may need the hint
   - Simplified the error-reporting ask (local models may not track tool errors reliably)

### Files

- `tests/claude-code-prompt.md` (create)
- `tests/opencode-prompt.md` (create)

### Testing

Prompts are static text files; no automated test. Manual validation:
- Copy `tests/claude-code-prompt.md` content into Claude Code session
- Copy `tests/opencode-prompt.md` content into OpenCode session
- Verify neither creates `/scratch-*` at filesystem root

---

## Phase 3: Verify fix

### Steps

1. Rebuild `start-claude.sh` container:
   ```bash
   ./start-claude.sh --rebuild
   ```

2. Inside the new container, run Claude Code with Sonnet and paste the `tests/claude-code-prompt.md` content.

3. After completion, verify no root pollution:
   ```bash
   ls -d /scratch* 2>/dev/null && echo "FAIL: root pollution" || echo "PASS: no root pollution"
   ```

4. Verify the scratch project was created in `/tmp/`:
   ```bash
   ls /tmp/scratch* 2>/dev/null || ls $TMPDIR/scratch* 2>/dev/null
   ```

5. Repeat for `start-agent.sh` if OpenCode testing is desired.

### Files

No files changed in this phase (verification only).

### Testing

- `TMPDIR` should be `/tmp` in fresh bash shells
- No directories created at filesystem root (`/scratch-*`)
- Scratch project exists under `/tmp/` or sandbox-provided `$TMPDIR`

---

## Notes

- The `.bashrc` / `/etc/profile.d/` setup for `UV_CACHE_DIR` and `UV_PROJECT_ENVIRONMENT` still has value — it provides the dynamic `${TMPDIR:-/tmp}` expansion for interactive shells where the sandbox has overridden `TMPDIR`. The `CONTAINER_ENV`/`DOCKER_ENV_ARGS` fix is specifically for non-interactive Bash tool shells.

- Cleanup command for the polluted root directory from the original test:
  ```bash
  rm -rf /scratch-uv-proj /tmp/scratch-proj
  ```

- The OpenCode prompt targets local models (Ollama/omlx) which may have lower instruction-following fidelity than Claude. The prompt is intentionally simpler but still exercises the same three capability axes: file read, web fetch, environment setup.
