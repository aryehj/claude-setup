# Vane Eval

OFAT sweep harness for grading single-turn research-quality across (model,
prompt, temperature, thinking) on omlx — and, in the confirm phase, through
Vane's full retrieval pipeline. Grading is human-in-loop via Claude Code:
scripts emit one Markdown file per (cell × query); a Claude Code session
reads them against `queries.md` and writes `SCORES.md`. No grader API key
needed.

## Layout

- `queries.md` — six research queries with reference paragraphs and key facts.
- `lib/` — shared helpers (`Cell`, `call_omlx`, prompt builders, status taxonomy, query loader).
- `run_thinking.py` — interactive thinking-axis sweep against omlx (the cheap phase actually used; omlx's thinking flag is per-loaded-model, so the script prompts the human between phases).
- `run_cheap.py` — earlier OFAT sweep variant (no human-in-loop). Still functional; use whichever fits your workflow.
- `run_vane.py` — confirm phase: replays the cheap-phase winner + ablations through Vane (`POST /api/search`).
- `select_winners.py` — pick winner + ≤2 ablations from a graded run's `SCORES.md`.
- `JUDGE.md` — paste-into-Claude-Code grading prompt + rubric. Cheap-phase only; Vane-phase rubric arrives in Phase 4b.
- `results/<run-id>/` — per-run output. `MANIFEST.md` indexes the cell `.md` files; `SCORES.md` is the grader's output; `winners.json` feeds `run_vane.py`.

## Workflow

```
# 1. Cheap phase (omlx direct, no Vane)
uv run python tests/vane-eval/run_thinking.py --models a,b,c
# → tests/vane-eval/results/thinking-<UTC-ts>/

# 2. Grade
#    Open tests/vane-eval/JUDGE.md in a Claude Code session.
#    Paste the GRADING PROMPT block. Claude writes SCORES.md.

# 3. Pick winners
uv run python tests/vane-eval/select_winners.py \
    --from tests/vane-eval/results/thinking-<UTC-ts>
# → results/thinking-<UTC-ts>/winners.json (hand-edit if needed)

# 4. Vane confirm phase
uv run python tests/vane-eval/run_vane.py \
    --winners tests/vane-eval/results/thinking-<UTC-ts>/winners.json
# → tests/vane-eval/results/vane-<UTC-ts>/

# 5. Grade Vane run — Phase 4b (deferred until Vane data exists).
```

See `plans/vane-research-eval.md` for design rationale and phase status.

## Tests

```
uv run --with pytest pytest tests/vane-eval/
```

The `test_judge.py` suite checks JUDGE.md's structure (rubric axes, run-glob
hint, SCORES.md column schema matching `select_winners.parse_scores_md`).
