---
status: draft
---

# Reduce over-specification and redundancy in /plan

## Status

- [x] Phase 1: refactor `/plan` scaffold
- [x] Phase 2: align `/implement` with the new acceptance-criteria framing
- [x] Phase 3: ADR-032 capturing the rationale

## Context

`skills/plan/SKILL.md` produces plans that are reliably over-specified and bloated relative to the task at hand. Two structural drivers identified in conversation:

1. **Audience framing pushes toward over-specification.** Line 93 says "Assume whoever implements this has the plan plus the working directory and *nothing else* — no memory of this conversation, no outside knowledge of the surrounding task." That framing is functionally an instruction to restate everything an implementer might want, since the implementer is defined as context-free. CLAUDE.md, project conventions, and the working directory itself are all available to the implementer; the framing pretends they aren't.

2. **The mandatory phase scaffold (`### Steps` / `### Files` / `### Testing`) forces weight into every phase regardless of size.** In practice:
   - `### Files` duplicates path citations already in Steps.
   - `### Testing` mostly restates project test mechanism (`uv run pytest tests/`) — pure boilerplate. Genuine planning-time signal (edge cases, manual verification, "no testable behavior") is the rare exception, not the norm.
   - Step preambles re-narrate "why" already covered in plan-level Context, because each phase reads as semi-standalone.

`skills/implement/SKILL.md` is where TDD lives (step 5: tests-first, step 7: tests-pass-before-commit). Currently `/implement` derives the test surface from "the behavior this phase introduces or changes" — i.e., the implementer guesses the spec at execution time. With acceptance criteria as the explicit done-bar, `/implement` should read AC (or fall back to plan-level Goals) instead of guessing.

## Goals

- Audience reframing in `/plan` no longer pushes toward restating discoverable context.
- Phase scaffold drops `### Files` and `### Testing`; gains optional `### Acceptance criteria` only when phase-level done-state is distinct from plan-level Goals.
- Plan-level format gains an `## Approach` section as the landing site for the architectural through-line previously scattered into phase preambles.
- `/implement` keys its tests-first task off acceptance criteria (or plan-level Goals as fallback), not implementer-time guesswork.
- ADR-032 captures the reasoning so future edits to either skill don't re-introduce the dropped sections.

## Approach

This is a coordinated edit to two skill files plus one ADR. The risk is breaking the `/plan` ↔ `/implement` contract: `/implement` reads plans written under both the old and the new format (existing files in `plans/` won't be migrated). Two mitigations carry through all three phases:

- **Both skills must read both formats.** `/implement` already reads the whole plan; pointing it at AC → Goals as a graceful fallback chain (AC if present, else plan-level Goals, else infer from Steps) lets old plans keep working without rewrites.
- **Optionality is enforced by skill text, not just by absence.** "Optional" sections in skills tend to become de-facto required because the model sees the example and fills it in. The skill text needs an explicit *when not to include* clause for `### Acceptance criteria`, mirroring the existing "Omit this section only if…" pattern used for Unknowns.

The Approach section itself is the riskiest addition — it's exactly the kind of section that could become the next boilerplate trap if not carefully scoped. The skill text should frame it as "the architectural through-line across phases" with an explicit "skip this section for single-phase plans or plans where the through-line is already obvious from Goals."

## Unknowns / To Verify

None blocking. The skills sync into containers via the `start-claude.sh` / `start-agent.sh` skills-sync mechanism on `--rebuild` (see `CLAUDE.md` "Skills are synced from the upstream repo"); no separate publication step is needed.

---

## Phase 1: refactor `/plan` scaffold

### Steps

1. In `skills/plan/SKILL.md`, reframe the audience instruction in the "Write for a capable implementer" rule (currently around line 93). Replace "the contents of this plan plus the working directory (e.g., CLAUDE.md) and nothing else — no memory of this conversation, no outside knowledge of the surrounding task" with framing that affirms what *is* available: plan + working directory + CLAUDE.md + project conventions + standard tool knowledge. Keep the don't-confabulate rule intact — it's separately load-bearing. The point is to remove the implicit instruction to restate discoverable context, not to weaken the grounding rule.

2. In the same file's "Plan format" section, restructure plan-level sections to: Status / Context / Goals / **Approach** / Unknown / To Verify. Add a description for `## Approach`: "The architectural through-line across phases — the strategy that ties them together. 1–3 paragraphs. Skip this section for single-phase plans or when the through-line is already obvious from Goals."

3. In the same "Plan format" section, simplify the per-phase template. Remove `### Files` and `### Testing` entirely. Add `### Acceptance criteria` as an explicitly optional section with descriptive text: "Bullet list of what 'done' means for *this phase* when distinct from plan-level Goals. Omit when plan-level Goals already covers it. Include when there are phase-specific edge cases worth guarding, manual verification surface, or when the phase has no testable behavior (e.g., 'docs only — no code-level assertions')." The framing is acceptance criteria, not tests — `/implement` owns mechanism.

4. Update the "Steps" guidance paragraph in the same section. The current example ("add a `retry_limit` field to the pipeline config at `src/config/pipeline.ts`") is fine, but add a brief negative example of over-specification to balance the existing too-vague example — e.g., a step that re-narrates context already in the plan-level Context section.

5. Add a length / proportionality rule near the top of the Rules block: "Match plan length to task size. Sections like Approach and per-phase Acceptance criteria are optional — include them only when they carry signal the implementer can't get from the working directory or plan-level Goals. A small task should produce a small plan."

### Acceptance criteria

- A reader of the updated SKILL.md can identify, without ambiguity, when to *omit* `## Approach` and `### Acceptance criteria`.
- The audience-framing change does not weaken the existing don't-confabulate rule.

---

## Phase 2: align `/implement` with the new acceptance-criteria framing

### Steps

1. In `skills/implement/SKILL.md`, update step 5's "Tests come first" sub-bullet. Change the test target from "the behavior this phase introduces or changes" to a fallback chain: phase-level `### Acceptance criteria` if present → plan-level `## Goals` if not → derive from Steps as a last resort. Keep the existing exemptions (spikes, research, throwaway exploratory code) intact. Keep the "no testable behavior" escape hatch but note that plans may now declare this directly via Acceptance criteria (e.g., "docs only — no code-level assertions"), in which case the implementer accepts the plan's classification rather than re-deciding.

2. In step 2 ("Read the plan fully"), add a note that plans may follow either the legacy format (with `### Files` / `### Testing`) or the updated format (with optional `### Acceptance criteria`); both are valid. No migration of existing files is required.

### Acceptance criteria

- `/implement` produces correct behavior on a plan that uses the new format (AC present, no Files, no Testing).
- `/implement` produces correct behavior on a plan that uses the legacy format (Files + Testing per phase, no AC).

---

## Phase 3: ADR-032 capturing the rationale

### Steps

1. Append ADR-032 to `ADR.md` following the existing ADR template (Date / Status / Context / Decision). Title: "Reframe `/plan` around acceptance criteria; drop per-phase Files and Testing." Date `2026-04-29`, Status `Accepted`.

2. The Context section should record the two structural drivers (audience framing and mandatory scaffold) and the redundancy with `/implement`'s TDD ownership. The Decision section should enumerate the four concrete changes: audience reframing, `## Approach` added at plan level, `### Files` and `### Testing` dropped from phases, `### Acceptance criteria` added as optional. Cross-reference the corresponding `/implement` change (AC → Goals fallback chain).

3. Note in the ADR that existing plans in `plans/` are *not* migrated — `/implement` is updated to read both formats. This is the intentional escape valve that lets the change land without a flag-day rewrite.

---

## Notes

- The "optional sections" risk (optional becoming de-facto required) is the most likely failure mode. If, a few plans in, we observe `## Approach` and `### Acceptance criteria` sprouting reflexively on every plan, the next iteration is to tighten the skill text further — explicit negative examples of when *not* to include each section.
- This plan deliberately practices the new format on itself: no `### Files`, no `### Testing`, `### Acceptance criteria` only on phases where it adds signal, an `## Approach` section because the cross-phase through-line (preserving the plan↔implement contract during the format transition) genuinely needs a single landing site.
