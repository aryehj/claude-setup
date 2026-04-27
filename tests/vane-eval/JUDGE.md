# JUDGE — Vane Eval Grading Harness

This file is the human entry point for grading a cheap-phase / thinking-phase
eval run. The cells in `tests/vane-eval/results/{thinking,cheap}-<UTC-ts>/`
were emitted by `run_thinking.py` (or `run_cheap.py`) hitting omlx directly —
no Vane in the loop, no citations.

The grading itself is done by Claude inside a Claude Code session: open this
file, copy the **GRADING PROMPT** below, paste it into the chat, and Claude
dispatches a fan-out of grading subagents that produce `SCORES.md` next to
the cell files. From there, `select_winners.py` picks the winner.

> **Phase 4b** will extend this rubric with citation grading for Vane-phase
> runs (`results/vane-<UTC-ts>/`). For now, ignore Vane runs even if
> present — they have no `SCORES.md` schema yet.

---

## Why this prompt fans out instead of grading inline

A typical run is 6 queries × 3 models × 3 prompt styles × 3 temperatures × 2
thinking states = **324 cell files**, each ~12 KB. Reading all of them in
one agent costs ~950K tokens of input — well over a single Sonnet/Opus
session's context, even before the rubric work. A previous attempt by Sonnet
that improvised mid-run partitions ran out of context twice (subagent
overflows, then dispatcher overflow from verbose subagent returns).

The prompt below pre-bakes the partition and the I/O contract so the
dispatcher never reads cells, and so subagent returns stay one line each:

- **Partition** by `(query, model)` → 18 slices of ~18 cells (~55K tokens
  each), well within budget.
- **Subagents write rows directly to disk** as `SCORES-<query>-<model>.md`
  partials — they do NOT return the rows in their reply.
- **Subagents return a single status line** ("done: N rows written" or
  "blocked: ..."). Nothing else.
- **Main agent assembles** the partials with a Bash `cat` + `sort`, then
  reads only the small final `SCORES.md` to write the wash-up.

This keeps the dispatcher's working set tiny (MANIFEST + queries.md +
final assembled table) regardless of run size.

---

## How to use

1. Open this file (`tests/vane-eval/JUDGE.md`) in a Claude Code session at
   the repo root. Sonnet 4.6 is sufficient; Opus is fine too. No other
   priming context is needed.
2. Copy everything inside the **GRADING PROMPT** fenced block below and
   paste it into the chat.
3. Claude will: locate the latest run dir, read `MANIFEST.md` and
   `queries.md`, dispatch ~18 parallel subagents (one per query × model),
   wait for partials, assemble `SCORES.md`, and append a wash-up.
4. After grading, run `uv run python tests/vane-eval/select_winners.py
   --from tests/vane-eval/results/<run-dir>` to produce `winners.json`.

Expected wall time: a few minutes once subagents start; throttled mostly
by parallel-agent fan-out, not by Read calls.

---

## GRADING PROMPT

```
You are grading a research-quality eval run. Your output is a SCORES.md
file (Markdown table + wash-up) that select_winners.py will parse. There
are ~324 cell files in the run; reading them all in this main agent will
exhaust context. The plan below partitions cell reading into parallel
subagents so the dispatcher never reads cells. Follow it as written.

STEP 1 — Locate the run.

Glob the most recent non-Vane run directory under
`tests/vane-eval/results/`. Match either prefix:

    tests/vane-eval/results/thinking-*/MANIFEST.md
    tests/vane-eval/results/cheap-*/MANIFEST.md

Pick the lexicographically latest one (timestamps in the dir name sort
correctly). Record its absolute path; you will pass it to subagents.

STEP 2 — Load references and the sweep dimensions.

Read:
  - `tests/vane-eval/queries.md` (full file — small).
  - `<run_dir>/MANIFEST.md` to learn the swept models, prompt styles,
    temperatures, query slugs, and total cell count.

Do NOT read individual cell files in this main agent. Cells average
~12 KB each; reading more than a few dozen will eat your context. Cell
reading is exclusively delegated to subagents in STEP 3.

STEP 3 — Dispatch grader subagents (parallel, partition by query × model).

For every (query_id, model) pair in the sweep — typically 6 queries ×
3 models = 18 pairs — spawn ONE Agent (subagent_type=general-purpose).
Send all 18 in a SINGLE message so they run concurrently. Use the
SUBAGENT PROMPT template below; substitute the per-slice values.

Each slice has prompt_styles × temperatures × thinking_states cells
(typically 3 × 3 × 2 = 18 cells, ~55K tokens of cell content). That fits
comfortably in a subagent's context.

If MANIFEST shows a different sweep shape (e.g. 4 models, 4 temperatures),
keep the same partition rule — one subagent per (query, model) — and let
the per-slice cell count grow or shrink. If a single slice would still be
> ~80 cells, partition further by (query, model, prompt_style); otherwise
do not subdivide.

────────────── SUBAGENT PROMPT (template — substitute every <…>) ─────
Grade Vane-eval cell files for query "<query_id>" against model
"<model>". Score using the rubric below; write rows to a file; return
only a single-line ack.

Run dir (absolute): <run_dir>
Cells to grade: every file matching this glob, no exceptions —
    <run_dir>/<query_id>_<model>_-_*.md
You can enumerate them with `ls <run_dir>/<query_id>_<model>_-_*.md`.
Expect ~<expected_count> files.

REFERENCE for <query_id>:

  Query:
    <verbatim Query text from queries.md>

  Reference paragraph:
    <verbatim Reference paragraph>

  Key facts:
    - <fact 1>
    - <fact 2>
    ...

The reference is a FLOOR, not a ceiling. Credit correct-and-relevant
material that goes beyond the listed key facts. Penalise wrongness, not
unanticipated correctness.

RUBRIC (each axis 1–5; no citation column for cheap/thinking phase):

  • coverage:     how many of the query's key facts the response hits,
                  plus relevant adjacent material the reference implies.
                  5 = ≥90% of key facts plus relevant adjacent material.
                  3 = obvious half but misses subtler points.
                  1 = mostly off-topic or obviously incomplete.

  • accuracy:     factual correctness of claims actually made.
                  5 = no detectable errors.
                  3 = one or two minor slips (off-by-one date, swapped
                      unit, etc.).
                  1 = central claim is wrong.

  • succinctness: signal density.
                  5 = tight, no filler, no self-narration.
                  3 = noticeable padding but readable.
                  1 = wall of restated questions, hedges, list-of-lists
                      scaffolding.

PROCESS for each cell file:
  1. Read it.
  2. Skip cells whose `status:` frontmatter starts with "error" — emit
     no row for them.
  3. Read the YAML frontmatter and the `## Response` section. Ignore
     `## Query` and `## Reference` (they're echoed for convenience).
  4. Score coverage / accuracy / succinctness as integers 1–5.
  5. total = coverage + accuracy + succinctness  (max 15).
  6. Build ONE Markdown table row in this exact column order:

     | <file> | <label> | <model> | <prompt_style> | <temperature> | <thinking> | <coverage> | <accuracy> | <succinctness> | <total> |

     - file: cell .md filename only — no leading slash, no run-dir
       prefix, no surrounding whitespace beyond a single space.
     - label: copy the frontmatter `label:` field verbatim.
     - model, prompt_style, temperature: from frontmatter.
     - thinking: lowercase string "true" or "false".
     - coverage, accuracy, succinctness, total: integers.

OUTPUT (write a file, then return one line):

  Write all rows — concatenated with newlines, NO header, NO separator,
  NO surrounding prose, NO commentary, NO wash-up — to:

      <run_dir>/SCORES-<query_id>-<model_slug>.md

  where <model_slug> is the model name with every "/" replaced by "_".
  The file must contain ONLY table rows, one per non-error cell.

  Then return EXACTLY one line of text and stop:

      done: <N> rows written to SCORES-<query_id>-<model_slug>.md

  …or, if you couldn't complete:

      blocked: <one-sentence reason>

  Do NOT include the rows in your reply. Do NOT include any analysis,
  per-cell notes, or wash-up. The dispatcher will assemble the final
  SCORES.md from the partial files.
────────────────────────────────────────────────────────────────────

After dispatch, wait for all subagent acks. If any return `blocked:` or
write fewer rows than expected, re-dispatch ONLY those slices with a
clarification — never re-grade slices that already succeeded.

STEP 4 — Reconcile partials into SCORES.md.

From the run dir, run a Bash one-liner to assemble the partials. The
header line below is what `select_winners.py` parses; do not change
column names or order.

    cd <run_dir> && {
      printf '| file | label | model | prompt_style | temperature | thinking | coverage | accuracy | succinctness | total |\n'
      printf '|------|-------|-------|--------------|-------------|----------|----------|----------|--------------|-------|\n'
      cat SCORES-q*-*.md | sort -t'|' -k11,11 -b -nr
    } > SCORES.md

Verify:
  - `wc -l <run_dir>/SCORES.md` should be 2 (header + separator) +
    (total cells from MANIFEST minus any `status:error` cells).
  - The first data row's `total` should be the largest in the table.

If verification fails, do NOT silently continue — print which subagent
file looks malformed and re-dispatch just that slice.

Once verified, delete the partials so the run dir stays clean:

    rm <run_dir>/SCORES-q*-*.md

STEP 5 — Wash-up.

Read the assembled `SCORES.md` (now small enough to fit — ~50 KB at
324 rows). Append a `## Wash-up` section with short paragraphs, 2–4
sentences each:

  - Which axis dominated the ranking — model, prompt_style, temperature,
    or thinking?
  - Which model won, and was the win clean across queries or noisy?
  - Did prompt_style and temperature interact (e.g. structured + low
    temp clearly best, but research_system flipped at high temp)?
  - Did thinking=on help measurably, or just spend latency?
  - Note any cells whose status is `warn:reasoning-leaked` so the human
    can re-check the omlx model load. Do not score them differently.

This section is for the human; it is not parsed.

OUTPUT — write SCORES.md (table + wash-up) and stop. Do NOT also produce
winners.json — that is select_winners.py's job.
```

---

## Rubric details

### Coverage (1–5)

How well the response hits the **Key facts** for the query, and whether it
covers the territory the reference paragraph implies. Going beyond the
reference with correct-and-relevant material is a positive signal, not a
negative one — the reference is a *floor*, not a *ceiling*.

> **5 example (Q1, tritium):** names half-life ≈ 12.32 y, beta decay to
> He-3, tritium-illuminated EXIT signs and watch dials, tracer use in
> hydrology, and D-T fusion fuel. Mentions tritium-labeled biomedical
> tracers as an unanticipated extra → still counts toward 5.
>
> **1 example:** answers about deuterium, mentions only fusion, omits
> half-life entirely.

### Accuracy (1–5)

Factual correctness of the claims actually made. A response that is
narrowly scoped but right scores higher on accuracy than a sprawling one
with a single confident error.

> **5 example:** every concrete claim — dates, half-lives, model
> capacities, units — checks out against external sources.
>
> **1 example:** confident but wrong central claim (e.g. "Iron Curtain
> speech given by Eisenhower in 1949"). One such error caps accuracy at 1
> regardless of how clean the surrounding prose is.

### Succinctness (1–5)

Signal density. Does the response say it once, clearly, without padding,
self-narration, or stacks of bulleted scaffolding around a thin core?

> **5 example:** answers in a paragraph plus a tight bullet list of key
> facts, no preamble, no "let me explain…", no "in conclusion…".
>
> **1 example:** restates the question, opens with "That's a great
> question…", lists the same three points in three different formats,
> closes with a generic disclaimer.

---

## Notes for future you

- The cheap/thinking phase has no citations. If you find yourself wanting to
  judge "where did this fact come from", that's the Vane-phase rubric — see
  Phase 4b in `plans/vane-research-eval.md`.
- Latency is recorded per cell but is **not** in the rubric. Treat it as
  context for the wash-up only ("model X won but cost 4× the latency"); do
  not deduct points for slow cells.
- The `warn:reasoning-leaked` status means the human said thinking was OFF
  but `reasoning_content` came back populated — flag those in the wash-up so
  the human can re-check the omlx model load. Do not score them differently.
- If the `(query, model)` partition still overflows a subagent (e.g. the
  sweep grew to 5 prompt styles × 5 temps × 2 thinking = 50 cells per
  slice), subdivide further by `(query, model, prompt_style)`. Don't
  improvise mid-run — partition before dispatching, so every subagent gets
  a slice it can fit in one shot.
- If a subagent returns `blocked: …`, re-dispatch only that slice. Never
  re-grade slices that already produced a partial — duplicates will break
  the assembled table.
