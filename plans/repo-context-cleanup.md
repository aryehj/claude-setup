# Repo cleanup: shrink LLM context footprint

## Status

- [x] Phase 1: CLAUDE.md — collapse Key decisions to one-liners + ADR pointers
- [x] Phase 2: ADR.md — explicit Superseded headers and forward-pointers
- [x] Phase 3: Delete ROADMAP.md and its references
- [ ] Phase 4: README — replace the inline firewall smoke-test block with a pointer to `tests/test-agent-firewall.sh`

## Context

The three top-level docs are loaded into every Claude Code session whose cwd is this repo (`CLAUDE.md` always; `README.md`/`ADR.md` whenever the agent reads them, which is often). Current sizes:

- `README.md` — 631 lines
- `CLAUDE.md` — 459 lines
- `ADR.md` — 1903 lines (31 ADRs)
- `ROADMAP.md` — 26 lines, stale (still references the `/workspace` mount that was removed; one half-written "To Do")

Two specific redundancies dominate the bloat:

1. **CLAUDE.md restates ADR rationale.** The "Key decisions", "start-agent.sh key decisions", and "research.py key decisions" sections (lines ~49–417) are paragraph-long re-explanations of the corresponding ADRs. Each ends with "See ADR-NN" anyway. A model orienting in this repo doesn't need both.
2. **README inlines firewall smoke tests** (lines ~309–370) that already live in `tests/test-agent-firewall.sh`. CLAUDE.md's Layout block confirms the script covers "5 of 6 README cases + inter-container port isolation".

ADR.md is long because there are 31 dated decisions, not because individual ADRs are bloated. Bulk-deleting ADRs would erase load-bearing institutional memory. The user-confirmed scope is to mark superseded ADRs more visibly (so a reader can skip past them) without touching the bodies.

The auto-memory note `feedback_practice_proposed_format.md` applies: this plan is written in the post-ADR-032 format (no per-phase `### Files` / `### Testing`, optional `### Acceptance criteria`).

## Goals

- CLAUDE.md is a fast orienting map: layout + one-liners + ADR pointers, not a second narrative.
- ADR.md visually signals which ADRs are superseded so a reader skipping in can skim past them; bodies preserved.
- ROADMAP.md is gone (and its stale facts can no longer mislead).
- README is shorter where it duplicates a test script; otherwise unchanged.
- No source/script behavior changes. This is a docs-only pass.

## Approach

CLAUDE.md becomes the index and ADR.md becomes the rationale. Each "Key decisions" paragraph in CLAUDE.md collapses to a single line that names the decision and points at the ADR; readers who want the why click through. The structural decisions that *aren't* in an ADR (e.g., "single shared image / per-project containers") stay as one- or two-liners, since there's no other place to put them.

ADR.md edits are purely presentational: a `**Status:** Superseded by ADR-NN — <one-line summary>` header at the top of each superseded ADR. No body changes. ADR-001 and ADR-002 already say "superseded in part by" in their bodies; promote that into the header so it's visible without reading the body.

ROADMAP.md is dropped — its "Done" section is what the repo currently is, "Known Issues" mostly references behavior that no longer exists, and the "To Do" stub is empty. CLAUDE.md's Layout block loses one line.

README's six-step firewall-test block becomes one paragraph: "Run `bash tests/test-agent-firewall.sh` from inside the container; one host-side reload check (test 6) is run as `start-agent.sh --reload-allowlist`." Verify before deleting that the script actually covers tests 1–5 (CLAUDE.md says "5 of 6") — keep test 6's reload-from-host instructions inline since the script can't run that case.

## Unknowns / To Verify

- **Coverage of `tests/test-agent-firewall.sh`** vs. README's six tests. CLAUDE.md says "5 of 6 README cases" — verify by reading the script before Phase 4. If the mapping is one-to-one for tests 1–5, the README block can shrink to a pointer + the test-6 reload instructions. If the script diverges (e.g., different curl invocations or different expected exit codes), the inline block stays for tests where it differs.
- **Whether the SearXNG MCP shim section in `start-agent.sh` README** is still load-bearing as written. Out of scope for this plan — flag it if it appears stale during Phase 4 reading, but don't act on it here.

---

## Phase 1: CLAUDE.md — collapse Key decisions to one-liners + ADR pointers

### Steps

1. **Keep top-of-file structure.** Lines 1–47 (`# CLAUDE.md`, `## Layout`, `## What the script does`) stay as-is, except for the ROADMAP.md line in Layout (handled in Phase 3).
2. **Rewrite the `## Key decisions` section** (currently lines 49–168). Each `**Bold heading.**` paragraph becomes one line of the form: `- **<heading>** — <one-sentence summary>. See ADR-NN.` Drop multi-paragraph rationale entirely. Decisions without an ADR (e.g. "Single shared image, per-project containers", "`container system start` is idempotent", "`container inspect` returns `[]`") keep a one-line summary with no pointer. Group all the lines under `## Key decisions` as a single bulleted list — no sub-headers.
3. **Same treatment for `## start-agent.sh key decisions`** (lines 170–317) and `## research.py key decisions` (lines 319–417). Each `**Bold heading.**` block → one bullet, ADR pointer where one exists.
4. **Drop the "Firewall smoke tests live in README.md" line entirely** (line 313). Phase 4 turns the README block into a pointer to `tests/test-agent-firewall.sh`, so this CLAUDE.md note becomes vestigial.
5. **Keep `## Commit style`** (line 419–421) and `## Making changes` (line 423–end) verbatim. They cover behavior the model needs to act on, not rationale.

### Acceptance criteria

- A reader who has not read ADR.md can still tell, from CLAUDE.md alone, *which* decisions exist (every current `**bold heading**` survives as a bullet). They just can't tell *why* without clicking through.
- No bullet points to a non-existent ADR. Cross-check against ADR.md's `## ADR-NN:` headings.

---

## Phase 2: ADR.md — explicit Superseded headers and forward-pointers

### Steps

1. **Identify superseded ADRs.** From a re-read of `ADR.md`:
   - **ADR-001** (UV_CACHE_DIR static) — partially superseded by ADR-004.
   - **ADR-002** (allowWrite for `/tmp/uv-cache`) — partially superseded by ADR-004.
   - **ADR-016** (Vane default-on in start-agent) — Vane portion superseded by ADR-028; SearXNG portion still stands. Status header already says "Partially superseded — see ADR-028"; no change needed unless the wording is unclear.
   - **ADR-027** (Vane HTTPS-only proxy) — superseded by ADR-029. Status header already says "Superseded by ADR-029".
   - The `### 2026-04-17 revision` block inside ADR-013 is an in-place supersession — leave as-is; it's already inline.
2. **Promote partial-supersession into the Status line for ADR-001 and ADR-002.** Change `**Status:** Accepted` to `**Status:** Superseded in part by ADR-004 — dynamic `$TMPDIR` resolution replaced the static `/tmp/uv-cache` path.` (one line, inline). Bodies stay.
3. **Verify ADR-027's Status header is sufficient.** It currently reads `**Status:** Superseded by ADR-029`. Add a one-line summary so a skimmer can skip without reading: `**Status:** Superseded by ADR-029 — both `HTTP_PROXY` and `HTTPS_PROXY` are now set on `research-vane`; the HTTPS-only workaround was based on a wrong-Vane observation.`
4. **Verify ADR-016's Status header is sufficient.** Currently reads two sentences. Leave as-is — it already names the split (Vane portion superseded, SearXNG portion stands).
5. **No body edits.** Decision text, Context, Consequences all stay.

### Acceptance criteria

- A reader scrolling ADR.md can spot "this ADR is superseded; here's the pointer; here's the one-line why" within the first three lines of each superseded ADR's section.
- `grep "^\*\*Status:\*\* Superseded" ADR.md` returns one line per superseded ADR (currently 1 line; should be 3 after this phase: ADR-001, ADR-002, ADR-027).

---

## Phase 3: Delete ROADMAP.md and its references

### Steps

1. **`git rm ROADMAP.md`.**
2. **Edit CLAUDE.md's Layout block** (line 29): remove the `ROADMAP.md                   — planned work` line.
3. **Grep for `ROADMAP`** across the repo (`README.md`, `templates/`, scripts) and remove any remaining references. Best estimate: there are none, but a grep is cheap.

### Acceptance criteria

- `grep -rn ROADMAP .` returns no results outside `.git/` and `plans/` (plan filenames may legitimately contain the word).

---

## Phase 4: README — replace the inline firewall smoke-test block with a pointer

### Steps

1. **Read `tests/test-agent-firewall.sh`** and confirm it implements README tests 1–5 (default-deny, allowed-via-proxy, denied-via-proxy, Ollama carve-out, env wiring) plus inter-container port isolation. CLAUDE.md's Layout claims this is the case; verify before editing.
2. **Replace README lines ~309–370** (the `### Verifying the egress allowlist` block from "Six smoke tests exercise..." through the test-6 reload paragraph) with a shorter version:
   - One paragraph pointing at `bash tests/test-agent-firewall.sh` for tests 1–5 plus port isolation.
   - The test-6 hot-reload steps stay inline (the script doesn't run them — they require host-side `start-agent.sh --reload-allowlist`).
3. **Keep the `### Editing the allowlist` block above** (lines ~280–308) verbatim. Users edit by hand; that prose isn't a duplicate of any test.
4. **No other README edits** in this phase. Env-var consolidation, the Vane section, and the SearXNG section are out of scope — they're documentation, not duplication.

### Acceptance criteria

- README's egress-allowlist verification section is roughly one screen instead of two.
- Anything a user could previously copy-paste from the README is either still in the README (test 6's host-side reload) or in the script (`tests/test-agent-firewall.sh`).
- No instruction in the README points at a test number or label that's been removed.

---

## Notes

- **Why not delete ADRs.** The user has explicit memory feedback against deleting institutional context, and these ADRs are the only place several non-obvious facts live (e.g. ADR-014's note that SearXNG silently ignores `HTTPS_PROXY`; ADR-021's tinyproxy-vs-Squid scaling story). Marking superseded is enough to let a skimmer skip.
- **Why not also dedupe README's env-var tables.** They're split by script (start-claude / start-agent / research) which is the natural grouping; collapsing them would hurt scannability without saving meaningful context. Out of scope.
- **Risk of the CLAUDE.md collapse.** The current "Key decisions" sections sometimes carry behavioral guidance, not just rationale (e.g. "Run only one at a time" under shared `~/.claude` state). When collapsing each entry, scan for actionable instructions and either preserve them in the one-liner or move them to `## Making changes`.
- **Out of scope for this plan:** simplifying the actual scripts. `plans/simplify-start-agent.md` exists already and is unrelated.
