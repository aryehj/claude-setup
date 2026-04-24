# Remove Stale Claude Limitations

## Status

- [x] Phase 1: Remove 1M context block
- [x] Phase 2: Retire dead adaptive-thinking env var
- [x] Phase 3: Unpin global effort
- [x] Phase 4: Set skill model aliases
- [ ] Phase 5: Record ADR-017 capturing the policy

## Context

Three environment-level restrictions were added at various points to work around observed
Claude quality issues. Research shows two are outdated or counterproductive, and one is a
dead letter on the current model generation. A fourth item (a stale model pin in the `/plan`
skill) is a mechanical update.

**`CLAUDE_CODE_DISABLE_1M_CONTEXT=1`** (start-claude.sh:49, start-claude.sh:265-266,
dockerfiles/claude-agent.Dockerfile:15): Added with the rationale "quality degradation
observed with the larger window." No specific incident documented; no ADR. User preference
is to remove it and observe whether regressions appear in practice.

**`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1`** (start-claude.sh:50, start-claude.sh:269-270,
dockerfiles/claude-agent.Dockerfile:16): Added to address the real, widely-reported Feb–Mar
2026 issue where adaptive thinking allocated zero reasoning tokens, causing hallucinations and
unsound code changes. However, this env var is now a dead letter on Opus 4.7 (adaptive
thinking is the *only* thinking mode; the var is silently ignored). The actual lever is the
`effort` parameter, but effort defaults are model-specific and change with each release — not
something to pin globally.

**`effortLevel: medium`** (start-claude.sh:343-354, start-agent.sh:860-869): Originally set
to cap cost. Now counterproductive: it suppresses thinking across all models and overrides
model-specific defaults that Anthropic calibrates per release (currently `xhigh` for Opus
4.7, `high` for Sonnet 4.6). The effort system changes frequently enough that pinning any
value globally creates a maintenance burden and false confidence. Correct fix is to remove
the enforcement entirely and let Claude Code apply model-native defaults.

**`model: claude-opus-4-5` and `effort: high`** (skills/plan/SKILL.md:6-7): Hard-pins the
`/plan` skill to Opus 4.5 with fixed effort. No rationale documented; stale. Fix is to
switch to the `opus` alias (auto-tracks the latest Opus) and drop the effort pin.
`/cleanup` has no model set and inherits the session default; because it writes
`CLAUDE.md`/`ADR.md` prose that seeds every future session, pin it to `sonnet` (alias) so
its output quality doesn't swing with whatever the parent session happens to be using.
`/implement` is deliberately left alone — which model to implement with is a user judgment
call per task, and a session-inherited default is the right fit.

## Goals

- `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` removed from all three locations it appears
- `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` removed from all three locations it appears
- `effortLevel` enforcement removed from both scripts (all four occurrences); model-native defaults apply
- `/plan` skill: model → `opus` (alias), remove `effort: high`
- `/cleanup` skill: add `model: sonnet` (alias)
- `/implement` skill: intentionally unchanged — inherits session model
- CLAUDE.md updated to reflect removals; no effort pin documented as intentional
- `--rebuild` required to apply Dockerfile and image-baked changes; documented in plan
- ADR-017 added capturing why the three pins are gone and when re-pinning would be warranted

---

## Phase 1: Remove 1M context block

### Steps

1. In `start-claude.sh`, remove line 49 (`-e "CLAUDE_CODE_DISABLE_1M_CONTEXT=1"`) from the
   `CONTAINER_ENV=(...)` array.
2. In `start-claude.sh`, remove lines 265–266 (the two `echo` statements that write
   `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` into `/root/.bashrc` and
   `/etc/profile.d/disable-1m-context.sh`). Also remove the preceding comment on line 264
   ("# Disable 1M extended context — use standard 200K window.").
3. In `dockerfiles/claude-agent.Dockerfile`, remove line 15
   (`CLAUDE_CODE_DISABLE_1M_CONTEXT=1 \`).
4. In `CLAUDE.md`, remove the paragraph under "Key decisions" that starts with
   "**1M extended context is disabled; Claude Code uses the standard 200K window.**"

### Files

- `start-claude.sh`
- `dockerfiles/claude-agent.Dockerfile`
- `CLAUDE.md`

### Testing

After `--rebuild` on a test project, confirm `env | grep DISABLE_1M` returns nothing inside
the container.

---

## Phase 2: Retire dead adaptive-thinking env var

Mechanical cleanup. On Opus 4.7 the env var is silently ignored (adaptive thinking is the only
mode), so this should be a no-op on current-generation models. Kept separate from the effort
unpin so it can be reverted independently if anything surprising shows up.

### Steps

1. In `start-claude.sh` line 50, remove `-e "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1"` from
   `CONTAINER_ENV`.
2. In `start-claude.sh` lines 269–270, remove the two `echo` statements writing
   `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` into `.bashrc` and
   `/etc/profile.d/disable-adaptive-thinking.sh`. Remove the preceding comment on line 268
   (`# Disable adaptive thinking (extended thinking preamble).`) as well.
3. In `dockerfiles/claude-agent.Dockerfile`, remove line 16 (the
   `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` entry) **and** strip the trailing `\` from the
   previous `PATH="…"` line (line 14). After Phase 1 also removed line 15, the `PATH=` line
   becomes the last continuation in the `ENV` block; leaving the `\` there makes the `ENV`
   statement try to continue into a blank line and breaks the Docker build.
4. In `CLAUDE.md`, remove the paragraph under "Key decisions" about
   `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING`.

### Files

- `start-claude.sh`
- `dockerfiles/claude-agent.Dockerfile`
- `CLAUDE.md`

### Testing

After `--rebuild`:
- `env | grep DISABLE_ADAPTIVE` returns nothing inside the container
- `env | grep CLAUDE_CODE_DISABLE` returns nothing (both disable vars gone from Phases 1 and 2)
- Run a planning or code-change task and confirm thinking summaries still appear in the
  transcript (visible via `showThinkingSummaries: true` which is already set)

---

## Phase 3: Unpin global effort

Live behavior change: removes the `effortLevel: medium` cap from the shared global settings,
letting Claude Code apply model-native defaults (currently `xhigh` for Opus 4.7, `high` for
Sonnet 4.6). Expect longer thinking traces and higher token spend per turn.

### Steps

1. In `start-claude.sh` around lines 343–346, remove the entire `effortLevel` migration block:
   the `if data.get('effortLevel') != 'medium':` guard, the assignment, and the `print` call.
2. In `start-claude.sh` line 353, remove `"effortLevel": "medium"` from the JSON literal for
   new settings files, leaving `{"showThinkingSummaries": true, "coauthorTag": "none"}`. Also
   update the diagnostic `echo` on line 354 — drop `, effortLevel medium` so the message
   matches what was actually written.
3. In `start-agent.sh` around lines 860–862, remove the `effortLevel` migration block
   (same pattern as step 1).
4. In `start-agent.sh` line 869, remove `"effortLevel": "medium"` from the JSON literal
   (same pattern as step 2).
5. In `CLAUDE.md`, remove the paragraph under "Key decisions" about `effortLevel`. Add a single
   sentence noting that effort is intentionally unpinned — Claude Code applies model-native
   defaults which change with each release.

### Files

- `start-claude.sh`
- `start-agent.sh`
- `CLAUDE.md`

### Testing

The `settings.json` migration runs at attach time — no `--rebuild` required, but existing
sessions need to restart to pick it up.

- `python3 -c "import json; d=json.load(open('/root/.claude/settings.json')); print('effortLevel' in d)"` prints `False`
- Open a fresh session and check `/status` (or equivalent) to confirm effort is at the
  model-native default, not `medium`

---

## Phase 4: Set skill model aliases

`/plan` moves from a stale versioned pin (`claude-opus-4-5`) to the `opus` alias, which
auto-tracks the latest Opus release. `/cleanup` gets a `sonnet` alias pin — it writes
`CLAUDE.md` and `ADR.md` content that durably shapes future sessions, so its output quality
shouldn't depend on whichever model the parent session happens to be on. `/implement` is left
unchanged on purpose: picking the model for implementation is a per-task judgment call best
left to the session.

Neither skill gets an effort pin — effort is inherited from the session, so `/effort <level>`
works situationally (consistent with Phase 3's global-effort removal).

### Steps

1. In `skills/plan/SKILL.md`:
   - Line 6: change `model: claude-opus-4-5` to `model: opus`
   - Line 7: remove `effort: high` entirely (leave effort unset)
2. In `skills/cleanup/SKILL.md`:
   - Add `model: sonnet` to the frontmatter (after `argument-hint`, before `allowed-tools`)

### Files

- `skills/plan/SKILL.md`
- `skills/cleanup/SKILL.md`

### Testing

Invoke each skill and confirm the transcript shows the expected model family:
- `/plan` → Opus (current latest)
- `/cleanup` → Sonnet (current latest)
- `/implement` → whatever the session was using (sanity check — confirm no silent pin leaked in)

No rebuild needed — skill frontmatter is read at invocation time.

---

## Phase 5: Record ADR-017 capturing the policy

Written after the code changes so the ADR can describe the repo's actual end state. Scope
covers all four removed pins (1M context, adaptive-thinking env var, global `effortLevel`,
and `/plan` skill's `effort: high`) as one coherent policy: don't pin model behavior at the
environment or skill level; let Anthropic's model-native defaults apply.

### Steps

1. Append **ADR-017: Remove stale model-behavior pins** to the end of `ADR.md`, following the
   existing Context / Decision / Consequences structure used by ADR-015 and its siblings.
2. Content to cover:
   - **Context.** The four pins existed for different reasons: 1M context ("quality
     degradation observed," no documented incident); `DISABLE_ADAPTIVE_THINKING` (real Feb–Mar
     2026 issue where adaptive thinking allocated zero reasoning tokens); `effortLevel: medium`
     (cost cap); `/plan` skill's `effort: high` (undocumented). Each has since become stale,
     counterproductive, or a dead letter on current-generation models.
   - **Decision.** Remove all four. Rely on Claude Code's per-release model-native defaults.
     Use `/effort <level>` or project-local `.claude/settings.local.json` for situational
     overrides instead of global pins. Skills use alias-based model references (`opus`,
     `sonnet`) rather than versioned IDs so they auto-track Anthropic's latest release.
   - **Consequences.** Higher baseline token spend per session (model-native effort often
     exceeds `medium`). Container sessions following a model that regresses on adaptive
     thinking could reintroduce the Feb–Mar symptoms — rollback lever is `/effort <level>`
     per-session or a project-level override, **not** re-adding the env var
     (`DISABLE_ADAPTIVE_THINKING` is silently ignored on Opus 4.7+). If a future model
     release brings back the zero-token-thinking failure mode, re-pin via
     `settings.json` rather than the env var.
3. Update `CLAUDE.md` to cite ADR-017 where the removed paragraphs used to live — a single
   line under "Key decisions" pointing readers at the ADR for context.

### Files

- `ADR.md`
- `CLAUDE.md`

### Testing

- `grep -c "^## ADR-" ADR.md` increments by 1 (from 16 to 17)
- `grep "ADR-017" CLAUDE.md` returns the cross-reference line
- The ADR renders cleanly in whatever Markdown viewer the rest of `ADR.md` is read in (no
  behavioral test — this is docs)

---

## Notes

**`--rebuild` required for Dockerfile and image changes.** Phases 1 and 2 include Dockerfile
edits. Existing containers inherit image-baked env vars; only `--rebuild` applies those
changes. The `settings.json` changes in Phase 3 take effect on next run without rebuild (the
migration block runs at attach time), but the image-baked disable vars from Phases 1 and 2
will still be present until a rebuild.

**Effort is intentionally unpinned.** The effort system changes frequently — new levels added,
model-specific defaults revised with each release. Any pinned value requires active monitoring
to know when it's wrong. Removing the pin lets Claude Code apply Anthropic's current
model-native defaults. Use `/effort <level>` or `effortLevel` in a project's
`.claude/settings.local.json` for situational overrides.

**Adaptive thinking is already on for new models.** On Opus 4.7, adaptive thinking is the
only mode — `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` is silently ignored. On Opus 4.6,
removing the var restores adaptive thinking; the model-native effort default (`high`) ensures
thinking is not routinely skipped.

**Skill model aliases auto-update.** Phase 4 uses Claude Code aliases (`opus`, `sonnet`)
rather than version numbers, so the skills that *are* pinned track whatever Anthropic
designates as the current latest model in each family — no future maintenance required.
`/implement` is intentionally not pinned; it inherits the session model so the user can
pick per task. Effort is omitted from skill frontmatter across the board; skills inherit the
session effort, making `/effort <level>` work situationally.
