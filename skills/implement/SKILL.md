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
---

The user wants you to implement a plan previously written by `/plan`. $ARGUMENTS points to the plan — a path, filename, or slug under `plans/`. Your job is to execute the *active phase* (the first unchecked entry in the plan's Status checklist) carefully, with appropriate task tracking and commits.

## Process

1. **Locate the plan.** Resolve $ARGUMENTS to a file under `plans/`. If ambiguous (multiple matches) or missing, list candidates and ask the user which one via AskUserQuestion. If $ARGUMENTS is empty, list unimplemented plans (files in `plans/` not prefixed with `implemented`) and ask which to run.

2. **Read the plan fully.** Read the entire plan file, not just the active phase — context, goals, unknowns, and later phases all inform how to do the current phase well. Identify the *active phase*: the first unchecked `- [ ]` entry in the Status checklist. If all phases are checked, tell the user the plan looks complete and stop.

3. **Clarify before committing to an approach.** Use AskUserQuestion for anything genuinely ambiguous about the active phase: unresolved items in the plan's Unknowns section that block this phase, decisions the plan punted to implementation time, or assumptions you'd otherwise have to guess at. Do not ask questions the plan already answers. Do not ask cosmetic questions. Batch questions in a single AskUserQuestion call when possible — don't drip-feed. If the phase is straightforward and unambiguous, skip this step.

4. **Plan the tasks.** Decide whether the active phase needs to be broken down:
   - If the phase is a single small edit, skip task tracking — just do it.
   - If the phase spans multiple files, involves non-trivial logic, or has ordering constraints, create a task list via TaskCreate. One task per meaningful unit of work, not per line of code.
   - If you hit a factual unknown (a library behavior, API shape, config option) that would take more than a quick grep to resolve, add a **research task** or **spike task** before the dependent work. Research tasks read docs / probe the system; spike tasks write throwaway code to verify behavior before committing to a real implementation. Mark them clearly in the task title (e.g., "Research: does X library support Y?").

5. **Work the tasks.** Mark each task in-progress before starting it and completed immediately when done — do not batch status updates. If a task reveals new work, add tasks rather than expanding the current one silently. If the plan turns out to be wrong about something load-bearing, stop and surface it to the user before forging ahead.

6. **Commit atomically for risky changes.** After completing a task or group of tasks that represents a *coherent, potentially-revertible unit of risk*, make a git commit. Heuristics for what warrants its own commit:
   - Anything that could plausibly need to be reverted independently (a schema change, a dependency bump, a refactor that touches many files).
   - A checkpoint before a risky next step you'd rather not have to re-do if it goes wrong.
   - The completion of a sub-goal that stands on its own.
   Do not commit every tiny edit — bundle trivial changes. Do not commit broken / half-finished states. Follow the project's commit style (check recent `git log` for tone; respect any instructions in CLAUDE.md such as "no Co-Authored-By lines"). Do not push. Do not commit at all if the user's workflow clearly expects a single final commit, if the repo has uncommitted unrelated changes that a commit would sweep up, or if you're mid-phase and nothing has reached a revertible checkpoint yet.

7. **Update the plan's Status checklist.** When the active phase is complete, mark its checkbox `[x]` in the plan file. Do not mark later phases. Do not rename the plan file — that's `/cleanup`'s job.

8. **Report and suggest tests.** End your turn with a short summary of what changed and a **Testing suggestions** section. Assume you may not be able to run the tests yourself (no dev server, no credentials, UI changes, external services). For each meaningful behavior changed, give the user a concrete way to verify it: exact command, URL to hit, UI flow to click through, or log line to look for. If you *did* run tests, say which ones and what passed; still list any verifications that require human eyes or external systems.

## Rules

- One phase per invocation. Do not silently steamroll into the next phase. If the user wants the next phase, they will invoke `/implement` again.
- Respect the plan. The plan is the source of truth for what to build. If you disagree with it, say so and ask — don't quietly deviate.
- Don't over-scope. Resist bundling in unrelated cleanup, refactors, or "while I'm here" changes. Those belong in `/cleanup` or a separate task.
- Ground before guessing. When a step depends on external facts (library versions, API shapes), verify with Read/Grep/Bash/WebFetch rather than writing confident-looking placeholder code.
- Surface blockers early. If a task reveals that the plan is wrong or a phase is not implementable as written, stop and tell the user before writing code against a broken premise.
- Do not run `/cleanup`. Housekeeping (CLAUDE.md, README.md, ADR.md, renaming the plan file) is a separate skill the user invokes when the whole plan is done.
