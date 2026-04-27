# Revise the Vane research-quality eval before Phase 3

## Status

- [x] Decision 1: Status-flag taxonomy
- [x] Decision 2: Remove `skip:no-thinking-support`
- [x] Decision 3: Uniform 4000-token thinking budget + `max_tokens=8192` + `timeout_s=1200`
- [x] Decision 4: Temperature axis → 0.2 / 0.6 / 1.0 (bracket Google's Gemma recommendation)
- [x] Decision 5: Surface `finish_reason` and `output_tokens` in cell-file frontmatter
- [x] Decision 6: Status-summary header in `MANIFEST.md`

## Context

The thinking-axis sweep (`results/thinking-20260426T200004Z-runone/`, 324 cells, 4.18 hr wall) completed without errors but contains technical artefacts that will dominate Phase 4 grading and the eventual Phase 3 winner pick:

- **122/324 cells (38%)** hit `finish_reason="length"` at the configured `max_tokens=1024`. Cells were cut mid-sentence; cross-cell comparisons are not apples-to-apples.
- **6 cells produced empty `content`** because `max_tokens` fired before the model emerged from `reasoning_content`. The manifest still labels these `ok`. All six are `gemma-4-26b-a4b-it-8bit · think=on` (q2/q5/q6 × bare/structured × t=0.3,0.7).
- The natural-stop length distribution is censored — p95 of `stop` cells is 965 and p99 is 1020, with 16/202 finishing within 64 tokens of the cap. The true uncensored ceiling is unknown from this run.
- A smoke test confirms `temperature` is being passed and parsed: 108/108 (model × prompt × query × thinking) tuples produce byte-divergent content across t∈{0.0, 0.3, 0.7}, and the prefix-share-then-diverge pattern at low T matches a softmax-on-temperature implementation.

Server-side oMLX has per-model thinking budgets that act as a hard ceiling on `reasoning_content` tokens before the model is forced to emit `content`:

- `gemma-4-E4B-it-MLX-8bit` — 4000
- `gemma-4-26b-a4b-it-8bit` (MoE) — 8000
- `gemma-4-31b-it-6bit` — 12000

Once `max_tokens > thinking_budget + content_allowance`, the thinking budget fires first, the server forces transition to `content`, and the empty-content pathology cannot occur. Runaway redraft loops are server-bounded, not model-bounded.

## Goals

- Re-run `run_thinking.py` to produce a clean 324-cell dataset before Phase 4 grading and Phase 3 winner selection.
- Make the manifest a useful at-a-glance health report, not a rubber stamp.
- Preserve the user's interactive two-prompt phase structure (one human reload per phase) — wall-clock is not a constraint.

## Decisions

### Decision 1 — Status-flag taxonomy

`run_thinking.py:_run_phase` (lines 111-118) and `lib/cells.py:write_cell_output` (lines 197-201) currently flag only `error`, `skip:no-thinking-support`, and `warn:reasoning-leaked`. Two failure modes are silently labelled `ok`:

- `finish_reason="length"` — output truncated at the cap.
- `result["text"]` empty after strip — model never emerged from reasoning.

**Resolution.** Extend the status taxonomy and apply it with explicit precedence (worst wins). The new ladder, top-down:

1. `error` — HTTP/transport failure (existing).
2. `error:no-content` — request succeeded but `content` is empty/whitespace. **New.**
3. `warn:truncated` — `finish_reason="length"` and content non-empty. **New.**
4. `warn:reasoning-leaked` — `thinking=False` cell but `reasoning_content` populated (existing).
5. `ok` — none of the above.

The two writers (`_run_phase` for the manifest row, `write_cell_output` for the per-cell frontmatter) must use the same precedence. Factor the logic into a single `lib/cells.py:classify_status(cell, result)` helper and call it from both sites.

### Decision 2 — Remove `skip:no-thinking-support`

The current heuristic (`thinking and reasoning is None ⇒ skip:no-thinking-support`, `lib/cells.py:200`) misfires on at least three cells in the existing run (E4B q2 thinkon × all temperatures × bare): the model produced full long-form responses, hit `length`, but never entered the reasoning channel for that particular prompt. The "skip" framing wrongly implies the cell is unevaluable.

**Resolution.** Delete the `skip:no-thinking-support` branch entirely. The phase-level human reload (one prompt per phase, asserting "thinking is ON for ALL these models") establishes the configuration; per-cell absence of `reasoning_content` is just a data point, not a failure. Keep `warn:reasoning-leaked` — that one is a real misconfig signal in the inverse direction.

If the user wants protection against silent oMLX misconfiguration (e.g. a stale model load), the right answer is a **fail-fast assertion at the start of `_run_phase`**: first cell of each (model, phase) must match the expected reasoning state, else raise. That's a separate, simpler safeguard than the per-cell heuristic and can land in the same revision.

### Decision 3 — Uniform 4000-token thinking budget + `max_tokens=8192` + `timeout_s=1200`

The runone's per-model thinking budgets (E4B=4000, MoE=8000, 31b=12000) made the thinking axis a confounded comparison: the bigger model wasn't just larger, it also got 3× more reasoning tokens. Uniform 4000 across all three models removes the confound and tests "best research-quality answer at a fixed reasoning budget."

**Resolution.**

- **Server-side oMLX thinking budget = 4000** for E4B, MoE, and 31b. Applied during the phase-1 human reload prompt: "configure each model with thinking ON at 4000-token reasoning budget." MoE and 31b are deliberately cut below their native budgets; any "31b underperforms" finding should be read as "31b at 4k reasoning tokens," not as a native-capability claim.
- **`max_tokens=8192`** in `lib/cells.py:call_omlx`. Caps total response = up to 4000 reasoning + at least 4192 content; comfortably above the runone's natural-content distribution (p99 of stop cells = 1020, censored, true value likely a few thousand). Empty-content pathology is impossible because `max_tokens` strictly exceeds the reasoning budget.
- **`timeout_s=1200`** (raised from 600) in `lib/cells.py:call_omlx`. Worst-case cell is 31b · think=on running to the cap: ~104s/1024 tokens × 8 ≈ 14 min, plus margin. Avoids converting `warn:truncated` cells into `error` via timeout.

### Decision 4 — Temperature axis → 0.2 / 0.6 / 1.0

Google's published recommendation for the Gemma family (2, 3, and 4) is `temperature=1.0`, `top_k=64`, `top_p=0.95`, `min_p=0.0`. The runone tested temperatures 0.0 / 0.3 / 0.7 — all *below* the recommended setting, meaning every cell was sampled outside the regime Gemma is designed for. Bracketing the recommendation gives a more honest read on best-case performance.

**Resolution.**

- Replace `_TEMPERATURES = [0.0, 0.3, 0.7]` in `run_thinking.py:42` with `[0.2, 0.6, 1.0]`.
- `top_k`, `top_p`, `min_p` are already set server-side in oMLX to Gemma-recommended values for all three models; no client-side change to `call_omlx`'s request body is needed.
- Drops greedy decoding (t=0.0). Acceptable — Gemma isn't trained for greedy, and the runone showed t=0.0 and t=0.3 outputs share long prefixes anyway, so t=0.0 was largely redundant.

### Decision 5 — Surface `finish_reason` and `output_tokens` in frontmatter

The runone analysis required grepping the embedded JSON blob in each cell file to determine truncation and length. That makes Phase 4 grading and any post-hoc analysis slower than necessary.

**Resolution.** Extend `lib/cells.py:write_cell_output`'s YAML frontmatter with two new keys:

- `finish_reason: stop|length|<other>` — pulled from `result["raw"]["choices"][0]["finish_reason"]`.
- `output_tokens: <int>` — pulled from `result["raw"]["usage"]["completion_tokens"]`.

Both default to `unknown` / `0` if the response shape is missing those fields. No analysis script changes required for this revision; the keys just become available.

### Decision 6 — Status-summary header in `MANIFEST.md`

The runone manifest forces the grader to scroll 324 rows to gauge run health. A summary block at the top makes that an at-a-glance check.

**Resolution.** In `run_thinking.py:_write_manifest`, add a "## Status summary" section between "Run configuration" and "Cells" with one line per status that occurred in the run:

```
## Status summary

- ok: N
- warn:truncated: N
- warn:reasoning-leaked: N
- error:no-content: N
- error: N
```

Statuses with zero cells are omitted. The summary uses the same precedence ladder defined in Decision 1, so `error:no-content` and `warn:truncated` will appear once Decision 1 is in place.

### Open

### Other revisions

## Files

- `tests/vane-eval/lib/cells.py` — `classify_status` helper, frontmatter additions, drop `skip:no-thinking-support`, raise `timeout_s` to 1200, raise `max_tokens` to 8192, update docstring to record uniform 4000-token thinking budget.
- `tests/vane-eval/run_thinking.py` — call `classify_status` from `_run_phase`, optional fail-fast assertion, manifest summary block.
- `tests/vane-eval/test_lib.py` — extend status-classification tests for the new taxonomy.
