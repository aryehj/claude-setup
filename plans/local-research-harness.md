# Local research harness — interactive terminal pipeline

## Status

- [ ] Phase 1: scaffold + container infra
- [ ] Phase 2: search + rerank pipeline
- [ ] Phase 3: fetch + extract + per-source notes
- [ ] Phase 4: synthesis stage + bundle output
- [ ] Phase 5: interactive CLI with human gates
- [ ] Phase 6: q1–q6 batch eval driver
- [ ] Phase 7: 31b vs 26b-a4b synthesis bake-off
<!-- mark [x] as phases complete during implementation. Append `(Haiku ok)` for mechanical edits or `(Opus recommended)` for phases heavy with judgment calls; otherwise no annotation. -->

## Context

The Vane-based eval showed that retrieval ceilings dominated model/prompt/thinking variation: every model × prompt × thinking combination missed the same reference facts on q3 (saphenous nerve, training-load) and q6 ("lost in the middle"). Vane's intermediation also makes retrieval inscrutable.

The user is dropping Vane as the substrate. The keepers from `research.py`:
- `research` Colima VM
- `research-searxng` container (engines: google, bing, duckduckgo, brave, qwant, wikipedia, arxiv, google scholar, semantic scholar — see `research.py:432`)
- Squid proxy on port 8888 with the composed denylist
- `RESEARCH` iptables chain with the egress allowlist for SearXNG fan-out + LLM endpoint

Per-stage models, per the user's stated prior:
- Query expansion: E4B (small, format-following)
- Reranking: nomic embedder (user has it locally)
- Per-source content extraction: trafilatura (Python lib, no LLM)
- Per-source notes: 26b-a4b (daily driver — nearly E4B speed, more reliable)
- Synthesis: 26b-a4b by default in Phases 1–6 to keep iteration fast; Phase 7 runs a 31b-vs-26b-a4b bake-off at this stage to decide whether the slower model is worth it.

## Goals

- `./tests/local-research/bootstrap.sh "my query"` runs the whole pipeline in terminal with two human gates.
- Gate 1: review the ranked list of sources before fetching. Edit/drop indices, add manual URLs.
- Gate 2: review per-source notes before synthesis. Edit/drop indices.
- Output: tree of markdown files under `~/.research/sessions/<timestamp-slug>/` (`query.md`, `sources/<n>-<slug>.md`, `synthesis.md`, `manifest.md`). Plus a flat `handoff.md` concatenation for one-shot frontier paste.
- Synthesis defaults to 26b-a4b for speed; 31b is reserved for the Phase 7 bake-off and as an opt-in override via `SYNTH_MODEL`.
- `--batch` and `--no-synth` flags for non-interactive eval and for stopping before synthesis when handoff is the goal.
- Reuses `experiments/vane-eval/queries.md` for q1–q6 regression eval. Direct comparability with the Vane run.
- Lives in `tests/local-research/`.
- New container `research-runner` joins existing `research-net`; no new VM, no new firewall rules.

## Approach

Replace Vane with a thin Python CLI in a sibling container `research-runner` joined to `research-net`, reusing the existing Squid proxy + `RESEARCH` iptables chain rather than introducing any new firewall surface. The pipeline runs as discrete stages — expand → search → rerank → fetch → extract → note → synthesise — with two human approval gates between retrieval/rerank and per-source-note review. Output is a tree of markdown files under `~/.research/sessions/<timestamp-slug>/` for handoff to a frontier model when local synthesis isn't enough.

Phases ladder up from infrastructure to interactivity: Phase 1 stands up the container and confirms connectivity, Phase 2 makes a non-interactive ranked-list pipeline, Phase 3 adds per-source notes, Phase 4 adds synthesis + bundle output, Phase 5 wraps the lot in an interactive CLI with the two gates, and Phase 6 drives the q1–q6 eval against it for direct comparability with the Vane run. Each earlier phase is independently exercisable, so a regression in one phase doesn't block experimenting with the next.

The biggest risk is that the q3/q6 retrieval gap is engine-side (SearXNG's enabled engines simply don't index the relevant medical/training-load content), in which case the new architecture won't move the needle. Phase 6's comparison is the explicit check for that, and the mitigation (adding PubMed via `research.py`'s SearXNG settings) is called out in Notes — outside this plan but a one-liner.

## Unknowns / To Verify

1. **Exact omlx model IDs currently served.** The vane-eval cells used `gemma-4-31b-it-6bit`, `gemma-4-26b-a4b-it-8bit`, `gemma-4-E4B-it-MLX-8bit`. Confirm these still match what `/v1/models` returns; treat as env-var overrides if not. Phases 3 and 4. Also confirm the embedder model ID under omlx (likely `nomic-embed-text-v1.5` or similar) before Phase 2 — query `/v1/models` and pick the embedding-class entry.
2. **omlx embedding endpoint shape.** omlx exposes `/v1/embeddings` (OpenAI-compatible) — verify the request/response shape matches `{"input": "...", "model": "..."}` → `{"data": [{"embedding": [...]}]}` with one curl call before writing `lib/rerank.py`.
3. **omlx concurrency behavior.** If we want parallel per-source note generation, omlx may serialise concurrent `/v1/chat/completions` calls. Test two concurrent curl calls before deciding. Default to sequential in Phase 3; revisit only if 15-source phase exceeds ~25 minutes.
4. **Trafilatura coverage on actual source domains.** It's the de-facto Python standard but degrades on JS-rendered pages and PDFs. Test on 5 sample URLs from q3's search results before relying on it; provide a regex strip-tags fallback for `<500`-char results.
5. **Default research-net intra-bridge connectivity.** The `RESEARCH` iptables chain governs research-net→external; container-to-container on the same bridge should pass freely, but verify `curl http://research-searxng:8080/` from inside a throwaway container during Phase 1 setup before finalising the Dockerfile.
6. **Whether the bridge IP for Squid is stable across `research.py` runs.** Phase 1's bootstrap needs `HTTP_PROXY=http://<bridge_ip>:8888`. If unstable, derive at runtime from `docker network inspect research-net`. See `research.py:543`.
7. **`OMLX_API_KEY` plumbing.** `research.py` already passes `OMLX_API_KEY` into the research VM environment for the omlx backend (see `CLAUDE.md` "research.py key decisions"). The runner container needs the same key forwarded as a request `Authorization: Bearer ...` header on every omlx call. Forward via `-e OMLX_API_KEY=$OMLX_API_KEY` in `bootstrap.sh`; have the omlx client helper attach the header when the env var is set.

---

## Phase 1: scaffold + container infra

### Steps

1. Create `tests/local-research/` directory (top-level scripts + `lib/` + `test_*.py` + `eval/`).
2. Add `tests/local-research/Dockerfile`:
   - Base: `python:3.12-slim` (or whatever matches conventions in the repo — verify against any existing Python Dockerfiles before picking).
   - Install: `requests`, `trafilatura`, `pyyaml`. No node, no model server.
   - `WORKDIR /app`, copy `lib/` and entrypoint script.
3. Add `tests/local-research/lib/config.py` with module-level constants and env-var overrides. **omlx is the default and only supported backend for v1** — it hosts both the chat models and the embedder. Constants:
   - `SEARXNG_URL = "http://research-searxng:8080"`
   - `OMLX_BASE_URL = os.environ.get("OMLX_BASE_URL", "http://host.docker.internal:8000/v1")`
   - `OMLX_API_KEY = os.environ.get("OMLX_API_KEY", "")` — attached as `Authorization: Bearer ...` when non-empty.
   - `EMBED_MODEL` (default discovered at startup from `/v1/models` — pick the embedding-class entry, override via env)
   - `EXPAND_MODEL` (default `gemma-4-E4B-it-MLX-8bit`)
   - `NOTES_MODEL` (default `gemma-4-26b-a4b-it-8bit`)
   - `SYNTH_MODEL` (default `gemma-4-26b-a4b-it-8bit` — same as `NOTES_MODEL`; Phase 7 sweeps to 31b)
   - `SESSION_ROOT = Path("/sessions")` (bind-mounted from host `~/.research/sessions/`)
4. Add `tests/local-research/bootstrap.sh`:
   - Verify the `research` Colima context has `research-searxng` running and `research-net` exists. If not, print "run `./research.py --backend=omlx` first" and exit non-zero.
   - Build `research-runner:latest` if missing or stale (compare Dockerfile mtime to image creation time).
   - Compute bridge IP from `docker network inspect research-net` (per Unknowns #6).
   - `docker run --rm -it --network research-net --add-host=host.docker.internal:host-gateway -v "$HOME/.research/sessions:/sessions" -e HTTP_PROXY=http://<bridge_ip>:8888 -e HTTPS_PROXY=http://<bridge_ip>:8888 -e NO_PROXY=research-searxng,host.docker.internal,localhost,127.0.0.1 -e OMLX_BASE_URL=http://host.docker.internal:8000/v1 -e OMLX_API_KEY="$OMLX_API_KEY" research-runner:latest python -m lib.cli "$@"`.
   - The `--backend=ollama` path is intentionally not supported in v1; document this in `README.md`. If ollama support is needed later, gate it behind a separate config and a different embedder.
5. Add `lib/omlx.py` — shared OpenAI-compatible client wrapping `requests`. Exposes `chat(model, messages, **kw) -> str` and `embed(model, inputs) -> list[list[float]]`, plus `list_models() -> list[dict]`. Centralises base URL, API-key header, and timeouts (default 20 min, matching the recent `vane-eval` POST timeout bump per `599f0a3`). Every later phase uses this helper rather than calling `requests` directly.
6. Add `lib/smoke.py` with three checks: SearXNG reachable, omlx `/v1/models` lists at least one chat model and one embedding model, embedder returns a vector for the string "test". Bootstrap calls it via `--smoke` flag.
7. Write `tests/local-research/README.md` documenting bootstrap usage, env-var overrides, and the session output layout.

### Acceptance criteria

- `./tests/local-research/bootstrap.sh --smoke` prints non-empty results for SearXNG (`?q=test&format=json`), the omlx `/v1/models` listing (showing at least one chat model and the embedder), and a single embedding vector for the string "test".
- Squid proxy is exercised end-to-end: a fetch from inside the runner produces a fresh entry in `~/.research/squid-cache/access.log`.
- A `curl https://example.com` from inside the runner succeeds (Squid + denylist allows generic web).

---

## Phase 2: search + rerank pipeline

### Steps

1. `lib/expand.py` — query expansion. Single function `expand(query: str, n: int = 4) -> list[str]`. Calls `EXPAND_MODEL` with a fixed prompt asking for N alternative phrasings (different angles: technical-vs-lay, narrow-vs-broad, alternative terminology). Parse one-per-line. Return `[query, *expansions]` so the original is always queried.
2. `lib/search.py` — SearXNG client. `search(query: str, n: int = 20) -> list[dict]` issues GET to `{SEARXNG_URL}/search?q={...}&format=json` and returns the `results` array. Each result has `url`, `title`, `content`, `engine`, `score` per SearXNG's JSON schema.
3. `lib/rerank.py` — embedding rerank against omlx.
   - `embed(texts: list[str]) -> list[list[float]]` — POST to `{OMLX_BASE_URL}/embeddings` with `{"model": EMBED_MODEL, "input": texts}`. Attach `Authorization: Bearer $OMLX_API_KEY` header when the env var is non-empty. Returns the list of `data[i].embedding` arrays.
   - `rerank(query: str, results: list[dict], top_k: int = 15) -> list[dict]` — embed `query` and each result's `f"{title}\n{content}"`; cosine-rank against the query embedding; return top_k. Dedupe by URL before reranking. Preserve original `engine` and `score` in the returned dicts.
4. `lib/pipeline.py` — `gather_sources(query: str) -> dict` calls `expand → search-each → flatten → dedupe → rerank`. Returns a dict `{query, expansions, raw_results, ranked, timings}` for downstream stages and debugging. Pure orchestration; no I/O outside the helpers' subprocess/HTTP calls.
5. `test_lib.py` — pytest unit tests:
   - `rerank` returns top_k items in cosine order given stubbed embeddings.
   - Dedupe collapses duplicate URLs across expansions.
   - `search` parses a fixture JSON correctly.

### Acceptance criteria

- `gather_sources("medial knee pain cyclists")` (q3) runs end-to-end and the ranked top-15 contains visibly more medical / sports-medicine sources than the Vane run for the same question.
- Ranked-list ordering changes meaningfully when query phrasing varies — confirms the embedder is actually being hit (not silently no-op'd).

---

## Phase 3: fetch + extract + per-source notes

### Steps

1. `lib/fetch.py` — `fetch(url: str, timeout_s: int = 30) -> tuple[str, dict]`. Issue HTTP GET via `requests` (proxy env vars are honoured automatically). Returns `(body, meta)` where meta has `status`, `final_url` (after redirects), `latency_s`, `bytes`. Raises a structured exception on 4xx/5xx/timeout/proxy-deny.
2. `lib/extract.py` — `extract(html: str, url: str) -> str`. Wraps `trafilatura.extract(html, output_format='markdown', include_links=False, include_comments=False)`. Falls back to a regex strip-tags + `<title>` if trafilatura returns None or `<500` chars. Returns markdown.
3. `lib/notes.py` — `note_for_source(query: str, source_text: str, meta: dict) -> str`. Calls `NOTES_MODEL` with a fixed prompt: "Given this user query and this source text, write 4–8 short bullet points: (a) the source's central claim relevant to the query, (b) the most relevant specific facts/quotes, (c) any obvious caveats or contrary evidence. If the source is irrelevant, say so in one line." Truncate `source_text` to fit the model's context window with reasonable margin (rough budget: 8k tokens of source text). Sequential calls per Unknowns #3.
4. `lib/bundle.py` — `write_source(session_dir: Path, idx: int, url: str, title: str, extracted: str, note: str, meta: dict)`. Writes `sources/<idx:02d>-<slug>.md` with YAML frontmatter (`url`, `title`, `fetch_latency_s`, `extract_chars`, `note_model`, `note_latency_s`, `engine`) followed by `## Note\n\n{note}\n\n## Extracted\n\n{extracted}\n`.
5. Extend `pipeline.py` with `fetch_and_note(ranked: list[dict], session_dir: Path) -> list[Path]`. For each ranked entry: fetch → extract → note → write_source. Logs per-source progress to stderr (one line: idx, status, fetch_ms, extract_chars, note_ms, note_first_line). On any per-source failure, logs and continues; failures noted in `manifest.md`.

### Acceptance criteria

- A run on q3's ranked top-15 produces source markdown where the note section is query-relevant and concise, and the extracted section is clean prose (not nav cruft).
- Per-source phase completes within budget (~60s/source × 15 = ~15 min). Notably worse → env-var override to E4B for note generation is documented.
- Trafilatura fallback fires on a known JS-heavy page and produces reasonable content.
- A forced 404 / timeout on one source does not abort the batch; the failure is recorded in `manifest.md`.

---

## Phase 4: synthesis stage + bundle output

### Steps

1. `lib/synthesize.py` — `synthesize(query: str, source_metas: list[dict]) -> str`.
   - Build a single prompt: original user query, then a numbered list of source notes (note section only, not the full extracted text — keep tokens manageable). Each entry annotated with title and URL as citation anchors `[1]`, `[2]`, ...
   - Call `SYNTH_MODEL` (default 26b-a4b — same as notes; Phase 7 evaluates whether 31b is meaningfully better) with a prompt asking for a structured synthesis: TL;DR (3 sentences max), key claims with `[N]` citations, contradictions/uncertainties between sources, gaps that retrieval did not fill.
   - Return markdown.
2. Extend `lib/bundle.py`:
   - `write_query(session_dir, query, expansions)` → `query.md`.
   - `write_synthesis(session_dir, synthesis_md)` → `synthesis.md`.
   - `write_manifest(session_dir, stages, models, timings, source_count, gates_passed)` → `manifest.md`.
   - `write_tarball(session_dir)` → `bundle.tar.gz` of the whole session dir; convenience for one-file handoff.
3. `lib/handoff.py` — `prepare_handoff(session_dir: Path) -> Path`. Concatenates `query.md` + per-source notes (note sections only, no extracted bodies) + `synthesis.md` into a single `handoff.md` at the session-dir root. This is the paste-bombable single-file form. Tree form is canonical; handoff is convenience.

### Acceptance criteria

- A non-interactive run against q1 (factual baseline) produces a `synthesis.md` that cites the EC margin and popular-vote outcome correctly with `[N]` anchors that resolve to actual `sources/<n>-*.md` files.
- Synthesis-stage wall-clock on 26b-a4b is materially faster than the 5–15 min seen on 31b in vane-eval — target ≤5 min so a full q1–q6 sweep stays under ~2 h.
- `handoff.md` is self-contained: no broken citation refs, no embedded extracted-text walls.

---

## Phase 5: interactive CLI with human gates

### Steps

1. `lib/cli.py` — entrypoint `python -m lib.cli "user query"`:
   - Stage A (expand + search + rerank): one-line stderr per substage with timings.
   - **Gate 1**: print top-K ranked sources as `[idx] domain — title (engine)` + 1-line snippet. Prompt: `Approve [enter] / drop indices [comma list] / add URL [+url] / abort [q]:`. Accept multiple actions on one line.
   - Stage B (fetch + extract + notes): per-source progress line as each completes (idx, status, fetch ms, note ms, note first sentence).
   - **Gate 2**: one-line summary per source (idx, title, note first sentence). Prompt: `Approve [enter] / drop indices [comma list] / abort [q]:`.
   - Stage C (synthesise): print `synthesising with {SYNTH_MODEL}…` (with a rough time hint based on the model: ≤5 min for 26b-a4b, 5–15 min for 31b) then stream stdout if the inference endpoint supports streaming, else print on completion.
   - End: print session-dir path on stdout. Exit 0.
2. Resume support: write a small `state.json` in the session dir after each gate. `--resume <session-dir>` skips completed stages and re-enters at the next gate. Phase-1 implementation: state captures stage-completion booleans + a hash of the input query; reject resume if the hash differs.
3. `--batch` flag: skip both gates (auto-approve top-K and all notes). Used by Phase 6 eval driver.
4. `--no-synth` flag: stop after Gate 2. Useful when handoff is the goal and the user doesn't want to spend 10 min on local synthesis.
5. `--top-k N` and `--expansions N` flags surfacing the rerank/expansion knobs.

### Acceptance criteria

- An interactive run against a personal query reaches both gates with clear summaries; index parsing matches the indices shown; edits at each gate apply correctly to the next stage's input set.
- Ctrl-C at gate 1 followed by `--resume <session-dir>` shows the same ranked list and the same gate state.
- `--batch --no-synth` exits cleanly with a bundle that has every artifact except `synthesis.md`.
- `--batch` end-to-end on q1 produces a bundle equivalent to the manual approval flow.

---

## Phase 6: q1–q6 batch eval driver

### Steps

1. `tests/local-research/eval/run_q1q6.py`:
   - Parse `experiments/vane-eval/queries.md`.
   - For each q in q1..q6: invoke the CLI in `--batch` mode (full pipeline including synthesis). Save bundles under `tests/local-research/eval/results/local-research-<UTC-timestamp>/q<n>/`.
   - Optionally also a `--no-synth` variant per query to save a no-synthesis bundle for frontier handoff.
2. `tests/local-research/eval/manifest.py` — write a top-level `MANIFEST.md` in the run-dir (per-cell status, latency, file links). Cell shape here is `q<n>`.

### Acceptance criteria

- A q1-only run produces the expected layout and parses `queries.md` correctly before the full sweep is attempted.
- Comparison check vs. the Vane run: q3 source bundles contain PubMed / sports-medicine domains, and at least one q3 source's note mentions saphenous nerve or training-load increase. If yes → architecture has closed the gap. If no → gap is engine-side and SearXNG settings need PubMed (a `research.py:432` edit, outside this plan).
- Total wall-clock for the sweep lands under ~2 h (6 queries × ~15–20 min each, given 26b-a4b synthesis instead of 31b).

---

## Phase 7: 31b vs 26b-a4b synthesis bake-off

### Steps

1. `tests/local-research/eval/run_synth_bakeoff.py`:
   - Take a Phase 6 run-dir as input (`--from <run-dir>`); reuse its per-source notes verbatim. Do not re-run retrieval, fetch, extract, or note generation — synthesis is the only variable.
   - For each q1..q6 cell in the input run-dir, call `lib.synthesize.synthesize(...)` twice: once with `SYNTH_MODEL=gemma-4-26b-a4b-it-8bit`, once with `SYNTH_MODEL=gemma-4-31b-it-6bit`. Save outputs side-by-side as `synthesis-26ba4.md` and `synthesis-31b.md` under `tests/local-research/eval/results/synth-bakeoff-<UTC-timestamp>/q<n>/`. Record per-call latency.
   - Sequential, not parallel — omlx serialises requests per Unknowns #3, and we want clean per-call timings.
2. `tests/local-research/eval/bakeoff_manifest.py` — write a top-level `MANIFEST.md` summarising per-cell wall-clock for each model and total tokens (if the inference endpoint reports `usage`). Include side-by-side links to the two synthesis files for each query.
3. Add a `tests/local-research/eval/bakeoff_diff.py` helper that, for each query, prints a small comparison: which `[N]` citation anchors each synthesis used, citation-count overlap, length in chars, and whether each surfaces the q3 reference facts (saphenous nerve, training-load increase) and q6 "lost in the middle" — string-match heuristics, not an LLM judge.
4. Document in `tests/local-research/README.md` how to run the bake-off against any Phase 6 run-dir, and call out that the comparison is judged by reading the side-by-side outputs (no LLM judge in v1 — see Out of scope).

### Acceptance criteria

- A bake-off run completes for q1–q6 producing two synthesis files per query with recorded latencies, all referenced from `MANIFEST.md`.
- Per-query 26b-a4b synthesis is meaningfully faster than 31b (target ratio ≥2×) — confirms the speed argument for the Phase 1–6 default.
- The diff report makes it possible to answer "does 31b surface anything 26b-a4b missed on the same notes" by eyeball — at minimum, citation-anchor sets, lengths, and reference-fact hits are shown side-by-side per query.
- Decision recorded in this plan's Notes section after the run: keep 26b-a4b as default, switch to 31b, or expose a `--synth-model` per-run flag (already present via `SYNTH_MODEL` env var, but may want to lift to a CLI flag if the answer is "depends on query").

---

## Notes

**Per-stage model assignments** are env-var overridable via `bootstrap.sh`. The eval harness can sweep them; daily use can shift to E4B for notes if 26b-a4b proves too slow over many sources.

**omlx is the only supported backend in v1.** The user's nomic embedder runs on omlx, and consolidating chat + embedding on a single OpenAI-compatible endpoint simplifies the client (one helper, one auth header). Ollama can be added later as a separate config; not worth the abstraction overhead now.

**Why a new container, not extending research-vane.** Vane is a complex Node app and effectively a black box. The runner is ~500 lines of Python total. Joining `research-net` reuses the existing egress firewall (Squid + iptables `RESEARCH` chain) without copying any of it.

**Why `tests/local-research/` rather than top-level `local-research/`.** Daily-driver invocation is a single `bootstrap.sh`; path depth doesn't matter once aliased.

**Parallelism deferred.** Per Unknowns #3, omlx may serialise concurrent calls anyway. Sequential per-source notes keep stderr legible (real-time progress) and make the human-in-loop pacing natural. Revisit only if profiling shows wall-clock pain.

**Out of scope for v1:**
- TUI library / pretty UI (user explicitly said worry about pretty UI later).
- Cross-session corpus / persistent index. Sessions are independent dirs on disk.
- Multi-turn conversation per session. One query → one bundle → frontier chat for follow-ups if needed.
- Faithfulness / citation-grounding pass. The visible per-source notes in the bundle are the v1 trust mechanism.
- Frontier API integration. The bundle is the API; user pastes/uploads it manually.

**Adjacent risk worth flagging.** If the q3 retrieval gap persists after Phase 6 (saphenous nerve / training-load still absent from any retrieved source), the cause is upstream of this harness — SearXNG's enabled engines don't index the relevant content. Adding PubMed via SearXNG's `pubmed` engine is a one-line `render_searxng_settings()` change in `research.py:432`. That's outside this plan but worth a pre-eval edit if you want to give the new architecture its best chance to demonstrate retrieval gains independent of engine-list changes.
