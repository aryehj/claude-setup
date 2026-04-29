# Local research harness — interactive terminal pipeline

## Status

- [ ] Phase 1: scaffold + container infra
- [ ] Phase 2: search + rerank pipeline
- [ ] Phase 3: fetch + extract + per-source notes
- [ ] Phase 4: synthesis stage + bundle output
- [ ] Phase 5: interactive CLI with human gates
- [ ] Phase 6: q1–q6 batch eval driver

## Context

The Vane-based eval (`tests/vane-eval/results/vane-20260429T025556Z/`, analysis at `tests/vane-eval/analysis/vane-20260429T025556Z.md`) showed that retrieval ceilings dominate model/prompt/thinking variation: every model × prompt × thinking combination missed the same reference facts on q3 (saphenous nerve, training-load) and q6 ("lost in the middle"). Vane's intermediation also makes retrieval inscrutable.

The user is dropping Vane as the substrate. The keepers from `research.py`:
- `research` Colima VM
- `research-searxng` container (engines: google, bing, duckduckgo, brave, qwant, wikipedia, arxiv, google scholar, semantic scholar — see `research.py:432`)
- Squid proxy on port 8888 with the composed denylist
- `RESEARCH` iptables chain with the egress allowlist for SearXNG fan-out + LLM endpoint

What replaces Vane: a thin Python CLI in a sibling container `research-runner` joined to `research-net`. Pipeline runs as discrete stages with two human approval gates (after retrieval-and-rerank, after per-source notes), then synthesises with the 31b model. Output is a tree of markdown files under `~/.research/sessions/<timestamp-slug>/` for handoff to a frontier model when local synthesis isn't enough.

Per-stage models, per the user's stated prior:
- Query expansion: E4B (small, format-following)
- Reranking: nomic embedder (user has it locally)
- Per-source content extraction: trafilatura (Python lib, no LLM)
- Per-source notes: 26b-a4b (daily driver — nearly E4B speed, more reliable)
- Synthesis: 31b (slow OK; user explicitly wants to test 31b at this step)

## Goals

- `./tests/local-research/bootstrap.sh "my query"` runs the whole pipeline in terminal with two human gates.
- Gate 1: review the ranked list of sources before fetching. Edit/drop indices, add manual URLs.
- Gate 2: review per-source notes before synthesis. Edit/drop indices.
- Output: tree of markdown files under `~/.research/sessions/<timestamp-slug>/` (`query.md`, `sources/<n>-<slug>.md`, `synthesis.md`, `manifest.md`). Plus a flat `handoff.md` concatenation for one-shot frontier paste.
- 31b is exclusively the synthesis-stage model; everything else is smaller.
- `--batch` and `--no-synth` flags for non-interactive eval and for stopping before synthesis when handoff is the goal.
- Reuses `tests/vane-eval/queries.md` for q1–q6 regression eval. Direct comparability with the Vane run.
- Lives in `tests/local-research/` mirroring `tests/vane-eval/` layout.
- New container `research-runner` joins existing `research-net`; no new VM, no new firewall rules.

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

Create the directory layout, the `research-runner` Dockerfile, and a host-side bootstrap that builds the image and runs the container with the right network and mounts. No pipeline logic yet — Phase 1 success is "smoke-test hits SearXNG and prints JSON."

### Steps

1. Create `tests/local-research/` directory mirroring the `tests/vane-eval/` layout (top-level scripts + `lib/` + `test_*.py` + `eval/`).
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
   - `SYNTH_MODEL` (default `gemma-4-31b-it-6bit`)
   - `SESSION_ROOT = Path("/sessions")` (bind-mounted from host `~/.research/sessions/`)
4. Add `tests/local-research/bootstrap.sh`:
   - Verify the `research` Colima context has `research-searxng` running and `research-net` exists. If not, print "run `./research.py --backend=omlx` first" and exit non-zero.
   - Build `research-runner:latest` if missing or stale (compare Dockerfile mtime to image creation time).
   - Compute bridge IP from `docker network inspect research-net` (per Unknowns #6).
   - `docker run --rm -it --network research-net --add-host=host.docker.internal:host-gateway -v "$HOME/.research/sessions:/sessions" -e HTTP_PROXY=http://<bridge_ip>:8888 -e HTTPS_PROXY=http://<bridge_ip>:8888 -e NO_PROXY=research-searxng,host.docker.internal,localhost,127.0.0.1 -e OMLX_BASE_URL=http://host.docker.internal:8000/v1 -e OMLX_API_KEY="$OMLX_API_KEY" research-runner:latest python -m lib.cli "$@"`.
   - The `--backend=ollama` path is intentionally not supported in v1; document this in `README.md`. If ollama support is needed later, gate it behind a separate config and a different embedder.
5. Add `lib/omlx.py` — shared OpenAI-compatible client wrapping `requests`. Exposes `chat(model, messages, **kw) -> str` and `embed(model, inputs) -> list[list[float]]`, plus `list_models() -> list[dict]`. Centralises base URL, API-key header, and timeouts (default 20 min, matching the recent `vane-eval` POST timeout bump per `599f0a3`). Every later phase uses this helper rather than calling `requests` directly.
6. Add `lib/smoke.py` with three checks: SearXNG reachable, omlx `/v1/models` lists at least one chat model and one embedding model, embedder returns a vector for the string "test". Bootstrap calls it via `--smoke` flag.
6. Write `tests/local-research/README.md` documenting bootstrap usage, env-var overrides, and the session output layout.

### Files

- `tests/local-research/Dockerfile`
- `tests/local-research/bootstrap.sh`
- `tests/local-research/lib/__init__.py`
- `tests/local-research/lib/config.py`
- `tests/local-research/lib/omlx.py`
- `tests/local-research/lib/smoke.py`
- `tests/local-research/README.md`

### Testing

- `./tests/local-research/bootstrap.sh --smoke` prints non-empty results for SearXNG (`?q=test&format=json`), the omlx `/v1/models` listing (showing at least one chat model and the embedder), and a single embedding vector for the string "test".
- Confirm Squid proxy is exercised by `tail -f ~/.research/squid-cache/access.log` during a fetch test and watching one entry appear.
- Confirm a `curl https://example.com` from inside the runner succeeds (Squid + denylist allows generic web).

---

## Phase 2: search + rerank pipeline

Wire SearXNG fan-out and nomic embedder rerank. No human interaction yet — pipeline is callable as a function and prints a ranked list.

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

### Files

- `tests/local-research/lib/expand.py`
- `tests/local-research/lib/search.py`
- `tests/local-research/lib/rerank.py`
- `tests/local-research/lib/pipeline.py`
- `tests/local-research/test_lib.py`

### Testing

- Drive `gather_sources("medial knee pain cyclists")` (q3) end-to-end and eyeball the ranked top-15. Hypothesis: with 5 expanded queries fanning out across all SearXNG engines, more medical / sports-medicine sources appear than in the Vane run for the same question. Compare against `tests/vane-eval/results/vane-20260429T025556Z/q3_*.md` `search_results` JSON blocks.
- Verify ranked-list ordering changes meaningfully when query phrasing varies; if rerank is a no-op, the embedder isn't being hit correctly.

---

## Phase 3: fetch + extract + per-source notes

Fetch each ranked URL through Squid, extract main content with trafilatura, generate a per-source note with the daily-driver model.

### Steps

1. `lib/fetch.py` — `fetch(url: str, timeout_s: int = 30) -> tuple[str, dict]`. Issue HTTP GET via `requests` (proxy env vars are honoured automatically). Returns `(body, meta)` where meta has `status`, `final_url` (after redirects), `latency_s`, `bytes`. Raises a structured exception on 4xx/5xx/timeout/proxy-deny.
2. `lib/extract.py` — `extract(html: str, url: str) -> str`. Wraps `trafilatura.extract(html, output_format='markdown', include_links=False, include_comments=False)`. Falls back to a regex strip-tags + `<title>` if trafilatura returns None or `<500` chars. Returns markdown.
3. `lib/notes.py` — `note_for_source(query: str, source_text: str, meta: dict) -> str`. Calls `NOTES_MODEL` with a fixed prompt: "Given this user query and this source text, write 4–8 short bullet points: (a) the source's central claim relevant to the query, (b) the most relevant specific facts/quotes, (c) any obvious caveats or contrary evidence. If the source is irrelevant, say so in one line." Truncate `source_text` to fit the model's context window with reasonable margin (rough budget: 8k tokens of source text). Sequential calls per Unknowns #3.
4. `lib/bundle.py` — `write_source(session_dir: Path, idx: int, url: str, title: str, extracted: str, note: str, meta: dict)`. Writes `sources/<idx:02d>-<slug>.md` with YAML frontmatter (`url`, `title`, `fetch_latency_s`, `extract_chars`, `note_model`, `note_latency_s`, `engine`) followed by `## Note\n\n{note}\n\n## Extracted\n\n{extracted}\n`.
5. Extend `pipeline.py` with `fetch_and_note(ranked: list[dict], session_dir: Path) -> list[Path]`. For each ranked entry: fetch → extract → note → write_source. Logs per-source progress to stderr (one line: idx, status, fetch_ms, extract_chars, note_ms, note_first_line). On any per-source failure, logs and continues; failures noted in `manifest.md`.

### Files

- `tests/local-research/lib/fetch.py`
- `tests/local-research/lib/extract.py`
- `tests/local-research/lib/notes.py`
- `tests/local-research/lib/bundle.py`
- (extends) `tests/local-research/lib/pipeline.py`, `test_lib.py`

### Testing

- Run on q3's ranked top-15 from Phase 2. Eyeball one source markdown: note section is query-relevant and concise; extracted section is clean prose, not nav cruft.
- Time the per-source phase: budget ~60s/source × 15 = ~15 min. If notably worse, env-var override to `EXPAND_MODEL`-style E4B for note generation.
- Trafilatura fallback: pick a known JS-heavy page and confirm fallback fires (extract returns reasonable content).
- One forced 404 / timeout to confirm error path doesn't abort the batch.

---

## Phase 4: synthesis stage + bundle output

Run 31b over the per-source notes to produce `synthesis.md`; assemble the full bundle.

### Steps

1. `lib/synthesize.py` — `synthesize(query: str, source_metas: list[dict]) -> str`.
   - Build a single prompt: original user query, then a numbered list of source notes (note section only, not the full extracted text — keep tokens manageable). Each entry annotated with title and URL as citation anchors `[1]`, `[2]`, ...
   - Call `SYNTH_MODEL` (default 31b) with a prompt asking for a structured synthesis: TL;DR (3 sentences max), key claims with `[N]` citations, contradictions/uncertainties between sources, gaps that retrieval did not fill.
   - Return markdown.
2. Extend `lib/bundle.py`:
   - `write_query(session_dir, query, expansions)` → `query.md`.
   - `write_synthesis(session_dir, synthesis_md)` → `synthesis.md`.
   - `write_manifest(session_dir, stages, models, timings, source_count, gates_passed)` → `manifest.md`.
   - `write_tarball(session_dir)` → `bundle.tar.gz` of the whole session dir; convenience for one-file handoff.
3. `lib/handoff.py` — `prepare_handoff(session_dir: Path) -> Path`. Concatenates `query.md` + per-source notes (note sections only, no extracted bodies) + `synthesis.md` into a single `handoff.md` at the session-dir root. This is the paste-bombable single-file form. Tree form is canonical; handoff is convenience.

### Files

- `tests/local-research/lib/synthesize.py`
- `tests/local-research/lib/handoff.py`
- (extends) `tests/local-research/lib/bundle.py`

### Testing

- Run the full pipeline non-interactively against q1 (factual baseline). Verify `synthesis.md` cites the EC margin and popular-vote outcome correctly with `[N]` anchors that resolve to actual `sources/<n>-*.md` files.
- Time the synthesis stage: expect 5–15 min on 31b based on vane-eval data.
- Eyeball that `handoff.md` is self-contained (no broken citation refs, no embedded extracted-text walls).

---

## Phase 5: interactive CLI with human gates

Stitch the pipeline together with two human approval gates and a clean stderr/stdout flow.

### Steps

1. `lib/cli.py` — entrypoint `python -m lib.cli "user query"`:
   - Stage A (expand + search + rerank): one-line stderr per substage with timings.
   - **Gate 1**: print top-K ranked sources as `[idx] domain — title (engine)` + 1-line snippet. Prompt: `Approve [enter] / drop indices [comma list] / add URL [+url] / abort [q]:`. Accept multiple actions on one line.
   - Stage B (fetch + extract + notes): per-source progress line as each completes (idx, status, fetch ms, note ms, note first sentence).
   - **Gate 2**: one-line summary per source (idx, title, note first sentence). Prompt: `Approve [enter] / drop indices [comma list] / abort [q]:`.
   - Stage C (synthesise): print "synthesising with 31b… (5–15 min)" then stream stdout if the inference endpoint supports streaming, else print on completion.
   - End: print session-dir path on stdout. Exit 0.
2. Resume support: write a small `state.json` in the session dir after each gate. `--resume <session-dir>` skips completed stages and re-enters at the next gate. Phase-1 implementation: state captures stage-completion booleans + a hash of the input query; reject resume if the hash differs.
3. `--batch` flag: skip both gates (auto-approve top-K and all notes). Used by Phase 6 eval driver.
4. `--no-synth` flag: stop after Gate 2. Useful when handoff is the goal and the user doesn't want to spend 10 min on local synthesis.
5. `--top-k N` and `--expansions N` flags surfacing the rerank/expansion knobs.

### Files

- `tests/local-research/lib/cli.py`
- (extends) `tests/local-research/lib/pipeline.py` for resume hooks

### Testing

- Run interactively against a personal query end-to-end. Gates are clear; index parsing matches indices shown; edits applied correctly to the next stage's input set.
- Ctrl-C at gate 1 → re-run with `--resume <session-dir>` → same ranked list shown, same gate state.
- `--batch --no-synth` exits cleanly with bundle minus `synthesis.md`.
- `--batch` end-to-end on q1 produces a bundle equivalent to a manual approval flow.

---

## Phase 6: q1–q6 batch eval driver

Drive the new pipeline against `tests/vane-eval/queries.md` non-interactively and produce a results layout comparable to `tests/vane-eval/results/`.

### Steps

1. `tests/local-research/eval/run_q1q6.py`:
   - Parse `tests/vane-eval/queries.md` (same parser as `tests/vane-eval/lib/queries.py` — import or copy).
   - For each q in q1..q6: invoke the CLI in `--batch` mode (full pipeline including synthesis). Save bundles under `tests/local-research/eval/results/local-research-<UTC-timestamp>/q<n>/`.
   - Optionally also a `--no-synth` variant per query to save a no-synthesis bundle for frontier handoff.
2. `tests/local-research/eval/manifest.py` — write a top-level `MANIFEST.md` in the run-dir mirroring `tests/vane-eval/results/<run>/MANIFEST.md` shape (per-cell status, latency, file links). Cell shape here is `q<n>` rather than the model-product cells used by vane-eval.
3. After the eval run, write a manual analysis at `tests/vane-eval/analysis/local-research-<timestamp>.md` comparing this run's coverage on the gap facts (q3 saphenous nerve, q3 training-load, q6 lost-in-the-middle) against the Vane run. Same fact-check tables as the previous analysis used.

### Files

- `tests/local-research/eval/run_q1q6.py`
- `tests/local-research/eval/manifest.py`
- `tests/local-research/eval/results/.gitkeep`
- `tests/vane-eval/analysis/local-research-<timestamp>.md` (written by hand after the run)

### Testing

- Run on q1 alone first to confirm shape and parse. Then full sweep.
- Comparison check: do q3 source bundles now contain PubMed / sports-medicine domains? Does any q3 source's note mention saphenous nerve or training-load increase? If yes on either, the architecture has closed the gap. If no, the gap is engine-side and SearXNG settings need PubMed (a `research.py` edit, outside this plan).
- Total wall-clock budget: 6 queries × ~20–30 min each = 2–3 h sweep.

---

## Notes

**Per-stage model assignments** are env-var overridable via `bootstrap.sh`. The eval harness can sweep them; daily use can shift to E4B for notes if 26b-a4b proves too slow over many sources.

**omlx is the only supported backend in v1.** The user's nomic embedder runs on omlx, and consolidating chat + embedding on a single OpenAI-compatible endpoint simplifies the client (one helper, one auth header). Ollama can be added later as a separate config; not worth the abstraction overhead now.

**Why a new container, not extending research-vane.** Vane is a complex Node app and effectively a black box. The runner is ~500 lines of Python total. Joining `research-net` reuses the existing egress firewall (Squid + iptables `RESEARCH` chain) without copying any of it.

**Why `tests/local-research/` rather than top-level `local-research/`.** Mirrors the `tests/vane-eval/` precedent. Daily-driver invocation is a single `bootstrap.sh`; path depth doesn't matter once aliased.

**Parallelism deferred.** Per Unknowns #3, omlx may serialise concurrent calls anyway. Sequential per-source notes keep stderr legible (real-time progress) and make the human-in-loop pacing natural. Revisit only if profiling shows wall-clock pain.

**Out of scope for v1:**
- TUI library / pretty UI (user explicitly said worry about pretty UI later).
- Cross-session corpus / persistent index. Sessions are independent dirs on disk.
- Multi-turn conversation per session. One query → one bundle → frontier chat for follow-ups if needed.
- Faithfulness / citation-grounding pass. The visible per-source notes in the bundle are the v1 trust mechanism.
- Frontier API integration. The bundle is the API; user pastes/uploads it manually.

**Adjacent risk worth flagging.** If the q3 retrieval gap persists after Phase 6 (saphenous nerve / training-load still absent from any retrieved source), the cause is upstream of this harness — SearXNG's enabled engines don't index the relevant content. Adding PubMed via SearXNG's `pubmed` engine is a one-line `render_searxng_settings()` change in `research.py:432`. That's outside this plan but worth a pre-eval edit if you want to give the new architecture its best chance to demonstrate retrieval gains independent of engine-list changes.
