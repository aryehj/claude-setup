# Local research harness — interactive terminal pipeline

## Status

- [x] Phase 1: scaffold + container infra (sonnet-medium ok)
- [x] Phase 2: per-round search + rerank pipeline (sonnet-medium ok)
- [x] Phase 3: fetch + extract + per-source notes (sonnet-medium ok)
- [x] Phase 4: round orchestration (branch proposal, dedupe, per-round digest, termination)
- [x] Phase 5: source-quality biasing (input-side levers)
- [~] Phase 6: SearXNG-config tuning loop (agent-as-loop) — harness + pre-run defaults landed; step 4 (run the loop) not yet done
- [ ] Phase 7: hierarchical synthesis + bundle output
- [ ] Phase 8: interactive CLI with multi-round gates (sonnet-medium ok)
- [ ] Phase 9: --batch mode + q1–q6 regression eval (Opus recommended)
- [ ] Phase 10: synthesis-quality evaluation harness (Opus recommended)
<!-- mark [x] as phases complete during implementation. Effort annotations: `(sonnet-medium ok)` for mechanical phases where the spec leaves few decisions (wrapper code, fixed prompts, scaffolding); `(Opus recommended)` for judgment-heavy phases (autonomous decisions, eval design, novel rubric work); unannotated phases default to sonnet-high — typically because the prompt itself is the product (Phases 4, 5, 6, 7) and wording materially affects downstream quality. -->

## Context

The Vane-based eval showed that retrieval ceilings dominated model/prompt/thinking variation: every model × prompt × thinking combination missed the same reference facts on q3 (saphenous nerve, training-load) and q6 ("lost in the middle"). Vane's intermediation also makes retrieval inscrutable.

Replacing Vane with a thin Python pipeline that does what real secondary research looks like: iterative, branching search across multiple peripherally related domains. Target ~50–100 sources across 3–5 rounds, not a single 15-source pass. Vane does this but poorly; the goal is to do the same shape well, with a debuggable per-round bundle.

Keepers from `research.py`:
- `research` Colima VM
- `research-searxng` container (engines: google, bing, duckduckgo, brave, qwant, wikipedia, arxiv, google scholar, semantic scholar — see `research.py:432`)
- Squid proxy on port 8888 with the composed denylist
- `RESEARCH` iptables chain with the egress allowlist for SearXNG fan-out + LLM endpoint

Per-stage models, per the user's stated prior:
- Query expansion: E4B (small, format-following)
- Reranking: nomic embedder (user has it locally)
- Per-source content extraction: trafilatura (Python lib, no LLM)
- Branch proposal (gap-driven, after each round): 26b-a4b (judgment call over accumulated digests)
- Per-round digest: 26b-a4b
- Per-source notes: 26b-a4b (daily driver — nearly E4B speed, more reliable)
- Final synthesis: 26b-a4b by default in Phases 1–9; Phase 10 sweeps over context-shape, model tier, prompt template, and thinking.

## Goals

- `./tests/local-research/bootstrap.sh "my query"` runs an iterative, multi-round pipeline in terminal with three classes of human gate: branch-proposal review (per round), source review (per round), final synthesis approval. Total `2N+1` gates across an N-round session.
- Output: tree of markdown files under `~/.research/sessions/<timestamp-slug>/` with `query.md`, `rounds/<n>/sources/<m>-<slug>.md`, `rounds/<n>/digest.md`, `synthesis.md`, `manifest.md`. Plus a flat `handoff.md` concatenation for one-shot frontier paste.
- Each round's digest is preserved as a debuggable artifact even though final synthesis cites at source-level `[N]`.
- Final synthesis is hierarchical: digests are primary context; per-source notes are supplemental for the top-K relevance-ranked sources across all rounds.
- `--batch` and `--no-synth` flags. `--batch` selects branches autonomously and terminates on a hybrid hard-cap + novelty-floor heuristic (Phase 9).
- Reuses `experiments/vane-eval/queries.md` for q1–q6 regression eval. Direct comparability with the Vane run.
- Lives in `tests/local-research/`.
- New container `research-runner` joins existing `research-net`; no new VM, no new firewall rules.

## Approach

Replace Vane with a thin Python CLI in a sibling container `research-runner` joined to `research-net`, reusing the existing Squid proxy + `RESEARCH` iptables chain rather than introducing any new firewall surface. The pipeline is a loop: each round does `expand → search → rerank → fetch → extract → notes → digest → branch-proposal`. The orchestrator accumulates sources across rounds with URL dedupe. Round termination is human-driven in interactive mode, hybrid hard-cap-plus-novelty-floor in batch mode. After the final round, hierarchical synthesis runs over the per-round digests + top-K source notes with source-level `[N]` citations.

Phases ladder bottom-up: 1 stands up infra, 2–3 build the per-round inner loop, 4 builds the orchestration layer (branch proposal, dedupe, digest, termination scaffolding), 5 tunes the input side of the funnel so what enters rerank is biased toward science / considered editorial and away from SEO / listicle / marketing fluff, 6 tunes SearXNG itself in an autonomous loop so the candidate set is best-quality before runner-side levers act on it, 7 wraps up final synthesis + bundle, 8 wraps it all in an interactive CLI, 9 nails down `--batch` semantics with the autonomous termination heuristic and runs q1–q6, 10 runs the synthesis-quality input-variable sweep. Each phase is independently exercisable; a regression in one doesn't block experimenting with the next.

The biggest open risk is still that the q3/q6 retrieval gap is engine-side (SearXNG's enabled engines simply don't index the relevant medical/training-load content), in which case the new architecture won't move the needle on the same engines. Phase 9's Vane comparison is the explicit check; Phase 6 attacks this risk directly by letting the engine list itself be a tunable.

## Unknowns / To Verify

1. **Exact omlx model IDs.** vane-eval used `gemma-4-31b-it-6bit`, `gemma-4-26b-a4b-it-8bit`, `gemma-4-E4B-it-MLX-8bit`. Confirm against `/v1/models`; treat as env-var overrides if they've changed. Also confirm the embedder model ID — likely `nomic-embed-text-v1.5` or similar — by querying `/v1/models` and picking the embedding-class entry.
2. **omlx embedding endpoint shape.** Verify `{"input": "...", "model": "..."}` → `{"data": [{"embedding": [...]}]}` with one curl call before writing `lib/rerank.py`.
3. **omlx concurrency behavior.** Does omlx serialise concurrent `/v1/chat/completions`? Test with two concurrent curls. Default to sequential everywhere; revisit only if a 50–100-source run is unworkably slow.
4. **Trafilatura coverage on actual source domains.** Test on 5 sample URLs from q3's search results; provide a regex strip-tags fallback for `<500`-char results.
5. **Default research-net intra-bridge connectivity.** Verify `curl http://research-searxng:8080/` from inside a throwaway container during Phase 1 setup.
6. **Bridge IP stability across `research.py` runs.** Phase 1's bootstrap needs `HTTP_PROXY=http://<bridge_ip>:8888`. Resolve at runtime per `bootstrap.sh` invocation; do not bake into the image. See `research.py:543`.
7. **`OMLX_API_KEY` plumbing.** `research.py` already passes it into the research VM environment. Forward via `-e OMLX_API_KEY=$OMLX_API_KEY` in `bootstrap.sh`; the omlx client helper attaches the header when set. `--smoke` fails loud if missing.
8. **Rerank score scale across models.** Use a per-run normalised threshold rather than a fixed cutoff — embedder-agnostic and self-calibrating. Exact normalisation strategy (within-round vs re-embedded against the seed query) is an implementation detail for Phase 9; calibrate the sigma multiple after a real q1 run.
9. **Long-context degradation thresholds for synthesis.** Both 26b-a4b and 31b claim 250k context windows; the testing host has 64 GB RAM. Set ceilings at where we'd expect long-context performance to degrade, not to minimise resource use. Targets: per-round digest ≤5k tokens (≈800–1200 words), final synthesis input ≤40–50k tokens (digests + top-K notes + instructions). Well under typical "lost in the middle" degradation regimes for capable long-context models, but ~3× more input than the prior plan. Measure a 4-round run after Phase 4; loosen further if quality holds.
10. **SearXNG engine catalog as of run time.** Engine names and per-engine option shapes for Phase 6's mutations need to come from SearXNG's upstream `settings.yml` (e.g. `pubmed`, `crossref`, `core.ac.uk`, `openairepository` may or may not be first-party; some live in third-party engine plugins). Verify by reading the running container's `/etc/searxng/settings.yml` and `/usr/local/searxng/searx/engines/` before treating any engine as "drop-in".

---

## Phase 1: scaffold + container infra

### Steps

1. Create `tests/local-research/` directory (top-level scripts + `lib/` + `test_*.py` + `eval/`).
2. Add `tests/local-research/Dockerfile`:
   - Base: `python:3.12-slim` (or whatever matches conventions in the repo — verify against any existing Python Dockerfiles before picking).
   - Install: `requests`, `trafilatura`, `pyyaml`. No node, no model server.
   - `WORKDIR /app`, copy `lib/` and entrypoint script.
3. Add `tests/local-research/lib/config.py` with module-level constants and env-var overrides. **omlx is the default and only supported backend for v1** — it hosts both the chat models and the embedder. Constants needed: SearXNG URL, omlx base URL + API key, four model IDs (embedder, expand, notes, synth), session root (bind-mounted from host `~/.research/sessions/`), and the round-budget constants (`MAX_ROUNDS`, `MAX_SOURCES`) consumed by Phase 9. Model IDs resolved per Unknowns #1; all model constants env-var overridable.
4. Add `tests/local-research/bootstrap.sh`:
   - Verify the `research` Colima context has `research-searxng` running and `research-net` exists. If not, print "run `./research.py --backend=omlx` first" and exit non-zero.
   - Build `research-runner:latest` if missing or stale (compare Dockerfile mtime to image creation time).
   - Compute bridge IP from `docker network inspect research-net` (per Unknowns #6).
   - `docker run --rm -it --network research-net --add-host=host.docker.internal:host-gateway -v "$HOME/.research/sessions:/sessions" -e HTTP_PROXY=http://<bridge_ip>:8888 -e HTTPS_PROXY=http://<bridge_ip>:8888 -e NO_PROXY=research-searxng,host.docker.internal,localhost,127.0.0.1 -e OMLX_BASE_URL=http://host.docker.internal:8000/v1 -e OMLX_API_KEY="$OMLX_API_KEY" research-runner:latest python -m lib.cli "$@"`.
   - The `--backend=ollama` path is intentionally not supported in v1.
5. Add `lib/omlx.py` — shared OpenAI-compatible client wrapping `requests`. Exposes `chat(model, messages, **kw) -> str` and `embed(model, inputs) -> list[list[float]]`, plus `list_models() -> list[dict]`. Centralises base URL, API-key header, and timeouts (default 20 min, matching the recent `vane-eval` POST timeout bump per `599f0a3`). Every later phase uses this helper rather than calling `requests` directly.
6. Add `lib/smoke.py` with three checks: SearXNG reachable, omlx `/v1/models` lists at least one chat model and one embedding model, embedder returns a vector for the string "test". Bootstrap calls it via `--smoke` flag. Fail loud (non-zero exit + clear error) if `OMLX_API_KEY` is unset.
7. Write `tests/local-research/README.md` documenting bootstrap usage, env-var overrides, and the session output layout.

### Acceptance criteria

- `./tests/local-research/bootstrap.sh --smoke` prints non-empty results for SearXNG (`?q=test&format=json`), the omlx `/v1/models` listing (showing at least one chat model and the embedder), and a single embedding vector for the string "test".
- Squid proxy is exercised end-to-end: a fetch from inside the runner produces a fresh entry in the Squid access log (whichever path `research.py` exposes — implementation detail).
- A `curl https://example.com` from inside the runner succeeds (Squid + denylist allows generic web).

---

## Phase 2: per-round search + rerank pipeline

### Steps

1. `lib/expand.py` — query expansion. Single function `expand(query: str, n: int = 4) -> list[str]`. Calls `EXPAND_MODEL` with a fixed prompt asking for N alternative phrasings (different angles: technical-vs-lay, narrow-vs-broad, alternative terminology). Parse one-per-line. Return `[query, *expansions]` so the original is always queried.
2. `lib/search.py` — SearXNG client. `search(query: str, n: int = 20) -> list[dict]` issues GET to `{SEARXNG_URL}/search?q={...}&format=json` and returns the `results` array.
3. `lib/rerank.py` — embedding rerank against omlx.
   - `embed(texts: list[str]) -> list[list[float]]` — POST to `{OMLX_BASE_URL}/embeddings` with `{"model": EMBED_MODEL, "input": texts}`. Attach `Authorization: Bearer $OMLX_API_KEY` header when the env var is set.
   - `rerank(query: str, results: list[dict], top_k: int = 15, exclude_urls: set[str] = None) -> list[dict]` — embed `query` and each result's `f"{title}\n{content}"`; cosine-rank against the query embedding; return top_k. Dedupe by URL before reranking. Drop URLs in `exclude_urls` (cross-round dedupe) before scoring. Preserve original `engine` and `score` and add `rerank_score` to the returned dicts.
4. `lib/pipeline.py` — `gather_sources(query: str, exclude_urls: set[str] = None) -> dict` calls `expand → search-each → flatten → dedupe → rerank`. Returns `{query, expansions, raw_results, ranked, timings}` for Phase 4's orchestrator. Pure orchestration; no I/O outside the helpers' subprocess/HTTP calls.
5. `test_lib.py` — unit tests for rerank ordering, expansion-level URL dedupe, cross-round URL dedupe, and SearXNG JSON fixture parsing.

### Acceptance criteria

- `gather_sources("medial knee pain cyclists")` (q3) runs end-to-end and the ranked top-15 contains visibly more medical / sports-medicine sources than the Vane run for the same question.
- Ranked-list ordering changes meaningfully when query phrasing varies — confirms the embedder is actually being hit (not silently no-op'd).
- Passing `exclude_urls={url1, url2}` drops those URLs from the ranked output even if they would otherwise rank top.

---

## Phase 3: fetch + extract + per-source notes

### Steps

1. `lib/fetch.py` — `fetch(url: str, timeout_s: int = 30) -> tuple[str, dict]`. Issue HTTP GET via `requests` (proxy env vars are honoured automatically). Returns `(body, meta)` where meta has `status`, `final_url`, `latency_s`, `bytes`. Raises a structured exception on 4xx/5xx/timeout/proxy-deny.
2. `lib/extract.py` — `extract(html: str, url: str) -> str`. Wraps `trafilatura.extract(html, output_format='markdown', include_links=False, include_comments=False)`. Falls back to a regex strip-tags + `<title>` if trafilatura returns None or `<500` chars. Returns markdown.
3. `lib/notes.py` — `note_for_source(query, source_text, meta) -> str`. Calls `NOTES_MODEL` with a fixed prompt asking for 4–8 query-relevant bullets covering central claim, specific facts/quotes, caveats; "irrelevant" line if the source doesn't apply. Truncate source text per Unknowns #9. Sequential per Unknowns #3.
4. `lib/bundle.py` — `write_source(...)` writes `<round_dir>/sources/<idx:02d>-<slug>.md` with a YAML frontmatter block (URL, title, round, branch, timings, model, engine, rerank score) followed by note and extracted-text sections. Exact field set decided at the keyboard.
5. Extend `pipeline.py` with `fetch_and_note(ranked, round_dir, round_idx, branch_label)`. For each ranked entry: fetch → extract → note → write_source. Returns source-meta dicts for the round digest. Per-source progress to stderr; per-source failure logs and continues, recorded in the round's manifest fragment.

### Acceptance criteria

- A run on q3's ranked top-15 produces source markdown where the note section is query-relevant and concise, and the extracted section is clean prose.
- Per-source phase completes within budget (~60s/source). Notably worse → env-var override to E4B for note generation is documented.
- Trafilatura fallback fires on a known JS-heavy page and produces reasonable content.
- A forced 404 / timeout on one source does not abort the batch; the failure is recorded in the round manifest fragment.

---

## Phase 4: round orchestration (branch proposal, dedupe, per-round digest, termination)

This phase builds the loop layer that Phase 2–3 plugs into. Branch proposal uses gap-driven reflection (per Phase 9's literature review). Termination scaffolding lands here; the actual `--batch` policy lives in Phase 9.

### Steps

1. `lib/round_state.py` — accumulator + URL-dedupe registry across rounds. Tracks seen URLs, accumulated source metas (with their round and branch labels), digest paths, branch history, and round count. Pickled per round for crash debug; not used for warm-resume. Canonicalize URLs before insert: strip tracking query params (`utm_*`, `srsltid`, `fbclid`, `gclid`, `ref`), normalize percent-encoding (`%27` → `'`), drop fragments, lowercase scheme+host. Phase 2 testing surfaced the same Incrediwear page appearing 3× under different `srsltid` values and Physiopedia twice via `%27` vs `'`; canonicalization here also tightens within-round dedupe in `lib/rerank.py` if reused.
2. `lib/branch.py` — `propose_branches(seed_query, accumulated_digests, k=4)`. Calls `NOTES_MODEL` with a gap-driven prompt: read accumulated digests, name K follow-up queries targeting still-missing facts or unexplored peripheral domains. Output one per line as `query | rationale` (instruct format explicitly in the prompt). Parser is tolerant: strip leading bullets/numbers, split each non-empty line on the first `|`. Round 1 has no digests; the seed query is the only branch.
3. `lib/digest.py` — `digest_round(round_idx, source_metas)`. Calls `NOTES_MODEL` with a prompt asking for an 800–1200 word digest covering what was learned, which subdomains were touched, and what remains unanswered, with `[r.N]` round-local citations. Written to `rounds/<n>/digest.md`. Feeds back into branch proposal and final synthesis.
4. `lib/orchestrate.py` — top-level `research(seed_query, mode)` drives the round loop. Per round: branch proposal → (interactive: branch gate) → per-branch `gather_sources(exclude_urls=state.seen_urls)` → top-K rerank → (interactive: source gate, combined across branches) → fetch + notes → digest → update state → continue? Continuation is a callable injected by the CLI: interactive returns the user's round-end gate; batch calls `lib/batch.should_stop` (Phase 9). Final round-end emits `synthesis.md` via Phase 7.
5. Acceptance harness: a forced 2-round driver in `test_orchestrate.py` that bypasses the continuation callable. Validates Phase 4 in isolation before Phase 8/9 wire up real termination.

### Acceptance criteria

- A 2-round forced run on q3 produces `rounds/01/` and `rounds/02/` dirs, each with sources and a digest. URLs from round 01 do not reappear in round 02's `sources/`.
- `propose_branches` after round 01 of q3 returns 3–4 queries that name distinct subdomains (e.g., "saphenous nerve compression cycling," "training-load progression knee pain," "iliotibial vs medial meniscus diagnosis").
- A round-01 digest lands ≤5k tokens (per Unknowns #9) and is readable as a standalone summary.
- `state.pkl` after the run contains the complete accumulated-sources list with round and branch labels.

---

## Phase 5: source-quality biasing (input-side levers)

This phase tunes what enters the funnel before rerank, so rerank and synthesis aren't being asked to make silk purses out of SEO sows' ears. Two priority goals: (1) bias toward science and considered editorial takes (e.g., a Noah-Smith Substack post outranks a marketing blog with the same finance keyword); (2) bias away from promotional / marketing / listicle / fluff content that excels at SEO but carries no signal. Holds rerank, fetch, extract, and synthesis constant — the candidate set is the variable. Inherits Phase 4's bundle layout and dedupe registry; nothing downstream changes shape.

### Steps

1. **Baseline capture.** Run `gather_sources` on 3 fixture queries — q3 (medical), one finance/economics query targeting the Noah-Smith-vs-marketing-blog case, one general-interest query — and save raw + ranked lists as `tests/local-research/eval/source-bias/baseline-<query-slug>.json`. Hand-label each result with one of `{science, editorial-considered, seo-fluff, listicle, marketing, other}`. This rubric is reused for every variant cell below; treat the labels as the metric.

2. **Lever A — expansion prompt.** Add `EXPAND_PROMPT` env override in `lib/expand.py` so prompts are swappable without code edits. Try ≥3 variants:
   - current generic "different angles" prompt (control)
   - **scholarly-tilt**: require one expansion as a quoted exact phrase, one with `site:edu OR site:gov`, one with a methodology term ("meta-analysis", "review article", "longitudinal study", "RCT"), one in field-jargon register
   - **anti-SEO**: prohibit listicle/marketing phrasings ("best", "top N", "guide to", "how to", "X vs Y"); require one "evidence on …" / "what the research says about …" form

3. **Lever B — expansion model.** Compare `gemma-4-E4B-it` (current) vs `gemma-4-26b-a4b-it` for the same prompt. Small models often produce bland rewordings; size may matter more for vocabulary breadth than for instruction-following. Hold the prompt fixed across this lever — one axis at a time.

4. **Lever C — SearXNG categories per expansion.** Extend `lib/search.py:search()` to accept `categories` and `engines` kwargs (SearXNG-native). Route the seed expansion to the default mix; route scholarly-tilt expansions to `categories=science` (arxiv, scholar, semantic-scholar, pubmed-if-enabled). Compare label distribution vs the round-robin all-engines control.

5. **Lever D — pagination.** Add `pageno` loop in `lib/search.py` (P pages per query, default 1). Hypothesis: page 2+ dilutes SEO surface and pulls in deeper-cut results that low-SEO editorial sources lose on page 1. Test P ∈ {1, 2, 3}; record marginal new domains and label-distribution shift per added page.

6. **Lever E — domain priors at the SearXNG-result stage.** Add `lib/source_priors.py` with two static lists (env-var path overridable): `boost_domains` (`.edu`, `.gov`, `arxiv.org`, `nih.gov`, `nature.com`, `science.org`, `*.substack.com` for known long-form, named editorial outlets) and `penalize_patterns` (URL substrings: `/best-`, `/top-10-`, `/top-N-`, `/guide-to-`, `/vs-`; query strings carrying `utm_`; recognized free-tier marketing-blog hosts). Apply additive log-odds shifts to each raw result's score *before* rerank consumes it. This is the only lever that downweights without changing the candidate set; A–D shape the candidate set itself.

7. **Reporting.** `tests/local-research/eval/source-bias/MANIFEST.md` with one row per (query × lever-cell): label distribution before vs after, count of `science + editorial-considered`, count of `seo-fluff + listicle + marketing`, top-5 first-page diff vs baseline, qualitative one-line note. Per-axis marginal means at the top.

### Acceptance criteria

- For at least one query, a (prompt × categories × prior) combination raises `science + editorial-considered` share by ≥30 percentage points and lowers `seo + listicle + marketing` share by ≥30 pp vs baseline.
- Per-lever marginal effect sizes recorded in `MANIFEST.md` so a future re-run can drop dominated levers.
- Defaults updated in-place — `EXPAND_PROMPT` text, expansion-model assignment, search-call categories, pagination depth, and `source_priors.py` lists — to the winning combination. No compat shim, no flag for "old behavior" (matches the project's commit-style note). Downstream phases inherit these defaults.

### Notes
- Hold `EMBED_MODEL` and rerank logic constant. Rerank tuning is a separate phase if it ever earns one.
- SEO sites are sometimes the only result available on a given query. Lever E alone can't help when the input list is uniformly fluffy — A–D are the corrective there.
- Out of scope: trained relevance/quality classifier, click-through priors, per-domain scraping etiquette tuning, automated rubric labelling.

---

## Phase 6: SearXNG-config tuning loop (Sonnet-driven)

This phase tunes SearXNG's `settings.yml` itself — the layer Phase 5 left untouched. Phase 5's wins live in the runner (Python code, call-site args, post-result domain priors); this phase's wins live in `start-agent.sh`'s `SEARXNG_SETTINGS_FILE` heredoc (line ~616) — that's the SearXNG instance the agent has docker access to. After the loop converges, port the same engine list / weights / plugin selection to `render_searxng_settings()` at `research.py:428` so Phase 7+ inherits the win on the research VM too. Same rubric as Phase 5 (`{science, editorial-considered, seo-fluff, listicle, marketing, other}`); same fixture queries; same input/output shape into the rest of the pipeline. The variable is `settings.yml`, and only `settings.yml`. Phase 5's chosen defaults are held constant so the marginal effect of SearXNG-side tuning is isolable.

**Environment.** The agent runs the loop from inside the `claude-agent` container in the Colima VM. `start-agent.sh` bind-mounts `~/.claude-agent/searxng/` rw at `/host/searxng/` and exposes `/var/run/docker.sock`, so the agent can write `settings.yml` and `docker restart searxng` directly. Searches go to `http://searxng:8080` (the same VM-local SearXNG). No `research-runner`, no `bootstrap.sh --score-searxng` round-trip — the harness runs in-place.

Methodology, by user request: **the model executing this step is itself the loop.** No `run_loop.py`, no driver script, no in-Python `while True` calling an LLM API. The agent reads `iterations.jsonl` with Read, picks the next mutation, writes `settings.yml` with Write, runs the scorer with Bash, reads the new score, decides keep/revert, and repeats — turn by turn — for ~1 h. The environment is set up specifically to make this work: liberal egress, dedicated SearXNG, no permission prompts on the relevant tool calls. The plan provides the search space, the harness, the scoring function, and the stop condition; the agent picks the trajectory using its own judgment, one Bash/Edit/Read turn per iteration.

End goal: best mechanical floor on candidate-set quality from SearXNG alone, so the LLM (Phase 7's synthesizer) and the human (interactive review in Phase 8) see fewer marketing pillars, ad pages, listicles, and SEO fluff and more science / considered editorial.

### Steps

1. **Freeze the upstream baseline.** Run `gather_sources` on the 3 fixture queries from Phase 5 step 1 with Phase 5's chosen defaults applied. Save under `tests/local-research/eval/searxng-config/baseline-<query-slug>.json`. Reuse Phase 5's hand-labels where the URL is unchanged; LLM-judge label new URLs (see step 3). This baseline is Phase 6's reference for "did the SearXNG change help".

2. **Enumerate the search space.** SearXNG-native levers worth varying — verify each option's name and shape against the running container's `/etc/searxng/settings.yml` and `/usr/local/searxng/searx/engines/` per Unknowns #10 before treating any as drop-in:
   - **Engine list.** Add candidates: `pubmed`, `crossref`, `core.ac.uk`, `openairepository`, `lobste.rs`, `hackernews`, `peertube`. Disable any engine that consistently surfaces SEO-pillar content (likely candidates: `brave` for some categories). The current 9-engine list is in `render_searxng_settings()` at `research.py:432`; treat it as the starting point.
   - **Per-engine `weight`.** SearXNG's engine entry accepts `weight: <float>` to bias the merged-result rank. Boost arxiv, scholar, semantic-scholar, wikipedia, pubmed; deweight google, bing, brave (which over-represent SEO surface).
   - **`hostnames` plugin.** SearXNG-native `low_priority` / `high_priority` / `remove` regex lists at the result-aggregation stage (before SearXNG returns to the caller). This is the SearXNG-side equivalent of Phase 5's Lever E. Crucial contrast: Phase 5's Lever E filters within the runner's already-fetched result set; the `hostnames` plugin filters or reweights before SearXNG even returns. They may stack additively; record explicitly when they don't.
   - **`enabled_plugins` / `disabled_plugins`.** `tracker_url_remover` strips `utm_*`, `srsltid`, `gclid` etc. before result aggregation, which can collapse cross-engine duplicates. Overlaps with Phase 4's `lib/round_state.py` URL canonicalization but acts earlier; both can coexist. `oa_doi_rewrite` rewrites publisher-paywall DOIs to open-access mirrors — high-leverage for the science-tilt goal.
   - **`search.safe_search`.** `0/1/2`. Higher values may filter adversarial / promotional content as a side effect; document the trade-off if it also drops legitimate content.
   - **Per-engine `categories` overrides.** Reassign engines so the `science` category surfaces only science-tilt engines; the `general` category remains the SEO-heavy mix. This sharpens Phase 5's Lever C (per-expansion category routing).
   - **Per-engine `timeout`.** Slow but quality engines (semantic-scholar) get dropped on default timeout (~3s). Bumping to ~10s brings them into the merged result set.

3. **Add the iteration harness.** `tests/local-research/eval/searxng_config/iterate.py` — a small (~70 line) stdlib-only Python script. Deliberately scorer-less and pipeline-less — the agent driving the loop is the judge, and we want to expose **what SearXNG itself returns**, not what Phase 5's expand+rerank+priors stack returns (the pipeline would mask the very signal we're tuning).
   - **Restart + poll.** When invoked with `--restart`, calls `docker restart searxng` and polls `http://searxng:8080/search?q=test&format=json` until 200 (60s deadline).
   - **Capture.** Hits `http://searxng:8080/search?q=<fixture>&format=json` directly for each of the 3 fixture queries (q3 medical, creatine, finance-team) with no expansion / rerank / priors layered on; records the top-N raw results (`--top-n`, default 15: `url`, `title`, `content` snippet, `engines`).
   - **Append.** Writes a row to `tests/local-research/eval/searxng_config/iterations.jsonl` with `{iter, ts, settings_sha, top_n, axis_touched, mutation_summary, rationale, kept_or_reverted, top_ranked_per_query}`. `axis_touched` and `mutation_summary` are passed in via flags so the agent can record what it just changed; `rationale` and `kept_or_reverted` are left blank by the harness and patched in by the agent's same-turn `Edit`. Any constrained-field drift the agent might inadvertently introduce (`secret_key`, `base_url`, `outgoing.proxies`) is the agent's responsibility to avoid — there's no programmatic guard, but the heredoc seed in `start-agent.sh` is the easy reset.
   - **No labels, no score number, no LLM judge, no pipeline import.** Drop them all. `iterate.py` is stdlib-only (`json`, `urllib.request`, `subprocess`, `pathlib`, `argparse`, `datetime`, `hashlib`, `time`). Throwaway harness; what survives this phase is the winning `settings.yml` content (ported into `start-agent.sh` and `research.py`) and `RESULTS.md`.

4. **Be the loop.** *You — the model executing this step — are the iteration driver.* Do not write a script that drives the loop; do not delegate to omlx, ollama, or any in-process LLM call; do not hand a "run this" command back to the user. The user has explicitly opted out of the loop. Run it yourself, agent-turn by agent-turn, until a stop condition fires.

   Per-turn protocol (one model turn = one iteration):
   - **Read** `tests/local-research/eval/searxng_config/iterations.jsonl` (and the current `/host/searxng/settings.yml`) to recover prior trajectory. Skim the previous row's `top_ranked_per_query` lists; that's the evidence base.
   - **Decide** the next mutation. One axis per iteration. Start broad — engine list, per-engine `weight` — then narrow to `hostnames`, plugin lists, `search.safe_search`, per-engine `timeout`. If two changes look orthogonal, run them serially before stacking, so the row-level marginal effect is interpretable later.
   - **Write** the new `settings.yml` to `/host/searxng/settings.yml` with Write or Edit.
   - **Run** the harness with Bash: `python3 tests/local-research/eval/searxng_config/iterate.py --restart --top-n 15 --axis-touched <axis> --mutation-summary "<one line>"`. The harness restarts `searxng`, polls until ready, runs the 3 fixture queries, and appends a fresh row to `iterations.jsonl`. (Restart latency, ~10–20 s, dominates wall-clock — that's the per-iteration floor.)
   - **Judge** the appended row directly. Read the new `top_ranked_per_query` lists; compare against the prior row's lists and the running trajectory. There's no auto-score — the agent forms the verdict.
   - **Patch** the row in-place (Edit) to set `rationale` (one sentence audit-quality reasoning) and `kept_or_reverted`. If reverted, restore the prior `settings.yml` before the next turn.

   Discipline:
   - Each `rationale` is one sentence the next iteration's reader can audit. Short, specific, mechanical (e.g., "added `pubmed`; q3 top-5 now shows two PubMed cohort studies that weren't in the prior row, kept").
   - Constrained fields the harness rejects: `secret_key`, `base_url`, `outgoing.proxies` block. The loop touches only `engines:`, `enabled_plugins:`, `disabled_plugins:`, `hostnames:`, `search:`, and per-engine entries.
   - If the loop runs out of budget on engine-list churn alone, defer plugin/timeout axes to a follow-up rather than truncate mid-axis.

   Stop conditions, whichever fires first — record the reason on the final row's `stop_reason` field, then stop:
   - wall-clock ≥ 60 min from the first iteration of the session,
   - 5 consecutive iterations the agent judges as no-improvement (each `kept_or_reverted: reverted` or `kept` with rationale "no meaningful change"),
   - the agent declares the search saturated for the chosen axes and explicitly stops.

   Operational notes for the executing agent:
   - Use `/loop` dynamic mode or just stay in the conversation and chain turns directly — either works; pick whichever keeps the per-turn context tight.
   - Do not summarize each turn back to the user. The audit trail lives in `iterations.jsonl`. A single end-of-loop summary referencing row IDs is enough.
   - If the harness hangs or returns malformed output, fix the underlying issue (or revert the offending settings.yml) — do not paper over with retries that pollute the iteration record.

5. **Commit the winning config in place.** Update the `SXNG` heredoc inside the `LOCAL_SEARCH_ENABLED` block of `start-agent.sh` (line ~616 — the `cat > "$SEARXNG_SETTINGS_FILE"` block) so future `start-agent.sh` runs seed the winning config on first launch. Then port the same engine list / weights / plugin selection to `render_searxng_settings()` at `research.py:428` so downstream Phase 7+ runs against `research-searxng` inherit the win. Match the existing template style in each file; do not introduce a separate config file. No compat shim for "old SearXNG config" — matches the project's no-compat-shims commit style. The seed runs only on first launch; existing `~/.claude-agent/searxng/settings.yml` files keep whatever the loop converged on.

6. **Write the explanation.** `tests/local-research/eval/searxng_config/RESULTS.md`:
   - Per-iteration trajectory: ordered list of `(iter, axis_touched, mutation_summary, kept_or_reverted, rationale)`. The `rationale` column is the trajectory; there is no score column.
   - Per-axis takeaway (which axis the agent kept the most mutations on; which axis converged fastest).
   - Top-N kept knobs, with one-sentence rationale per knob (drawn from the iteration row that adopted the knob).
   - Per-query before/after observations: name 2–3 specific URLs that disappeared and 2–3 that appeared, per fixture query.
   - Anything surprising — mutations that the agent expected to help and didn't, or vice versa.

7. **Write the downstream-orchestration follow-ups.** Append to `RESULTS.md` a section "Implications for Phase 8/9 round orchestration" — guidance for the LLM downstream (branch proposal in Phase 4; round selection in Phase 9; synthesizer prompts in Phase 7). Examples of the *kind* of insight that should land here (don't fabricate these — derive each one from what the iteration actually surfaced, citing iteration row IDs):
   - "arxiv `weight: 3.0` now dominates page 1 for science-tilt queries — branch proposal should issue at least one explicit non-arxiv expansion per round to recover web-of-knowledge citations from secondary literature."
   - "`pubmed` enabled but only fires usefully for medical queries — Phase 4's `propose_branches` should suppress medical-jargon expansions for non-medical seeds, otherwise a round wastes budget on PubMed misses."
   - "the `hostnames.low_priority` list now downweights ~12 SEO hosts before aggregation — Phase 5's `lib/source_priors.py` `penalize_patterns` for those same hosts is now redundant; remove to keep one source of truth."
   - "`oa_doi_rewrite` plugin returned more open-access PDFs in the candidate set; Phase 7 synthesis can lean harder on full-text citations rather than abstracts."
   These are *follow-ups for the next planning turn*, not implementation tasks for this phase. List them clearly so the next `/plan` invocation can pick them up.

### Acceptance criteria

- A working `settings.yml` lives in `start-agent.sh`'s `SXNG` heredoc (~line 616), and `searxng` starts cleanly with it (`docker logs searxng` clean; `http://searxng:8080/search?q=test&format=json` returns ≥ 5 results across at least 3 distinct engines). The same config has been ported to `render_searxng_settings()` at `research.py:428`.
- The agent's end-of-loop summary in `RESULTS.md` calls out at least one fixture query where the kept settings produced a qualitatively better top-15 (e.g., new science/editorial URLs that the prior baseline had missed, or specific SEO/listicle URLs that disappeared), citing the iteration row that drove the win.
- `iterations.jsonl` records ≥ 20 iterations, each with `top_ranked_per_query`, `axis_touched`, `mutation_summary`, `rationale`, `kept_or_reverted`; the final row carries a `stop_reason` field.
- `RESULTS.md` exists with: per-iteration trajectory (rationale column), per-axis takeaway, top-N kept knobs, per-query before/after URL observations, and ≥ 3 concrete downstream-orchestration follow-ups derived from specific iteration rows (not generic priors).
- Cross-VM isolation unchanged: `tests/test-cross-vm-isolation.sh` still passes.

### Notes
- Holds Phase 5's chosen defaults constant. If a Phase 6 win conflicts with a Phase 5 default (e.g., `tracker_url_remover` makes the runner-side URL canonicalization redundant), record in `RESULTS.md` as a follow-up; do not retroactively edit Phase 5 in this phase.
- The agent IS the judge. There is no auto-labeler, no score number gating decisions, and no LLM rubric classifier. If a future plan wants a quantitative trajectory chart, derive it post-hoc from the saved `top_ranked_per_query` lists; do not retrofit a scorer into the loop itself.
- Iteration record is the audit trail. Treat `iterations.jsonl` as load-bearing — if a future plan revisits SearXNG tuning, it should be able to read this file end-to-end and reconstruct the trajectory from the agent's prose rationales + the raw URL lists.
- Out of scope: trained relevance classifier, click-through priors, per-engine API key acquisition, SearXNG core code changes, adding new third-party engines that require pip-installing extra packages into the SearXNG image.

---

## Phase 7: hierarchical synthesis + bundle output

### Steps

1. `lib/synthesize.py` — `synthesize(seed_query, digests, top_source_metas, config=DEFAULT)`. Caller (orchestrator or sweep driver) assembles `digests` and `top_source_metas` per `config.context_shape` and passes them in; the function builds the prompt and calls the model. Top-K source notes are ranked against the seed query — re-rerank all accumulated sources against the seed embedding before selection, since per-round rerank scores were computed against per-round branch queries and aren't directly comparable. Anchor sources `[1]..[K]`, digests `[R1]..[RN]`.

   `SynthConfig` is a frozen dataclass with five orthogonal axes — `context_shape` ∈ `{digests_only, digests_plus_topk, raw_notes}`, `model` (str), `prompt_template` ∈ `{free_form, structured}`, `thinking` (bool), `top_k` (int, default 30). Provides `slug()` for filenames and `to_dict()` for manifest rows. Default ships with `digests_plus_topk`, `SYNTH_MODEL` env var (or 26b-a4b), `structured`, `thinking=False`, `top_k=30`. Phase 10 may overturn any axis.

   `structured` template: TL;DR (3 sentences), key claims with `[N]` source citations and `[Rn]` round refs where helpful, contradictions/uncertainties between sources, gaps that retrieval did not fill. `free_form` is an unstructured "synthesise these sources" instruction.
2. Extend `lib/bundle.py` to write `query.md`, per-round dirs `rounds/<n>/{sources/, digest.md}`, `synthesis.md`, and `manifest.md` (rounds, models, timings, source counts per round, gates passed, termination reason). API shape decided at the keyboard.
3. `lib/handoff.py` — `prepare_handoff(session_dir: Path) -> Path`. Concatenates `query.md` + per-round digests + top-K source notes (note sections only) + `synthesis.md` into a single `handoff.md` at the session-dir root. Tree form is canonical; handoff is convenience.

### Acceptance criteria

- A non-interactive 3-round run on q1 produces a `synthesis.md` that cites the EC margin and popular-vote outcome correctly with `[N]` anchors that resolve to actual `rounds/<r>/sources/<m>-*.md` files.
- Synthesis-stage wall-clock on 26b-a4b with hierarchical input (digests + top-30 notes, ≤40–50k input tokens) lands ≤8 min.
- `handoff.md` is self-contained: no broken citation refs, no embedded extracted-text walls.

---

## Phase 8: interactive CLI with multi-round gates

### Steps

1. `lib/cli.py` — entrypoint `python -m lib.cli "user query"`. Drives the orchestrator with `2N+1` gates per session. Each gate accepts: approve (enter), drop indices (comma list), add (`+text` for queries / URLs), abort (`q`); compose actions on one line.
   - Pre-round (rounds ≥ 2 only): K proposed branches with rationales. Round 1 skips this gate.
   - Per-round source review (after each round's fetch + notes): combined source list across the round's branches, each line with domain, title, engine, branch label, snippet, note first sentence.
   - Round-end gate: round digest first sentence + accumulated source count. Choices: continue another round, stop and synthesise, abort.
   - Final synthesis gate: accumulated source count + each round's digest first sentence. Confirm or abort.
   - Synthesis stage: print model + rough time hint (≤8 min for 26b-a4b, 10–20 min for 31b). Print on completion (no streaming — synchronous POST is simpler than SSE parsing).
   - Exit prints session-dir path on stdout.
2. `--batch` flag: skip all gates. Branches from `propose_branches`; round count and termination governed by Phase 9's `should_stop`.
3. `--no-synth` flag: stop after the final round-end gate.

### Acceptance criteria

- An interactive 2-round run on a personal query reaches all gate types with clear summaries; index parsing matches indices shown; gate edits propagate correctly to the next stage's input set.
- `--batch --no-synth` exits cleanly with a bundle that has every round's artifacts plus `handoff.md` but no `synthesis.md`.
- `--batch` end-to-end on q1 produces a bundle equivalent in shape to the manual approval flow.

---

## Phase 9: --batch mode + q1–q6 regression eval (Opus recommended)

This phase's batch logic is judgment-heavy because there's no human in the loop and termination has to be defensible. Bakes in research from recent deep-research literature.

### Background (research summary)

Across GPT-Researcher, LangGraph's open-deep-research, local-deep-researcher, OpenAI Deep Research, and Perplexity Deep Research, the dominant patterns for autonomous iterative research are:

- **Branch surfacing: gap-driven reflection.** LLM reads accumulated digests + original query and proposes K=3–5 follow-up queries naming missing facts. STORM is the outlier (persona-driven), but every shipping system without RL training uses gap-driven. (Sources: [LangChain open-deep-research](https://blog.langchain.com/open-deep-research/), [GPT-Researcher deep mode](https://docs.gptr.dev/docs/gpt-researcher/gptr/deep_research), [Stanford STORM arxiv:2402.14207](https://arxiv.org/abs/2402.14207).)
- **Termination: hybrid hard cap + early-stop heuristic.** [Stop-RAG (arxiv:2510.14337, Oct 2025)](https://arxiv.org/abs/2510.14337) shows that prompted LLM self-assessment underperforms a fixed cap on accuracy because models stop too early; only a learned value head reliably beats a fixed cap. Without training, the right move is hard cap + a structural-novelty floor.
- **Budgets in production:** GPT-Researcher: `MAX_ITERATIONS=3`. LangGraph: `max_research_loops=3`. Perplexity Deep Research: 3–5 sequential rounds, 100–300 sources cited. OpenAI Deep Research: dozens of queries, 20–50+ sources typical. (Sources: [GPT-Researcher config](https://docs.gptr.dev/docs/gpt-researcher/gptr/config), [Perplexity Deep Research](https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research), [OpenAI Deep Research](https://openai.com/index/introducing-deep-research/).)

### Steps

1. `lib/batch.py` — implements `should_stop(state: RoundState) -> tuple[bool, str]`. Returns `(stop, reason)` where reason is a short human-readable string written into `manifest.md`. Logic, in order:
   - Hard cap: `state.round_count >= MAX_ROUNDS` (default 4) → stop, reason `"max_rounds"`.
   - Source cap: `len(state.accumulated_sources) >= MAX_SOURCES` (default 80) → stop, reason `"max_sources"`.
   - Novelty floor (only if `round_count >= 2`): compute `new_unique_domains = |this_round_domains - prior_domains|` and `new_high_rerank = count(this_round_sources where rerank_score >= threshold)`. Stop if `new_unique_domains / total_domains < 0.15` AND `new_high_rerank < 5`. Reason `"diminishing_returns"`.
   - Threshold for `high_rerank` is per-run normalised per Unknowns #8 — sigma multiple env-overridable; calibrated from a real q1 run.
   - Skip an LLM "do I have enough?" gate per Stop-RAG's empirical result.
2. `tests/local-research/eval/run_q1q6.py`:
   - Parse `experiments/vane-eval/queries.md`.
   - For each q in q1..q6: invoke the CLI in `--batch` mode (full pipeline including final synthesis). Save bundles under `tests/local-research/eval/results/local-research-<UTC-timestamp>/q<n>/`.
   - Optionally also a `--no-synth` variant per query for frontier handoff.
3. `tests/local-research/eval/manifest.py` — write a top-level `MANIFEST.md` with per-cell status, latency, round count, source count, termination reason, and file links.

### Acceptance criteria

- A q1-only batch run terminates within 4 rounds, records a termination reason, and parses `queries.md` correctly.
- Comparison vs. the Vane run: q3 source bundles contain PubMed / sports-medicine domains (or alternative authoritative medical sources), and at least one q3 source's note mentions saphenous nerve or training-load increase. If yes → architecture has closed the gap. If no → SearXNG engine list needs PubMed (research.py:432, outside this plan).
- Total wall-clock for the sweep lands under ~4 h (6 queries × up to ~30–40 min each, given more rounds and more sources than the earlier 15-source design).
- Termination breakdown across q1..q6 reported in `MANIFEST.md` — sanity check that not all queries hit `max_rounds` (would indicate the novelty heuristic is too lax) and not all queries stop at round 2 with `diminishing_returns` (would indicate the threshold is too aggressive).

---

## Phase 10: synthesis-quality evaluation harness (Opus recommended)

Replaces the earlier model bake-off. The right question is which input variables move synthesis quality and what mechanical scaffolding makes a small-N (~24-cell) eval productive.

### Background (research summary)

Across MT-Bench / Chatbot Arena, G-Eval, Prometheus 2, RAGAS, ALCE, ARES, and the 2024–2025 LLM-as-Judge surveys:

- **Small-N eval methodology.** A 4–6-dim structured rubric (faithfulness, citation accuracy, coverage, coherence, contradiction-handling), reference-based fact recall against per-query "must-hit" facts, and pairwise A/B for ties. LLM-as-judge as a cheap second opinion *only* with bias mitigations (length normalisation, no same-family writer + judge). (Sources: [MT-Bench / Chatbot Arena Zheng et al.](https://arxiv.org/html/2306.05685v4), [LLMs-as-Judges survey arxiv:2412.05579](https://arxiv.org/html/2412.05579v2), [Justice or Prejudice arxiv:2410.02736](https://arxiv.org/html/2410.02736v1).)
- **Citation grounding (mechanical).** ALCE-style `citation_precision` + `citation_recall` via NLI. v1 implementation: parse `[N]` markers, decompose synthesis into atomic claims, run an LLM judge with a 3-class entailment prompt over `(claim, source-N-bullets)` pairs. ~50 LOC, no model finetune. (Sources: [ALCE](https://ar5iv.labs.arxiv.org/html/2305.14627), [RAGAS faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/), [ARES arxiv:2311.09476](https://arxiv.org/abs/2311.09476).)
- **Input-variable effect ordering** (largest first, per ["Do MDS Models Synthesize?" TACL 2024](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00687/124262/Do-Multi-Document-Summarization-Models-Synthesize), [DeepResearch Bench](https://deepresearch-bench.github.io/static/papers/deepresearch-bench.pdf), and ALCE): **context-shape > model tier > prompt template > thinking toggle > citation-format demands**.

### Steps

1. Add a per-query reference file `tests/local-research/eval/references/q<n>.md` with: gold-answer paragraph, must-hit facts list, and known contradictions. Curate from `experiments/vane-eval/queries.md` and the prior eval's reference notes. Used by both fact-recall scoring and citation-grounding eval.
2. `tests/local-research/eval/run_synth_sweep.py`:
   - Take a Phase 9 run-dir as input (`--from <run-dir>`); reuse its per-round notes + digests verbatim. Synthesis is the only variable.
   - 24-cell sweep per query: 3 (context-shape) × 2 (model tier) × 2 (prompt template) × 2 (thinking toggle).
     - context-shape: `{digests-only, digests + top-30 notes, raw-notes}`. With ~50–100 sources × ~500 tokens per note, raw-notes lands ~25–50k tokens — within the claimed 250k window and within the long-context-degradation ceiling from Unknowns #9. If a cell exceeds the synth model's effective limit on a longer run, log and skip rather than crash.
     - model tier: `{26b-a4b, 31b}`
     - prompt template: `{free-form, structured (TL;DR/claims/contradictions/gaps)}`
     - thinking: `{off, on}` (omlx flag if available; skip cells if unsupported)
   - Sweep driver iterates the Cartesian product of `SynthConfig` axes (Phase 7), calling `synthesize(...)` once per cell. File names use `config.slug()`; `manifest.md` rows use `config.to_dict()`. Save outputs as `synthesis-<slug>.md` under `tests/local-research/eval/results/synth-sweep-<UTC-timestamp>/q<n>/`. Record per-call latency and (if reported) `usage` tokens.
   - Sequential per Unknowns #3.
3. `lib/eval/citation_grounding.py` — ALCE-style mechanical metrics. Parse `[N]` markers per sentence, run an LLM judge with a 3-class entailment prompt (entailed / partial / unsupported) over `(claim, cited-source-bullets)` pairs, aggregate to `citation_precision = supported / cited` and `citation_recall = supported_claims / total_claims`. Use a different model family from the writer to mitigate self-preference bias.
4. `lib/eval/fact_recall.py` — `score_fact_hits(synthesis, must_hits)`. Regex match each must-hit against the synthesis; optional embedding-similarity fallback for paraphrased hits (rerank embedder, threshold per Unknowns #8).
5. `tests/local-research/eval/sweep_manifest.py` — per-query and aggregate `MANIFEST.md` with mechanical metrics per cell, per-axis marginal means at the top, and pairwise A/B markdown for the two hardest queries (q3, q6) to break rubric ties.
6. Document in `tests/local-research/README.md` how to run the sweep against any Phase 9 run-dir. Include a one-paragraph "what to read for in the rubric" note: prioritise faithfulness and citation accuracy, then coverage, then coherence, then contradiction-handling.

### Acceptance criteria

- A sweep against the Phase 9 q1 bundle produces up to 24 synthesis files with mechanical metrics in `MANIFEST.md`. No cell crashes (raw-notes cells may exceed effective context on long runs — log and skip rather than crash).
- Citation-precision and -recall numbers are non-trivially distinguished across cells (range > 0.1 between best and worst); confirms the metric is not floored.
- Per-axis marginal means show context-shape as the largest mover (per literature priors); if not, that's an interesting result and worth noting in the plan's Notes.
- Decision recorded in this plan's Notes section after the run: which (context-shape, model, prompt, thinking) combination becomes the new default for `synthesize()`. Lifted to a `--synth-config` CLI flag if the answer is "depends on query."

---

## Notes

**Per-stage model assignments** are env-var overridable via `bootstrap.sh`. Phase 10 sweeps over the synthesis-stage assignments specifically.

**No prompt-template versioning.** When a template's text changes, replace in place; rely on git tags for re-runnable historical bundles. Matches the project's no-compat-shims commit style.

**Hierarchical synthesis is structural, not a knob.** At 50–100 sources, no local model can fit all per-source notes in a single synthesis call. The per-round digest is the structural answer: synthesise within a round (≤15 sources fits easily), then synthesise over digests + a top-K of relevance-ranked sources. Keep digests as kept artifacts on disk for debuggability.

**Why no LLM self-assessment for batch termination.** Per [Stop-RAG (arxiv:2510.14337)](https://arxiv.org/abs/2510.14337), prompted LLM "do I have enough?" gates underperform a fixed-iterations cap on retrieval-QA accuracy because the model stops too early. A learned value head dominates both, but training one is out of scope. Hard cap + structural novelty floor is the strongest non-trained option.

**omlx is the only supported backend in v1.** The user's nomic embedder runs on omlx, and consolidating chat + embedding on a single OpenAI-compatible endpoint simplifies the client. Ollama can be added later as a separate config; not worth the abstraction overhead now.

**Why a new container, not extending research-vane.** Vane is a complex Node app and effectively a black box. The runner is a few hundred lines of Python. Joining `research-net` reuses the existing egress firewall (Squid + iptables `RESEARCH` chain) without copying any of it.

**Why `tests/local-research/` rather than top-level `local-research/`.** Daily-driver invocation is a single `bootstrap.sh`; path depth doesn't matter once aliased. Per-round dir layout (`rounds/<n>/...`) is for implementation debug; refactor for production once it's working.

**Parallelism deferred.** Per Unknowns #3, omlx may serialise concurrent calls anyway. Sequential per-source notes within a round keep stderr legible and make pacing natural. The 50–100-source target raises the stakes — revisit if profiling shows wall-clock pain.

**Phase 5 vs Phase 6 split.** Phase 5 changes runner-side Python; Phase 6 changes SearXNG's `settings.yml`. Both target the same metric (label distribution into rerank) with the same rubric. Order matters: Phase 5 first because its levers are cheaper to iterate (no container restart) and surface the rubric first; Phase 6 then squeezes the engine layer with Phase 5's wins held constant.

**Out of scope for v1:**
- TUI library / pretty UI.
- Cross-session corpus / persistent index. Sessions are independent dirs on disk.
- Multi-turn conversation per session. One query → one bundle → frontier chat for follow-ups if needed.
- Resume support (orchestrator state is pickled per round for crash debug, not for warm-resume).
- Learned value head for batch termination (Stop-RAG-style; would require training data).
- Frontier API integration. The bundle is the API; user pastes/uploads it manually.

**Adjacent risk worth flagging.** If the q3 retrieval gap persists after Phase 9 (saphenous nerve / training-load still absent from any retrieved source across multiple rounds), the cause is upstream of this harness — SearXNG's enabled engines don't index the relevant content. Phase 6 is where adding PubMed via SearXNG's `pubmed` engine should land naturally; if the Phase 6 Sonnet loop ends up not exploring engine additions, edit `render_searxng_settings()` at `research.py:432` directly as a pre-Phase-9 mitigation.
