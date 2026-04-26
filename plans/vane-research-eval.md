# Vane Research-Quality Eval

## Status

- [x] Phase 1: Query set, references, shared helpers
- [x] Phase 2: Cheap-phase OFAT sweep against omlx
- [x] Phase 3: Vane API confirm sweep
- [ ] Phase 4: Grading harness for human-in-loop Claude Code judge

## Context

`research.py` runs Vane (a Perplexica fork, image `itzcrazykns1337/vane:slim-latest`) at `http://localhost:3000`, with SearXNG-backed search and an LLM endpoint pointed at omlx on the macOS host (default `http://host.docker.internal:8000/v1`). Prior model evaluation in this repo is hand-graded transcript review (`tests/session-*.md`) against OpenCode — not Vane, and not systematic. The user wants a small, automatable test that varies four factors (model, prompt wording, temperature, thinking mode) and reports which combination produces the most useful research output.

The user picked a deliberately cheap grading loop: scripts emit one Markdown file per (cell × query); the human opens those files in a Claude Code session and lets the on-screen Claude grade them by reading both the cell outputs and a reference paragraph the user wrote when authoring the query set. No API-key wiring.

The user also picked a two-phase design: a cheap phase that hits the local LLM directly (no Vane in the loop) sweeps the matrix; a smaller confirmation phase replays the cheap phase's *winner* plus one or two ablations through Vane's full pipeline (SearXNG → scrape → cite → answer) to check the winning settings still win once Vane is in the path.

Relevant files:

- `research.py:51-92` — Vane container constants and the `vane_data_dir` path.
- `tests/probe-vane-egress.sh` — existing pattern for `colima ssh -p research -- docker inspect research-vane …`.
- `tests/session-*.md` — prior manual model-eval transcripts (different surface; useful only as a tone reference for the query set).

## Goals

- Test harness lives at `tests/vane-eval/`. Self-contained; one Python file per phase plus a small `lib/`.
- Six research-style queries with one-paragraph reference answers checked into the repo.
- Cheap phase (`run_cheap.py`) sweeps four factors **OFAT** (one factor at a time) — total cells bounded to ≤ 90.
- Vane phase (`run_vane.py`) replays the user-selected winner plus 1–2 ablations on a 3-query subset.
- Grading is one human action per phase: open `JUDGE.md` in a Claude Code session and paste the embedded grading prompt.
- Re-runnable: timestamped run dirs under `tests/vane-eval/results/`, no clobber.

## Unknowns / To Verify

These are factual unknowns the implementer must resolve in the listed phase before writing the dependent code. Hedging beats fabrication — do not invent specifics for any of these.

1. **Vane HTTP API surface** — path, request body, response shape. Vane is a Perplexica fork; Perplexica documents `POST /api/search` with `chatModel`, `query`, `optimizationMode`, `focusMode`, `history`, `systemInstructions` — but this image (`itzcrazykns1337/vane:slim-latest`) may differ. Verify by reading the running container's route source: `colima ssh -p research -- docker exec research-vane sh -c 'ls /app && grep -rln "/api/search\\|/api/chat" /app/src 2>/dev/null | head'`. Affects Phase 3 design.
2. **Whether Vane's API forwards `temperature` and a thinking flag.** If not, Phase 3 cannot sweep those axes via API alone — fall back to writing them into Vane's config under `~/.research/vane-data/` and restarting the container between cells. Affects Phase 3.
3. **omlx's parameter for Qwen3 thinking mode.** Likely `chat_template_kwargs: {enable_thinking: bool}` in the chat-completions body, but verify with one probe call before coding the helper. Affects Phase 1's `lib/cells.py` and Phase 2.
4. **omlx model discovery endpoint shape.** Confirm `GET http://host.docker.internal:8000/v1/models` returns OpenAI-style `{data: [{id: …}, …]}`. Affects Phase 1.
5. **The user's model shortlist.** The user said "auto-discover + a shortlist I'll provide." Treat the shortlist as user-supplied at Phase 2 invocation time (`--models a,b,c`). If the flag is omitted, auto-discover and use everything. If a shortlist entry is missing from `/v1/models`, fail loud and name the missing model.

---

## Phase 1: Query set, references, shared helpers

### Steps

1. Create `tests/vane-eval/` and `tests/vane-eval/lib/`.
2. Author `tests/vane-eval/queries.md` with six queries spanning: 1 factual lookup, 2 multi-hop synthesis, 1 domain/technical, 1 current-events-style, 1 open-ended/judgment. For each, write a 1-paragraph reference answer (5–10 lines) plus a bulleted "key facts" list the judge should check for. Format: `## Q1: <slug>` H2 per query, followed by `**Query:**`, `**Reference:**`, `**Key facts:**` blocks. Slugs are stable identifiers (`q1`–`q6`) used as filename fragments downstream.
3. Resolve Unknown #4: probe `GET {OMLX_BASE}/v1/models` (one-shot script or `httpx` REPL) and confirm response shape before writing the helper.
4. Resolve Unknown #3: make one POST to `{OMLX_BASE}/v1/chat/completions` against a Qwen3 model with both `chat_template_kwargs={"enable_thinking": true}` and (separately) a top-level `enable_thinking: true` field. Note which one produces a `<think>…</think>` block; that becomes the supported parameter path. Capture this in a comment at the top of `cells.py`.
5. Create `tests/vane-eval/lib/__init__.py` (empty).
6. Create `tests/vane-eval/lib/cells.py` with:
    - A `Cell` dataclass: `query_id, model, prompt_style, temperature, thinking, label`. `label` is a short human-readable summary (e.g. `"qwen3.6-27b · structured · t=0.3 · think=off"`) for filenames and the manifest.
    - `discover_omlx_models(base_url) -> list[str]` — `GET /v1/models`, parses based on Unknown #4 result.
    - `build_prompt(query: str, style: Literal["bare","structured","research_system"]) -> tuple[str|None, str]` — returns `(system_prompt, user_message)`. "bare" = no system, just the query. "structured" = no system, user message includes a format hint ("Answer concisely. Cite key facts."). "research_system" = a research-analyst system prompt + bare user query.
    - `call_omlx(base_url, cell, query, timeout_s) -> dict` — issues the chat-completions POST, applies the verified thinking parameter only when `cell.thinking is True`, returns a normalized result `{text, raw, latency_s, error}`.
    - `write_cell_output(run_dir: Path, cell, query_id, query_text, reference_text, result) -> Path` — writes a single `.md` per cell with YAML frontmatter (cell metadata + run context) and body sections: query, reference, response text, raw-JSON appendix, latency.
7. Create `tests/vane-eval/lib/queries.py` with `load(path: Path = …) -> list[Query]` parsing the H2-section format from step 2. Fail loud on missing fields. `Query` is a dataclass: `id, title, query, reference, key_facts: list[str]`.

### Files

- `tests/vane-eval/queries.md` (new)
- `tests/vane-eval/lib/__init__.py` (new)
- `tests/vane-eval/lib/cells.py` (new)
- `tests/vane-eval/lib/queries.py` (new)

### Testing

- `uv run --with httpx python -c "from tests.vane_eval.lib.cells import discover_omlx_models; print(discover_omlx_models('http://host.docker.internal:8000/v1'))"` — should list ≥ 1 model.
- `uv run python -c "from tests.vane_eval.lib.queries import load; qs = load(); assert len(qs) == 6; print([q.id for q in qs])"` — prints `['q1','q2',...,'q6']`.
- Unit-style: invoke `build_prompt` for each style and confirm the system/user split is what the docstring claims.

---

## Phase 2: Cheap-phase OFAT sweep against omlx

### Steps

1. Create `tests/vane-eval/run_cheap.py`. CLI: `--base-url` (default `http://host.docker.internal:8000/v1`), `--models a,b,c` (optional override), `--queries q1,q3` (optional subset), `--out tests/vane-eval/results/cheap-<UTC-ts>` (default), `--force` (bypass cell-count guard).
2. On startup: call `discover_omlx_models`. If `--models` was supplied, intersect; abort with a clear error if any shortlist entry is missing from discovery.
3. Compute the OFAT cell list. Define a `default` cell from the inputs: model = first of (shortlist if given else discovered), prompt_style = `"structured"`, temperature = `0.3`, thinking = `False`. Then sweep:
    - **Models axis:** every model in the active list, others at default. (M cells)
    - **Prompt axis:** `bare`, `structured`, `research_system`, others at default. (3 cells)
    - **Temperature axis:** `0.0`, `0.3`, `0.7`, others at default. (3 cells)
    - **Thinking axis:** `False`, `True`, others at default. (2 cells; skip and log if the chosen default model doesn't support thinking — see Unknown #3.)
    De-duplicate cells that coincide with the default (don't run the default M+1 times). Total cells per query ≈ M + 3 + 3 + 2 − 4 = M + 4. For M=3, Q=6 → 42 calls. Refuse to start if `cells × queries > 90` unless `--force`. Print the planned cell count and a one-line summary of each axis before running.
4. Iterate cells × queries. For each: build the prompt, call `call_omlx`, write the cell file via `write_cell_output`. Wrap each call in try/except; on failure record `error` in the cell file and continue (a partial run is more useful than no run).
5. After the sweep, write `tests/vane-eval/results/<run-id>/MANIFEST.md` listing every cell file (relative path + label + status `ok` / `error` / `skip:no-thinking-support`), the run config (omlx base URL, model list, defaults), and total wall-clock time. This is the entry point the judge in Phase 4 will read.

### Files

- `tests/vane-eval/run_cheap.py` (new)

### Testing

- Tiny dry run: `uv run python tests/vane-eval/run_cheap.py --models <one-model> --queries q1`. Expect ~6 cell files plus `MANIFEST.md`.
- Full run (representative): `uv run python tests/vane-eval/run_cheap.py --models <2–4 models>`. Should complete inside ~30–60 min depending on model size; produce one `.md` per cell × query plus `MANIFEST.md`.
- Open one cell file by hand; confirm frontmatter is YAML-parseable, body has query/reference/response sections, raw JSON appendix is present.
- Force a failure: point `--base-url` at a wrong port; confirm the run completes with all cells in `error` state and the script exits non-zero.

---

## Phase 3: Vane API confirm sweep

### Steps

1. Resolve Unknown #1 first: `colima ssh -p research -- docker exec research-vane sh -c 'find /app/src -name "route.ts" -path "*api*" | head'` then read the relevant file(s). Document the verified endpoint, method, request schema, and response schema in a comment block at the top of `run_vane.py`. If the body shape is unfamiliar, also probe with one curl through Vane (or via a sidecar `colima ssh -p research -- docker run --rm --network research-net curlimages/curl …`) to confirm.
2. Resolve Unknown #2 against the verified schema. If `temperature`/thinking aren't in the body, locate where Vane reads its model defaults under `~/.research/vane-data/` (likely a JSON config). Decide between API-passable and config-mutated knobs. Record the decision in the same comment block.
3. Create `tests/vane-eval/select_winners.py`. CLI: `--from tests/vane-eval/results/cheap-<run>` (required). Reads `SCORES.md` (produced by Phase 4 grading) if present; otherwise prints the manifest and asks the user to hand-edit `winners.json`. Output: `tests/vane-eval/results/cheap-<run>/winners.json` with shape `{winner: {model, prompt_style, temperature, thinking}, ablations: [{…}, …]}`. Keep ablations to ≤ 2.
4. Create `tests/vane-eval/run_vane.py`. CLI: `--winners <path-to-winners.json>` (required), `--queries q1,q3,q5` (default — pick 3 queries spanning factual/multi-hop/judgment), `--out tests/vane-eval/results/vane-<UTC-ts>`. Loops `(winner + ablations) × queries` and POSTs to Vane via the verified endpoint.
5. For each call: pass `chatModel`/`temperature`/thinking via the verified mechanism. If config-mutation is required, write the config, restart the container (`colima ssh -p research -- docker restart research-vane`), wait for the health check (poll `GET http://localhost:3000/` until 200), then issue the request. Otherwise just POST. Record per-cell latency (Vane wall-clock, not just LLM time).
6. Capture Vane's response body in full. In addition to the `write_cell_output` body, append a "Citations" section listing each source URL Vane returned, and a derived metric block: `{citation_count, edu_gov_wiki_share, denylist_hits}`. The denylist source for the third metric is the composed file at `~/.research/denylist-cache/composed.acl` (or whatever filename `compose_denylist` writes — verify against `research.py`).
7. Write a `MANIFEST.md` in the Vane run dir mirroring Phase 2's, plus a header line linking back to the cheap run that fed it (`source: ../cheap-<ts>/`).

### Files

- `tests/vane-eval/run_vane.py` (new)
- `tests/vane-eval/select_winners.py` (new)
- `tests/vane-eval/results/cheap-<ts>/winners.json` (created by user via `select_winners.py`)
- `tests/vane-eval/results/vane-<ts>/` (output of `run_vane.py`)

### Testing

- Smoke: hand-author a `winners.json` with one cell, run `python run_vane.py --winners … --queries q1`. Expect a single cell file with citations and metrics populated.
- Full sub-test: 3 cells (winner + 2 ablations) × 3 queries = 9 Vane calls. Expected runtime 5–15 min. Tail Squid's `access.log` during the run to confirm scrape activity originated at `research-vane`'s IP: `colima ssh -p research -- docker exec research-squid tail -f /var/log/squid/access.log`.
- Negative test: temporarily kill `research-searxng`. `run_vane.py` should still produce cell files marking the search failure rather than aborting the run.

---

## Phase 4: Grading harness for human-in-loop Claude Code judge

### Steps

1. Create `tests/vane-eval/JUDGE.md`. Sections in this order:
    - **How to use.** Two sentences: open this file in a Claude Code session at the repo root; paste the GRADING PROMPT below into the chat.
    - **GRADING PROMPT.** Self-contained; includes (a) instruction to read `tests/vane-eval/queries.md`, (b) instruction to glob the latest `tests/vane-eval/results/*/MANIFEST.md` and read every cell file referenced, (c) the rubric (1–5 on coverage, accuracy, succinctness; for Vane runs only, 1–5 on citation quality — total /15 cheap, /20 Vane), (d) the output format (a single Markdown table sorted by total score, written to `SCORES.md` in the same run dir), (e) a wash-up section asking the judge for short paragraphs on: which axis dominated, which model won, whether prompt/temperature interact, whether thinking helped or just slowed things down.
    - **Rubric details.** One paragraph per axis, with a 1-line example of what "5" vs "1" looks like. Specifically tell the judge to credit *correct-but-unanticipated* answers — the reference is a floor, not a ceiling.
    - **Coverage warning.** Tell the judge that citation quality only applies to Phase 3 (Vane) cells; cheap-phase cells have no citations and the citation column should be `n/a`.
2. Update `select_winners.py` to read `SCORES.md` if present and pre-fill the winners.json template (top row → winner; rows 2–3 → ablations). User still hand-edits before saving.
3. Create `tests/vane-eval/README.md` — five-line entrypoint listing the workflow: run cheap, grade, select winners, run vane, grade. Link to `JUDGE.md`.

### Files

- `tests/vane-eval/JUDGE.md` (new)
- `tests/vane-eval/README.md` (new)
- `tests/vane-eval/select_winners.py` (updated)

### Testing

- End-to-end dry run, on a single tiny model and 2 queries: cheap → JUDGE → select_winners → vane → JUDGE. Confirm the user only had to: paste the grading prompt twice, hand-edit `winners.json` once.
- Open `JUDGE.md` in a fresh Claude Code session at repo root (no other context). Paste the grading prompt. Confirm Claude can locate the run dir, read every cell file referenced in MANIFEST, read the references in `queries.md`, and produce a well-formed `SCORES.md`.

---

## Notes

- **Why OFAT, not full grid.** A full crossing of (3 models × 3 prompts × 3 temps × 2 thinking) × 6 queries = 324 calls. OFAT collapses that to ≈ M+4 cells per query. The cost is missing axis interactions; the gain is a runtime under an hour. If the grading wash-up flags a suspicious axis, re-run with that axis pinned at the new winner (e.g. fix prompt = `research_system`, sweep model × temp).
- **Why grade in a Claude Code session, not via a judge API call.** Per user direction: avoids any API-key wiring; keeps the human in the loop at the only step where judgment is irreducible; cell `.md` files double as a permanent record that's grep-able later.
- **Why two phases.** The cheap phase isolates LLM behavior from Vane's pipeline. The Vane phase confirms the cheap winner survives Vane's added latency, scrape noise, and citation-injection. A regression Phase A → Phase B is itself a useful signal — it tells you Vane's prompt-stuffing is fighting the model.
- **Risks.**
    1. Reference paragraphs reflect the author's prior knowledge. The grading prompt must explicitly tell the judge to credit correct-but-unanticipated answers.
    2. Latency drift can mask quality differences across runs. Record latency per cell, but do not fold it into the rubric.
    3. If omlx's loaded model set changes mid-run, model-axis comparisons are corrupted. Snapshot the model list at run start in `MANIFEST.md`; don't re-discover during the run.
    4. Vane's API may not expose temperature/thinking, forcing config-mutation between cells (slower; risk of leaking state across cells). Mitigate by restarting `research-vane` between any two cells whose config differs.
- **Out of scope.** Embedding-model comparison; multi-turn / follow-up evaluation; cost analysis. We are testing single-turn research-quality only.
