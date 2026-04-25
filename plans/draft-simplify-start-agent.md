# Simplify start-agent.sh: Drop Dead Weight

## Status

- [ ] Drop SearXNG `outgoing.proxies` drift check
- [ ] Drop `MODEL_COUNT=` stderr emission
- [ ] Simplify in-use detection's `existing_proj` decoration
- [ ] (Optional) Drop 30-day image staleness warning
- [ ] (Optional) Extract allowlist heredoc to `templates/agent-allowlist.txt`
- [ ] (Optional) Unify ollama/omlx probe blocks

## Context

`start-agent.sh` has accumulated some defensive code, dead emissions, and cosmetic decoration that no longer earn their keep. This plan removes them. None of these changes alter behavior in the common case; they remove code paths that are either obsolete migrations or purely informational.

This is independent of `plans/research-vm-isolation.md`, but Phase 2 of that plan already touches `start-agent.sh`, so the cheap items here (#1-#3) could be folded into that work.

## Goals

- Smaller, more readable `start-agent.sh`
- No regressions in steady-state behavior
- No removal of code that handles real platform variance (colima ssh quoting, HOST_IP discovery, mount drift, iptables atomicity)

## Unknowns / To Verify

- **Is `MODEL_COUNT=...` consumed anywhere?** The comment claims "Emit a machine-friendly summary line for the shell wrapper" — verify by `grep -n MODEL_COUNT start-agent.sh` and confirming nothing parses it. If a wrapper script outside this repo consumes it, leave it.
- **Has `BRIDGE_IP` ever drifted in practice?** The drift check exists because the old Squid-on-3128 path could leave stale settings. Verify with `git log -p -- start-agent.sh | grep -B2 -A20 "drift"` that this was the only motivating scenario. If `BRIDGE_IP` itself can shift across Colima versions, the drift check is still load-bearing.

---

## Step 1: Drop SearXNG `outgoing.proxies` drift check

**Why it doesn't pull weight**: The check at `start-agent.sh:588-609` was added to migrate users from an old Squid-on-port-3128 config to the current tinyproxy-on-port-8888 (commit `3604c5f` and prior). That migration window is closed. The seed block at lines 557-587 writes the correct value on first run, `BRIDGE_IP` rarely shifts (Docker reuses `172.17.0.1` across Colima restarts), and `--rebuild` is the user-facing recovery if it ever does.

**Action**:
- Delete lines 588-609 (the entire `else` branch including the `awk` parser, `sed` rewrite, and `SEARXNG_CONFIG_CHANGED=true`)
- Delete the `elif [[ "${SEARXNG_CONFIG_CHANGED:-false}" == "true" ]]` branch in the SearXNG container lifecycle (around lines 785-787) — without the drift check, this branch is unreachable
- Resulting structure: seed-if-missing, then start container

**Files**:
- `start-agent.sh`

**Testing**:
- Run `start-agent.sh` on an existing setup with a valid `~/.claude-agent/searxng/settings.yml` — verify no behavior change
- Manually edit `settings.yml` to a wrong proxy URL, run `start-agent.sh`, verify SearXNG starts but search fails (expected — user fix is to delete the file or `--rebuild`)

---

## Step 2: Drop `MODEL_COUNT=` stderr emission

**Why it doesn't pull weight**: The Python block at `start-agent.sh:1037-1038` emits `MODEL_COUNT=N` to stderr with a comment claiming a shell wrapper consumes it. No wrapper in this repo does. Dead output.

**Action**:
- Delete the trailing two lines of the Python block:
  ```python
  count = len(providers.get(provider_key, {}).get('models', {})) if provider_key else 0
  print(f"MODEL_COUNT={count}", file=sys.stderr)
  ```
- Delete the comment one line above

**Files**:
- `start-agent.sh`

**Testing**:
- Run `start-agent.sh`, verify model discovery messages still appear (the `[opencode-config] discovered N models...` line above is unaffected)

---

## Step 3: Simplify in-use detection's `existing_proj` decoration

**Why it doesn't pull weight**: Lines 1112-1117 detect when an interactive bash is already attached to the container, then build a complex Go-template chain to extract the project directory just to decorate the warning message. The script attaches anyway regardless of what it finds — the message is purely cosmetic.

**Action**:
Two options:
- **Option A (minimal)**: Replace the Go template at line 1114 with a simple message. Drop `existing_proj` extraction; warning becomes `warning: container '$CONTAINER_NAME' appears to have an interactive session attached. Attaching anyway; two shells sharing state may be confusing.`
- **Option B (more aggressive)**: Drop the entire in-use detection block (lines 1110-1117). The warning is informational and never changes behavior. Users will figure out two shells are weird from the prompt.

Recommendation: Option A. Keeps the heads-up, drops the gnarly template.

**Files**:
- `start-agent.sh`

**Testing**:
- Open two terminal windows, run `start-agent.sh` in both pointed at the same project — verify the warning fires and both shells attach

---

## Step 4: (Optional) Drop 30-day image staleness warning

**Why it might not pull weight**: Lines 766-772 check `$IMAGE_STAMP` (a file written at `start-agent.sh:765`) and warn if the image is >30 days old. The Dockerfile already has the apt staleness warning that fires per-shell when apt caches are >7 days old (`dockerfiles/claude-agent.Dockerfile:27-36`), which catches the more important signal at finer granularity. The image-level warning duplicates this with a coarser threshold.

**Action**:
- Delete lines 766-772 (the `elif` branch checking `$IMAGE_STAMP`)
- Delete the `date +%s > "$IMAGE_STAMP"` line at 765
- Decide whether to keep `IMAGE_STAMP=...` constant (line 182) — only used here, so remove if the warning goes
- Keep `rm -f "$IMAGE_STAMP"` in the `--rebuild` block (or remove if the constant is gone)

**Why optional**: Cheap to keep. Drop only if you want the script tighter; the apt warning is doing the load-bearing work either way.

**Files**:
- `start-agent.sh`

**Testing**:
- Verify `start-agent.sh` runs without errors after removal

---

## Step 5: (Optional) Extract allowlist heredoc to `templates/agent-allowlist.txt`

**Why it might not pull weight (in the script)**: The 155-line heredoc at `start-agent.sh:221-370` is content, not code. It bloats the script and forces users to read 155 lines of allowlist before getting to the next logic block. Moving it to `templates/agent-allowlist.txt` keeps the seed behavior identical but lets the script read more linearly.

**Action**:
- Create `templates/agent-allowlist.txt` with the current heredoc content
- Replace the heredoc at lines 220-372 with:
  ```bash
  ALLOWLIST_TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/templates/agent-allowlist.txt"
  if [[ ! -f "$ALLOWLIST_FILE" ]]; then
    cp "$ALLOWLIST_TEMPLATE" "$ALLOWLIST_FILE"
    echo "==> Seeded allowlist at $ALLOWLIST_FILE"
  fi
  ```
- This pattern matches the global CLAUDE.md template handling at lines 829-847

**Bonus**: `research.sh` will need its own allowlist template (`templates/research-allowlist.txt`). Both scripts converge on the same seeding pattern.

**Files**:
- `start-agent.sh` (modify)
- `templates/agent-allowlist.txt` (new)

**Testing**:
- Delete `~/.claude-agent/allowlist.txt`, run `start-agent.sh`, verify the template is copied verbatim
- Verify byte-for-byte equivalence: `diff <(old script seeded version) <(new script seeded version)`

---

## Step 6: (Optional) Unify ollama/omlx probe blocks

**Why it might not pull weight**: The probe at lines 722-751 has two `case` arms that differ only in URL path (`/api/tags` vs `/v1/models`) and whether to send `Authorization: Bearer $OMLX_API_KEY`. They share the same retry/timeout/warning shape.

**Action**:
- Replace the `case` block with a single helper:
  ```bash
  inference_probe() {
    local url="$1"; shift
    vm_ssh curl -sf --max-time 3 "$@" "$url" >/dev/null 2>&1
  }
  ```
- Compute the probe URL and auth args once based on `$BACKEND`, call once, emit a unified warning if it fails

**Why optional**: ~10 lines saved, mostly readability. Not load-bearing.

**Files**:
- `start-agent.sh`

**Testing**:
- Run with each backend (`--backend=ollama` and `--backend=omlx`) against a healthy server and a stopped server — verify probe behavior matches current

---

## Notes

- **Folding into `plans/research-vm-isolation.md` Phase 2**: Steps 1, 2, 3 are tiny diffs in the same file already being touched. Cheapest path is to do them in the same commit as Vane removal. Steps 4-6 are independent and can land separately.

- **Sequencing with Vane removal**: Step 1 (drift check removal) interacts with the SearXNG block that Phase 2 of the research plan touches. If both land, do Vane removal first, then drift-check removal — the SearXNG container lifecycle's `elif SEARXNG_CONFIG_CHANGED` branch only makes sense to delete after the drift check is gone.

- **Not in scope**: The CLI-flag-vs-env-var redundancy (`--memory` / `CLAUDE_AGENT_MEMORY`, etc.) looks like duplication but env vars are documented and presumably used; removing them would be a behavior change, not a simplification.
