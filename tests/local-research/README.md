# local-research harness

Iterative multi-round research pipeline in a Docker container, using the existing
`research` Colima VM (Squid proxy + `RESEARCH` iptables chain) and `research-searxng`.

## Prerequisites

The `research` environment must be running before you use this harness:

```bash
./research.py --backend=omlx
```

## Quick start

```bash
export OMLX_API_KEY=<your-key>
./tests/local-research/bootstrap.sh "my research question"
```

Run the smoke check to verify connectivity before a real query:

```bash
./tests/local-research/bootstrap.sh --smoke
```

## Env-var overrides

| Variable | Default | Description |
|---|---|---|
| `OMLX_BASE_URL` | `http://host.docker.internal:8000/v1` | omlx API base URL |
| `OMLX_API_KEY` | *(required)* | omlx API key |
| `EMBED_MODEL` | `nomic-embed-text-v1.5` | Embedding model for reranking |
| `EXPAND_MODEL` | `gemma-4-E4B-it-MLX-8bit` | Query expansion model |
| `NOTES_MODEL` | `gemma-4-26b-a4b-it-8bit` | Per-source notes and digests model |
| `SYNTH_MODEL` | `gemma-4-26b-a4b-it-8bit` | Final synthesis model |

Confirm the exact model IDs on your omlx instance:

```bash
curl -s -H "Authorization: Bearer $OMLX_API_KEY" \
    http://localhost:8000/v1/models | python3 -m json.tool
```

Then export the corrected IDs before running bootstrap.sh.

## Session output layout

Each run produces a directory under `tests/local-research/sessions/<timestamp-slug>/`:

```
query.md                    — the original research question
rounds/
  <n>/
    sources/
      <idx>-<slug>.md       — fetched + extracted + noted source
    digest.md               — per-round 800–1200 word digest with [r.N] citations
synthesis.md                — hierarchical final synthesis with [N] source anchors
manifest.md                 — rounds, models, timings, source counts, termination reason
handoff.md                  — flat concatenation for frontier paste (query + digests + top-K notes + synthesis)
```

## CLI flags (Phase 6+)

```
./bootstrap.sh "query"            interactive mode (2N+1 gates across N rounds)
./bootstrap.sh --smoke            smoke check only
./bootstrap.sh --batch "query"    autonomous mode (no gates; batch termination heuristic)
./bootstrap.sh --no-synth "query" stop after final round, skip synthesis
```

## Eval

- `eval/run_q1q6.py` — batch sweep over q1–q6 from `experiments/vane-eval/queries.md` (Phase 7)
- `eval/run_synth_sweep.py` — synthesis-quality input-variable sweep (Phase 8)
- `eval/references/q<n>.md` — gold answers and must-hit facts per query (Phase 8)
