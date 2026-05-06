# Phase 6 SearXNG Config Tuning — Results

21 iterations, ~31 minutes. Agent-as-judge loop. No Python pipeline, no rerank, no LLM judge — raw SearXNG candidates evaluated by the agent reading `top_ranked_per_query` from `iterations.jsonl`.

## Winning config

`sha=dc83ad2062d2` — committed to `start-agent.sh` and `research.py`.

Key knobs vs baseline:
| Axis | Baseline | Winner |
|---|---|---|
| Engine list | google, bing, duckduckgo, brave, qwant, wikipedia, arxiv, github, stackoverflow | Same minus bing/qwant; plus pubmed, google scholar |
| pubmed | not present | weight=3, timeout=15, categories=[general,science,"scientific publications"] |
| google scholar | not present | weight=1.5, science-only |
| arxiv | default weight, science-only | weight=1, science-only |
| google | default weight | weight=1.0 |
| brave | default weight | weight=0.5 |
| plugins | none | oa_doi_rewrite |
| hostnames | none | high_priority: NIH/nature/lancet/NEJM/LWW/clinorthop/BJSM; low_priority: verywellhealth/healthline/medicalnewstoday; remove: cookedbytaste/buckedup/ubiehealth |
| request_timeout | 3.0s (default) | 8.0s + max 15.0s |
| safe_search | default | 1 |

## Before / after: top-10 URLs per query

### q3 — cyclist medial knee pain (full question text)

| # | Baseline | Final |
|---|---|---|
| 1 | myvelofit.com — consumer cycling tips | bikeradar.com [duckduckgo+brave] |
| 2 | bikeradar.com | cyclingweekly.com [duckduckgo+brave] |
| 3 | complete-physio.co.uk | bicycling.com [duckduckgo+brave] |
| 4 | summitpt-nj.com | wmphysicaltherapy.com [duckduckgo+brave] |
| 5 | bikexchange.com | roadcyclingacademy.com [duckduckgo+brave] |
| 10 | cyclingweekly.com | **pmc.ncbi.nlm.nih.gov/PMC5717478** [brave] — PMC clinical article |

Note: q3 uses full question text ("A cyclist develops stubborn medial knee pain…"). PubMed requires short keyword queries; it returns 0 results for this fixture. The pipeline's expand.py + local model (Gemma 26B) rephrases long questions into short search terms — the pipeline path ("medial knee pain cycling", "saphenous nerve cycling knee") yields 15–19 PubMed results and surfaces journals.lww.com/clinorthop articles. The harness tests a worst-case scenario.

### creatine — "is creatine safe to take long term"

| # | Baseline | Final |
|---|---|---|
| 1 | mayoclinic.org | **pubmed (39408214)** — PubMed RCT |
| 2 | health.harvard.edu | **pubmed (28749884)** — PubMed review |
| 3 | uclahealth.org | **pubmed (15758854)** — PubMed review |
| 4 | verywellhealth.com | verywellhealth.com [duckduckgo+brave] |
| 5 | pubmed.ncbi.nlm.nih.gov/12701816 | health.harvard.edu |
| 8 | health.com | **oadoi.org/10.3389/fnut.2025.1578564** — open-access mirror |
| 10 | cookedbytaste.com | **oadoi.org/10.1186/s12970-021-00412-w** — open-access mirror |

3 PubMed abstract URLs + 2 oadoi.org open-access mirrors in top 10 vs 1 PubMed + 0 oadoi in baseline.

### finance-team — "is it unusual for a 60-person software consulting company to have a 4-person finance team"

| # | Baseline | Final |
|---|---|---|
| 1 | companysights.com | **getaleph.com** [duckduckgo+brave] |
| 2 | consultingsuccess.com | myconsultingoffer.org |
| 3 | reddit.com/r/CFO | companysights.com |
| 4 | cfohub.com | growcfo.net |
| 7 | getaleph.com | mostlymetrics.com |
| 8 | companysights.com (2nd URL) | **cfo.com** — finance benchmarks |

Both look good. Final has better diversity (no duplicate companysights). cfo.com (FTE-per-$1B metric) is a useful addition. No academic PDFs in top 10 (google scholar science-only config worked).

## Per-axis findings

### Engine list (iters 1, 5, 8, 9/10, 14)

- **PubMed in `general` category is the most impactful single change.** Adding `categories: [general, science, "scientific publications"]` to pubmed's engine override causes it to fire on every query. Without this, SearXNG's default `general` queries never hit science-category engines.
- **Google Scholar must stay science-only.** At weight=1.5 in `general`, it floods the finance-team query with irrelevant academic PDFs (rows 1–9 all academic papers in iter 5). Moving it back to science-only (iter 8) immediately fixes finance-team without hurting creatine.
- **Arxiv must stay science-only.** At weight=3+general (iter 4), it dominates q3 top-9 with CS/math preprints entirely unrelated to the medical query. Science-only + weight=1 is the correct knob.
- **Semantic Scholar is broken.** CloudFront WAF returns HTTP 202 challenge for automated clients. Removed in iter 5, not re-added.
- **Bing and Qwant provide minimal value.** Bing contributed 1 result; Qwant was consistently suspended (access-denied). Removing them (iter 14) improved result diversity without loss.

### Weights (iters 2, 5, 11, 15, 19)

- **Extreme weight ratios cause flooding.** iter 2 set science engines to weight=3 across the board — the combined search still showed only duckduckgo+google because the science engines were not in general category. After the category fix, weight=3 arxiv+general (iter 4) flooded q3 with irrelevant preprints.
- **pubmed weight=3, timeout=15s is the right knob.** Weight=3 means when pubmed fires and NCBI responds, it surfaces 3 PubMed abstracts in creatine top-5. Timeout=15 is a marginal improvement over 8s (NCBI sometimes slow).
- **NCBI rate limiting is the binding constraint, not weight or timeout.** NCBI caps ~3 req/sec for unregistered IPs. Three fixture queries fired back-to-back trigger 429 on the 2nd and 3rd queries. The pipeline's sequential-per-query pattern avoids this; the harness reveals a worst-case.
- **brave weight=0.5 is correct.** At default weight, brave over-represents SEO surface when active. At 0.5, it still contributes useful coverage (oadoi.org mirrors, clinical articles via brave's index) but doesn't crowd out PubMed results.

### Plugins (iter 6)

- **oa_doi_rewrite works.** Creatine rows 4, 6, 7, 9 became oadoi.org open-access mirrors in iter 6 and stayed there through iter 20. This is the highest-signal plugin — it directly improves full-text access for the downstream pipeline.
- **tracker_url_remover not tested.** Left out of the agent-path config; may be worth adding for pipeline use.

### Hostnames (iters 6, 7, 12, 16, 17)

- **`high_priority` has visible effect for multi-engine results.** getaleph.com (high_priority + returned by both brave and duckduckgo) appears in position 1 for finance-team after the plugin is active. NIH/nature domains reliably score higher.
- **`low_priority` has negligible effect for single-engine results.** myvelofit.com set to low_priority in iter 7 still appeared in top-5 when only one engine returned it. The scoring boost from a single engine outweighs the priority penalty.
- **`remove` does NOT evict single-engine results.** Confirmed experimentally in iter 17: cookedbytaste.com (set to `remove`) still appeared when only brave returned it. The `remove` directive only works when the SAME URL appears from multiple engines — the plugin removes one instance but the other survives. Marketing sites that appear via only one engine cannot be hard-evicted at the SearXNG layer.
- **journals.lww.com and bjsm.bmj.com added to high_priority after iter 16.** A `categories=science` test query surfaced a saphenous nerve / cycling knee article from journals.lww.com/clinorthop — exactly the clinical orthopaedics content the pipeline targets. Added to high_priority for lift when these domains appear.

### Categories (iter 4 discovery, iter 16)

- **The `categories=science` query route (pipeline Lever C) is a more powerful axis than engine weights for scholarly content.** Short keyword queries via `?categories=science` yield 15–19 PubMed results for "medial knee pain cycling" vs 0 for the full question text. The pipeline's expand.py + local model rephrasing unlocks this.
- **This is the key downstream finding from the loop.** SearXNG config tuning is bounded by query formulation. The real lever is the pipeline's ability to generate short, targeted queries that PubMed and Google Scholar can handle.

### Search params (iters 11, 13)

- **safe_search=1 has negligible effect on result quality.** promotional/SEO content appears at all safe_search levels. Left at 1 (not 0) to avoid any adult content in general queries.
- **safe_search=2 is worse.** Filtered too aggressively; reverted in iter 13.

## Downstream orchestration follow-ups

1. **Query rephrasing is the highest-value pipeline lever.** iter 16 demonstrated that `categories=science` + short keyword queries ("medial knee pain cycling") yields 15–19 PubMed results where full-question-text yields 0. The local model (Gemma 26B) should generate 2–4 short keyword expansions per question, sent as `categories=science` queries alongside the main general query. Expected gain: 5–15 additional PubMed/clinical URLs per question.

2. **NCBI rate limiting requires sequential query dispatch.** The harness fires 3 queries in rapid succession and triggers 429 on queries 2–3. The pipeline must dispatch queries sequentially with ≥0.5s gaps, or use NCBI's registered API key path (10 req/sec). If queries are parallelized, use separate HTTP sessions with distinct User-Agent strings or interleave non-PubMed queries between PubMed queries.

3. **oa_doi_rewrite + `remove` limitation means the pipeline needs its own deduplication.** `remove` doesn't evict single-engine supplement marketing sites. The pipeline's runner-side URL canonicalization + source_priors.py penalties are the correct layer to filter cookedbytaste/buckedup/ubiehealth. The config's `remove` section is retained as a first-pass filter for multi-engine duplicates.

4. **brave is the best open-access mirror surfacer.** In iter 20 (brave active), creatine rows 8 and 10 were oadoi.org mirrors of Frontiers and J.Int.Soc.Sports.Nutr. papers. brave's index has higher oadoi.org coverage than duckduckgo or google in this proxy environment. Rate-limiting (429s) is brave's main failure mode under heavy iteration; the pipeline's lower query rate should avoid it.

5. **Finance/business queries have no tension with medical queries in the final config.** google scholar science-only removes all academic PDF pollution from finance-team results. If the pipeline needs academic sources for business questions (e.g., HBR papers), it should route those explicitly via `categories=science` with appropriate keywords rather than relying on the general query path.
