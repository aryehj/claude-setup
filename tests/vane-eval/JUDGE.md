# JUDGE — Vane Eval Grading Harness

This file is the human entry point for grading a cheap-phase / thinking-phase
eval run. The cells in `tests/vane-eval/results/{thinking,cheap}-<UTC-ts>/`
were emitted by `run_thinking.py` (or `run_cheap.py`) hitting omlx directly —
no Vane in the loop, no citations.

The grading itself is done by Claude inside a Claude Code session: open this
file, copy the **GRADING PROMPT** below, paste it into the chat, and Claude
reads the cells against the references in `queries.md` and writes
`SCORES.md` next to the cell files. From there, `select_winners.py` picks the
winner.

> **Phase 4b** will extend this rubric with citation grading for Vane-phase
> runs (`results/vane-<UTC-ts>/`). For now, ignore Vane runs even if
> present — they have no `SCORES.md` schema yet.

---

## How to use

1. Open this file (`tests/vane-eval/JUDGE.md`) in a Claude Code session at the
   repo root. No other priming context is needed.
2. Copy everything inside the **GRADING PROMPT** fenced block below and paste
   it into the chat. Claude will locate the most recent run dir, read every
   cell file, read the references in `queries.md`, and write `SCORES.md` in
   the same run dir.
3. After grading, run `uv run python tests/vane-eval/select_winners.py
   --from tests/vane-eval/results/<run-dir>` to produce `winners.json`.

---

## GRADING PROMPT

```
You are grading a research-quality eval run. Your output goes to a
SCORES.md file that will be parsed by select_winners.py.

STEP 1 — Locate the run.

Glob the most recent non-Vane run directory under
`tests/vane-eval/results/`. Match either prefix:

    tests/vane-eval/results/thinking-*/MANIFEST.md
    tests/vane-eval/results/cheap-*/MANIFEST.md

Pick the lexicographically latest one (timestamps in the dir name sort
correctly). Read its `MANIFEST.md` to enumerate every cell file in the
run.

STEP 2 — Read references.

Read `tests/vane-eval/queries.md` in full. Each query has a one-paragraph
**Reference** answer plus a bulleted **Key facts** list. Use these as the
floor of correctness, not a ceiling — see the rubric note about
correct-but-unanticipated answers below.

STEP 3 — Read every cell.

For each cell file the manifest lists, read the file. The relevant
sections are `## Query`, `## Reference` (already in the file for
convenience), `## Response`, and the YAML frontmatter (model,
prompt_style, temperature, thinking, latency_s, status, finish_reason,
output_tokens). Skip cells whose status starts with `error`.

STEP 4 — Score each cell on three axes (1–5 each).

  • coverage:     how many of the query's Key facts the response hits,
                  and whether it covers the territory the reference
                  paragraph implies.
                  5 = hits ≥90% of key facts plus relevant adjacent
                      material.
                  3 = hits the obvious half but misses subtler points.
                  1 = mostly off-topic or obviously incomplete.

  • accuracy:     factual correctness of the claims that ARE made.
                  5 = no detectable factual errors.
                  3 = one or two minor slips (off-by-one dates, swapped
                      units, etc.).
                  1 = central claim is wrong.

  • succinctness: signal density — does the response say it once,
                  clearly, without padding or self-narration?
                  5 = tight, no filler, no "as an AI…".
                  3 = noticeable padding but still readable.
                  1 = wall of restated questions, hedges, or list-of-
                      lists scaffolding.

The cheap/thinking phase has no citations, so there is NO citation
column in this rubric. Total is /15.

IMPORTANT — credit correct-but-unanticipated answers.

The reference paragraph reflects the eval author's prior knowledge. If a
response gives correct-and-relevant material the reference does not
mention, count that toward coverage. The reference is a floor, not a
ceiling. Penalise only when the response is wrong, not when it goes
beyond what was anticipated.

STEP 5 — Emit SCORES.md.

Write `SCORES.md` to the same run dir you read MANIFEST from. The body
must be a single Markdown table sorted by total descending. Use exactly
these column names (case-insensitive but spelled this way):

| file | label | model | prompt_style | temperature | thinking | coverage | accuracy | succinctness | total |

Where:
  - file: the cell .md filename, relative to the run dir, no leading slash.
  - label: copy the `label:` field from the cell's frontmatter.
  - model, prompt_style, temperature, thinking: from frontmatter.
  - coverage, accuracy, succinctness: integers 1–5 from your scoring.
  - total: integer sum of the three (max 15).

Do NOT include an extra "citation" column for cheap-phase rows.

STEP 6 — Wash-up.

After the table, write a `## Wash-up` section with short paragraphs:

  - Which axis dominated the ranking — model, prompt_style, temperature,
    or thinking?
  - Which model won, and was the win clean across queries or noisy?
  - Did prompt_style and temperature interact (e.g. structured + low
    temp clearly best, but research_system flipped at high temp)?
  - Did thinking=on help, or just spend latency without measurable
    quality gain?

Keep each paragraph 2–4 sentences. This section is for the human; it is
not parsed.

OUTPUT — write SCORES.md and stop. Do not also produce winners.json
(that's select_winners.py's job).
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
