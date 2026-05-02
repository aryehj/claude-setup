---
name: vane-experiments-wrapup
description: Cleanup of test-vane-models branch — delete the vane-eval harness, archive only run_thinking.py + queries as deprecated under experiments/, prune scratch from tests/, prep for low-noise merge.
type: project
---

# Vane experiments wrap-up

## Status

- [x] Phase 1: archive `run_thinking.py` + `queries.md` + ZIP archives of the two subfolders in /results  to `experiments/vane-eval/`; delete everything else under `tests/vane-eval/` (Haiku ok)
- [x] Phase 2: prune scratch from `tests/` (Haiku ok)
- [x] Phase 3: update docs + rename implemented plans
- [x] Phase 4: verify & commit

## Context

`test-vane-models` (28 commits ahead of `main`) added an OFAT eval harness under `tests/vane-eval/` and produced one Vane-pipeline run. The runs themselves are gitignored. The user's call: this was product-dev, not science — keep one deprecated reference script, drop the rest, and clean up `tests/` so it only holds product/infra tests.

State at planning time:

- `tests/vane-eval/` — committed: `run_cheap.py`, `run_thinking.py`, `run_vane.py`, `select_winners.py`, `lib/{cells,queries,__init__}.py`, `test_lib.py`, `test_run_cheap.py`, `test_run_vane.py`, `test_judge.py`, `JUDGE.md`, `queries.md`, `README.md`. Self-contained imports (`sys.path.insert(0, _HERE)`).
- `tests/vane-eval/results/` — gitignored (`.gitignore` line 30). Local-only; nothing to do in git.
- `tests/vane-eval/analysis/vane-20260429T025556Z.md` — untracked; cited by the not-yet-implemented `plans/local-research-harness.md` (lines 14, 130, 238).
- Scratch in `tests/`: 5× `session-ses_*.md`, `claude-code-prompt.md`, `opencode-prompt.md`, `walkback-checks.py` (CLAUDE.md line 26 calls it "historic debug-saga verifier; not a steady-state test"). No callers.
- `tests/` keepers: `test-agent-firewall.sh`, `test-cross-vm-isolation.sh`, `test_agent_sh.py`, `test_research.py`, `probe-denylist.sh`, `probe-vane-egress.sh`.
- Doc references to update: `README.md` lines 571–652, `CLAUDE.md` lines 17–39, `.gitignore` line 30.

## Goals

- `tests/` holds only product/infra tests.
- `experiments/vane-eval/` holds three files: `run_thinking.py` and `queries.md` and an archive of test results, plus a one-paragraph `README.md` flagging both as deprecated/unmaintained.
- The 28 vane commits already on the branch stand on their own; the cleanup is 3–4 small chores on top of them.

## Unknowns / To Verify

1. **`plans/local-research-harness.md` cites the (untracked) analysis file at `tests/vane-eval/analysis/vane-20260429T025556Z.md`.** This plan deletes the analysis file. The local-research plan will then have three broken citations (lines 14, 130, 238). Phase 3 below edits that plan to drop the broken paths but keep the substantive finding ("retrieval ceilings dominated") inline. If the user wants the analysis preserved instead, swap Phase 3's edit for a `git add` of the analysis file at a clean path (`experiments/vane-eval/findings.md`) and update the citations to point there.

---

## Phase 1: archive `run_thinking.py` + `queries.md`, delete the rest

### Steps

1. `mkdir -p experiments/vane-eval`.
2. `git mv tests/vane-eval/run_thinking.py experiments/vane-eval/run_thinking.py`.
3. `git mv tests/vane-eval/queries.md experiments/vane-eval/queries.md`.
4. Add a deprecation banner at the top of `experiments/vane-eval/run_thinking.py` (single comment block above the existing module docstring):

   ```python
   # DEPRECATED / UNMAINTAINED. Archived from tests/vane-eval/ on the
   # test-vane-models branch wrap-up. Imports its `lib/` siblings, which
   # were deleted in the same commit; the script will not run as-is.
   # Kept for reference only — see git history before this commit for
   # the runnable form.
   ```

5. Write a one-paragraph `experiments/vane-eval/README.md`:

   ```
   # vane-eval (archived, deprecated)

   `run_thinking.py` was the OFAT sweep harness used during the
   `test-vane-models` exploration. It is unmaintained and will not run
   as-is — its `lib/` helpers were removed during cleanup. `queries.md`
   is preserved as the canonical query set; future eval harnesses are
   free to reuse it.
   ```

6. Delete the remainder of `tests/vane-eval/` with one `git rm -r tests/vane-eval`. After steps 2–3, this removes: `run_cheap.py`, `run_vane.py`, `select_winners.py`, `lib/`, all `test_*.py`, `JUDGE.md`, `README.md`, the (untracked) `analysis/` and `results/` directories. Use `git rm -r --cached tests/vane-eval` followed by `rm -rf tests/vane-eval` if the untracked content blocks a plain `git rm -r`.
7. Update `.gitignore` line 30: delete `tests/vane-eval/results/` outright. The directory no longer exists; no replacement entry needed (results were one-off scratch on this branch). Keep the comment on line 29 only if the next plan adds a new results-style path; otherwise drop both lines.

### Files

- `experiments/vane-eval/run_thinking.py` (moved + deprecation banner)
- `experiments/vane-eval/queries.md` (moved)
- `experiments/vane-eval/README.md` (new, one paragraph)
- `tests/vane-eval/` (deleted)
- `.gitignore` (one line removed)

### Testing

- `ls experiments/vane-eval/` shows exactly three files.
- `ls tests/vane-eval` errors with "No such file or directory".
- `git status` shows two renames + one new file + a tree of deletions + one `.gitignore` edit.
- `uv run --with pytest pytest tests/test_research.py tests/test_agent_sh.py` still passes (untouched).

---

## Phase 2: prune scratch from `tests/`

### Steps

1. `git rm` these eight files:
   - `tests/session-ses_2497-bigqwen36.md`
   - `tests/session-ses_249b-littleqwenrun2.md`
   - `tests/session-ses_249clitteqwen.md`
   - `tests/session-ses_249d-gemma.md`
   - `tests/session-ses_249d.md`
   - `tests/claude-code-prompt.md`
   - `tests/opencode-prompt.md`
   - `tests/walkback-checks.py`

### Files

- 8 files deleted.

### Testing

- `ls tests/` shows only the keepers listed in Context.
- `git grep "session-ses_\|claude-code-prompt\|opencode-prompt\|walkback-checks"` returns matches only inside `plans/` (historic plan files; expected and left alone).

---

## Phase 3: docs + plan renames

### Steps

1. **`README.md`** — replace the "Research-quality eval harness" section (lines ~571–652) with two short paragraphs:

   ```
   ## Research-quality eval harness (archived)

   An exploratory OFAT sweep harness for grading single-turn research output
   across model / prompt / temperature / thinking-mode lived under
   `tests/vane-eval/` on the `test-vane-models` branch. The findings
   ("retrieval ceilings dominated model variation") motivated the
   `plans/local-research-harness.md` design.

   Only `experiments/vane-eval/run_thinking.py` and `queries.md` are kept;
   both are unmaintained. Do not extend this harness — start from
   `plans/local-research-harness.md` for the next iteration.
   ```

   The "Infrastructure tests" section below (lines ~654–680) stays — those scripts still test the product.

2. **`CLAUDE.md`** — layout block (lines ~17–39):
   - Drop every line under `vane-eval/` and the `walkback-checks.py` line.
   - Add a one-line `experiments/` entry below `tests/`:
     ```
     experiments/                 — archived experiments (not part of CI); see experiments/vane-eval/README.md
     ```
   - Update the comment on the `tests/` line from "unit tests and eval harness" to "unit tests and infra smoke tests".

3. **`plans/local-research-harness.md`** — remove the broken citations to the deleted analysis file:
   - Line 14: keep the sentence "The Vane-based eval … showed that retrieval ceilings dominated …" but drop the parenthetical paths and the analysis reference.
   - Line 130: drop the `Compare against tests/vane-eval/results/...` comparison instruction (the data is gone).
   - Line 238: drop the "After the eval run, write a manual analysis at `tests/vane-eval/analysis/...`" step; the new plan can decide where its own analyses live when implemented.
   - Lines 39–40, 61, 229, 234, 237, 245, 263: update `tests/vane-eval/queries.md` references → `experiments/vane-eval/queries.md`. Other `tests/vane-eval/...` references (results, analysis, layout-mirror) should be deleted, not redirected — that subtree no longer exists.

4. **Plan filename normalization** to match the existing `implemented - <slug>.md` convention:
   - `git mv plans/vane-research-eval.md "plans/implemented - vane-research-eval.md"`
   - `git mv plans/write-tests.md "plans/implemented - write-tests.md"`

### Files

- `README.md` (section rewrite)
- `CLAUDE.md` (layout block update)
- `plans/local-research-harness.md` (path-reference fixes)
- `plans/implemented - vane-research-eval.md` (renamed from `vane-research-eval.md`)
- `plans/implemented - write-tests.md` (renamed from `write-tests.md`)

### Testing

- `git grep "tests/vane-eval"` — no matches outside `plans/implemented - *` (the renamed historical plans, which are archive content and should not be rewritten).
- `git grep "walkback-checks"` — same; only historic plan files.
- The README and CLAUDE.md sections read coherently with no dangling references.

---

## Phase 4: verify & commit

### Steps

1. Sanity check final tree:
   - `ls tests/` — only product/infra tests.
   - `ls experiments/vane-eval/` — `README.md`, `run_thinking.py`, `queries.md`.
   - `git status` clean modulo `.DS_Store` / `__pycache__` (gitignored).
2. Run product tests:
   ```
   uv run --with pytest pytest tests/test_research.py tests/test_agent_sh.py
   ```
3. Commit in three atomic commits (no `Co-Authored-By` per CLAUDE.md):
   ```
   chore: archive vane-eval to experiments/, drop the rest         # Phase 1
   chore: prune scratch from tests/                                 # Phase 2
   docs: update README/CLAUDE.md and plans for new layout           # Phase 3
   ```
4. Push the branch. Merge strategy is the user's call at PR time (squash for a single commit on `main`; merge-commit to keep the chores visible).

### Files

None (verification + commits).

### Testing

- `git diff main..HEAD --stat` is dominated by deletions + two renames + a few small doc edits.

---

## Notes

- **No `experiments/` index.** With one inhabitant, an `experiments/README.md` is overkill. Add one when the second experiment lands.
- **`run_thinking.py` is intentionally non-runnable post-cleanup.** Its `lib/` is gone. The deprecation banner says so explicitly. If it ever needs to run, fish `lib/cells.py` and `lib/queries.py` out of git history.
- **Why delete the analysis writeup rather than preserve it.** The user's directive was "intermediate outputs do not need to be preserved." The findings live on through the design they motivated (`plans/local-research-harness.md`). The Unknowns section above gives the user a one-step swap if they change their mind.
