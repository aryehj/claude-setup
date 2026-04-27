# Simplify start-agent.sh: Drop Dead Weight

## Status

- [x] Step 1 — Drop SearXNG `outgoing.proxies` drift check
- [x] Step 2 — Drop `MODEL_COUNT=` stderr emission
- [x] Step 3 — Simplify in-use detection's `existing_proj` decoration
- [x] Step 4 — Drop 30-day image staleness warning *(was optional; promoted — cheap and isolated)*
- [ ] (Optional) Step 5 — Extract allowlist heredoc to `templates/agent-allowlist.txt`
- [ ] (Optional) Step 6 — Unify ollama/omlx probe blocks

## Context

`start-agent.sh` has accumulated some defensive code, dead emissions, and cosmetic decoration that no longer earn their keep. This plan removes them. None of these changes alter behavior in the common case; they remove code paths that are either obsolete migrations or purely informational.

**Cross-plan status (2026-04-27)**: `plans/research-vm-isolation.md` is now implemented (commit `7392409` removed the Vane container from `start-agent.sh`, keeping SearXNG). The earlier note about folding Steps 1-3 into Phase 2 is moot — this plan now stands fully on its own.

## Goals

- Smaller, more readable `start-agent.sh`
- No regressions in steady-state behavior
- No removal of code that handles real platform variance (colima ssh quoting, HOST_IP discovery, mount drift, iptables atomicity)

## Resolved unknowns

- **Is `MODEL_COUNT=...` consumed anywhere?** No. `grep -rn MODEL_COUNT` returns only the emit site at `start-agent.sh:1154` and references in this plan. Safe to delete.
- **Has `BRIDGE_IP` ever drifted in practice?** No. The drift-check commit `3604c5f` ("fix: drift-check SearXNG outgoing proxy in existing settings.yml") was specifically a *port* migration (Squid:3128 → tinyproxy:8888), not a bridge-IP change. `BRIDGE_IP` is rediscovered every run from `docker network inspect bridge` and falls back to `172.17.0.1`. Safe to delete the drift check.

---

## Step 1: Drop SearXNG `outgoing.proxies` drift check

**Why it doesn't pull weight**: The check at `start-agent.sh:730-752` (the `else` branch of the `[[ ! -f "$SEARXNG_SETTINGS_FILE" ]]` test) was added in `3604c5f` to migrate users from an old Squid-on-port-3128 config to the current tinyproxy-on-port-8888. That migration window is closed. The seed block at lines 699-729 writes the correct value on first run, `BRIDGE_IP` rarely shifts (Docker reuses `172.17.0.1` across Colima restarts), and `--rebuild` is the user-facing recovery if it ever does.

**Action**:
- Delete lines 730-752 (the entire `else` branch including the `awk` parser, `sed` rewrite, and `SEARXNG_CONFIG_CHANGED=true`)
- Delete the `elif [[ "${SEARXNG_CONFIG_CHANGED:-false}" == "true" ]]` branch in the SearXNG container lifecycle (lines 927-929) — without the drift check, this branch is unreachable
- Resulting structure: seed-if-missing, then start container

**Files**:
- `start-agent.sh`

**Testing**:
- Run `start-agent.sh` on an existing setup with a valid `~/.claude-agent/searxng/settings.yml` — verify no behavior change
- Manually edit `settings.yml` to a wrong proxy URL, run `start-agent.sh`, verify SearXNG starts but search fails (expected — user fix is to delete the file or `--rebuild`)

---

## Step 2: Drop `MODEL_COUNT=` stderr emission

**Why it doesn't pull weight**: The Python block at `start-agent.sh:1152-1154` emits `MODEL_COUNT=N` to stderr with a comment claiming a shell wrapper consumes it. Confirmed: no wrapper in this repo (or anywhere referenced) parses it. Dead output.

**Action**:
- Delete the trailing three lines of the Python block (the comment, the `count = …` assignment, and the `print(f"MODEL_COUNT={count}", …)`)

**Files**:
- `start-agent.sh`

**Testing**:
- Run `start-agent.sh`, verify model discovery messages still appear (the `[opencode-config] discovered N models...` line above is unaffected)

---

## Step 3: Simplify in-use detection's `existing_proj` decoration

**Why it doesn't pull weight**: Lines 1230-1231 detect when an interactive bash is already attached to the container, then build a complex Go-template chain (filtering out four `.claude*` / `.config/opencode` / `.local/share/opencode` mount destinations) just to extract the project mount and decorate the warning message. The script attaches anyway regardless of what it finds — the message is purely cosmetic, and four hand-maintained exclusions are exactly the kind of code that rots when mounts are added later.

**Action**:
Two options:
- **Option A (minimal)**: Replace the Go template at line 1230 with a simple message. Drop `existing_proj` extraction; warning becomes `warning: container '$CONTAINER_NAME' appears to already have an interactive session attached. Attaching anyway; two shells sharing state may be confusing.`
- **Option B (more aggressive)**: Drop the entire in-use detection block (lines 1227-1233 — the `running_state` check and the `docker top | grep` warning). Users will figure out two shells are weird from the prompt.

Recommendation: Option A. Keeps the heads-up, drops the gnarly template.

**Files**:
- `start-agent.sh`

**Testing**:
- Open two terminal windows, run `start-agent.sh` in both pointed at the same project — verify the warning fires and both shells attach

---

## Step 4: Drop 30-day image staleness warning

**Why it doesn't pull weight**: Lines 908-915 check `$IMAGE_STAMP` (a file written at `start-agent.sh:907`) and warn if the image is >30 days old. `dockerfiles/claude-agent.Dockerfile` already has the apt staleness warning that fires per-shell when apt caches are >7 days old, which catches the more important signal at finer granularity. The image-level warning duplicates this with a coarser threshold.

**Action**:
- Delete lines 908-915 (the `elif [[ -f "$IMAGE_STAMP" ]]` branch)
- Delete the `date +%s > "$IMAGE_STAMP"` line at 907 — collapse the build branch from `if … then …; date +%s > "$IMAGE_STAMP"; fi` to plain `if … then docker build …; fi`
- Delete the `IMAGE_STAMP` constant declaration at line 189 — only used here
- Delete `rm -f "$IMAGE_STAMP"` from the `--rebuild` block at line 595

**Files**:
- `start-agent.sh`

**Testing**:
- Run `start-agent.sh` from a clean state and verify the image builds and starts
- Re-run and verify no stale `$IMAGE_STAMP` references error out
- `bash -n start-agent.sh` to check syntax

---

## Step 5: (Optional) Extract allowlist heredoc to `templates/agent-allowlist.txt`

**Why it might not pull weight (in the script)**: The heredoc at `start-agent.sh:225-510` is now ~287 lines of allowlist data inside what should be orchestration code. It bloats the script and forces readers to skip ~290 lines before getting to the next logic block. Moving it to `templates/agent-allowlist.txt` keeps the seed behavior identical but lets the script read more linearly. (The plan originally cited 155 lines; the allowlist has grown since.)

**Action**:
- Create `templates/agent-allowlist.txt` with the current heredoc content (everything between the `cat > … <<'ALLOWLIST'` and `ALLOWLIST` markers)
- Replace the heredoc block at lines 223-516 with:
  ```bash
  ALLOWLIST_TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/templates/agent-allowlist.txt"
  if [[ ! -f "$ALLOWLIST_FILE" ]] || $RESEED_ALLOWLIST; then
    cp "$ALLOWLIST_TEMPLATE" "$ALLOWLIST_FILE"
    if $RESEED_ALLOWLIST; then
      echo "==> Reseeded allowlist at $ALLOWLIST_FILE"
    else
      echo "==> Seeded allowlist at $ALLOWLIST_FILE"
    fi
  fi
  ```
- This pattern matches the existing global CLAUDE.md template handling in the same file (around the `GLOBAL_CLAUDEMD_TEMPLATE` block).

**Bonus**: If/when a research-side allowlist template emerges, both scripts converge on the same seeding pattern.

**Files**:
- `start-agent.sh` (modify)
- `templates/agent-allowlist.txt` (new)

**Testing**:
- Delete `~/.claude-agent/allowlist.txt`, run `start-agent.sh`, verify the template is copied verbatim
- Verify byte-for-byte equivalence: `diff <(old script seeded version) <(new script seeded version)`
- Run `start-agent.sh --reseed-allowlist` and verify reseed works

---

## Step 6: (Optional) Unify ollama/omlx probe blocks

**Why it might not pull weight**: The probe at lines 840-869 has two `case` arms that differ only in URL path (`/api/tags` vs `/v1/models`) and whether to send `Authorization: Bearer $OMLX_API_KEY`. They share the same retry/timeout/warning shape.

**Action**:
- Compute the probe URL and auth args once based on `$BACKEND`, call once, emit a unified warning if it fails
- Or replace the `case` block with a single helper:
  ```bash
  inference_probe() {
    local url="$1"; shift
    vm_ssh curl -sf --max-time 3 "$@" "$url" >/dev/null 2>&1
  }
  ```

**Why optional**: ~10 lines saved, mostly readability. Not load-bearing. The two warning messages are also genuinely different (different remediation steps), so a unified version still needs `case`-based branching for the message — most of the saving is collapsing the curl invocation, which is small.

**Files**:
- `start-agent.sh`

**Testing**:
- Run with each backend (`--backend=ollama` and `--backend=omlx`) against a healthy server and a stopped server — verify probe behavior matches current

---

## Notes

- **Sequencing**: Steps 1-4 are independent and can land in any order or as a single commit. If bundling, do Step 1 (drift check) before Step 4 (image staleness) so the SearXNG settings rewrite isn't wedged between two unrelated cosmetic changes in the diff.
- **Not in scope**: The CLI-flag-vs-env-var redundancy (`--memory` / `CLAUDE_AGENT_MEMORY`, etc.) looks like duplication but env vars are documented and presumably used; removing them would be a behavior change, not a simplification.
- **`SEARXNG_CONFIG_CHANGED` is fully gone** as of the Step 1 landing — no remaining references in the script or repo.
