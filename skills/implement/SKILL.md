---
name: implement
description: Execute a plan written by /plan — work through the active phase with tasks, atomic commits, and test suggestions
disable-model-invocation: true
argument-hint: "<plan file path or slug>"
allowed-tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - Agent
  - AskUserQuestion
  - TaskCreate
  - TaskList
  - TaskUpdate
  - TaskGet
  - WebSearch
  - WebFetch
---

The user wants you to implement a plan previously written by `/plan`. $ARGUMENTS points to the plan — a path, filename, or slug under `plans/`. Your job is to execute the *active phase* (the first unchecked entry in the plan's Status checklist) carefully, with appropriate task tracking and commits.

## Process

1. **Locate the plan.** Resolve $ARGUMENTS to a file under `plans/`. If ambiguous (multiple matches) or missing, list candidates and ask the user which one via AskUserQuestion. If $ARGUMENTS is empty, list unimplemented plans (files in `plans/` not prefixed with `implemented`) and ask which to run.

2. **Read the plan fully.** Read the entire plan file, not just the active phase — context, goals, unknowns, and later phases all inform how to do the current phase well. Identify the *active phase*: the first unchecked `- [ ]` entry in the Status checklist. If all phases are checked, tell the user the plan looks complete and stop.

3. **Clarify before committing to an approach.** Use AskUserQuestion for anything genuinely ambiguous about the active phase: unresolved items in the plan's Unknowns section that block this phase, decisions the plan punted to implementation time, or assumptions you'd otherwise have to guess at. Do not ask questions the plan already answers. Do not ask cosmetic questions. Batch questions in a single AskUserQuestion call when possible — don't drip-feed. If the phase is straightforward and unambiguous, skip this step.

4. **Sanity-check the phase scope before starting.** If the active phase looks like it would obviously not fit in one session — many files, many VM/network roundtrips, many independent decisions — stop and propose a sub-phase split to the user. Do not try to force an oversized phase through. Running out of context mid-phase is the failure mode this skill exists to prevent.

5. **Plan the tasks. TaskCreate is mandatory.** Before doing any edits, create a task list via TaskCreate with one task per numbered step in the plan's active phase (or per meaningful unit of work if the plan isn't numbered). This is non-negotiable — even for short phases. The task list is how the user sees progress in the TUI; skipping it is skipping their visibility.
   - If you hit a factual unknown (library behavior, API shape, config option) that would take more than a quick grep to resolve, add a **research task** or **spike task** before the dependent work. Mark them clearly in the title (e.g., "Research: does X library support Y?").

6. **Work the tasks.** Mark each task in-progress before starting it and completed immediately when done — do not batch status updates. If a task reveals new work, add tasks rather than expanding the current one silently. If the plan turns out to be wrong about something load-bearing, stop and surface it to the user before forging ahead.

7. **Commit at checkpoints, not just at the end.** After completing a task or group of tasks that represents a coherent, potentially-revertible unit, make a git commit. Mid-phase checkpoint rule: **if you have substantive edits across more than ~3 files with nothing committed, stop and commit a checkpoint before continuing.** This bounds the blast radius of running out of context and gives the user a recoverable state.
   - Bundle trivial changes; don't commit every tiny edit.
   - Don't commit broken / half-finished states.
   - Follow the project's commit style (check recent `git log` for tone; respect CLAUDE.md instructions such as "no Co-Authored-By lines").
   - Do not push.

8. **Before declaring the phase done, the working tree must be clean — or every dirty file explicitly accounted for.** Run `git status` at the end. Either commit the remaining changes, or in your final report list every uncommitted file with a one-line reason for leaving it (e.g., "unrelated to this phase, found incidentally", "needs user decision before commit"). Do not silently end with a dirty tree.

9. **Update the plan's Status checklist.** When the active phase is complete, mark its checkbox `[x]` in the plan file. Do not mark later phases. Do not rename the plan file — that's `/cleanup`'s job.

10. **Report and suggest tests.** End your turn with a short summary of what changed and a **Testing suggestions** section. Assume you may not be able to run the tests yourself (no dev server, no credentials, UI changes, external services). For each meaningful behavior changed, give the user a concrete way to verify it: exact command, URL to hit, UI flow to click through, or log line to look for. If you *did* run tests, say which ones and what passed; still list any verifications that require human eyes or external systems.

## Rules

- One phase per invocation. Do not silently steamroll into the next phase. If the user wants the next phase, they will invoke `/implement` again.
- Respect the plan. The plan is the source of truth for what to build. If you disagree with it, say so and ask — don't quietly deviate.
- Don't over-scope. Resist bundling in unrelated cleanup, refactors, or "while I'm here" changes. Those belong in `/cleanup` or a separate task.
- Ground before guessing. When a step depends on external facts (library versions, API shapes), verify with Read/Grep/Bash/WebFetch rather than writing confident-looking placeholder code.
- Surface blockers early. If a task reveals that the plan is wrong or a phase is not implementable as written, stop and tell the user before writing code against a broken premise.
- Do not run `/cleanup`. Housekeeping (CLAUDE.md, README.md, ADR.md, renaming the plan file) is a separate skill the user invokes when the whole plan is done.
