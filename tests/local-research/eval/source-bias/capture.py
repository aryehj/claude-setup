#!/usr/bin/env python3
"""
Capture baseline and lever-variant result sets for source-quality eval.

Run from inside the research-runner container (via bootstrap.sh --capture) or
directly in the container shell:

    python eval/source-bias/capture.py

Outputs: eval/source-bias/baseline-<slug>.json and per-lever variant JSONs,
written to /sessions/source-bias-eval/ (bound to ~/.research/sessions/).

Usage:
    python eval/source-bias/capture.py [--variants VAR,...] [--query-slugs q3,creatine]

    --variants: comma-separated list from {baseline,scholarly-tilt,anti-seo,
                science-categories,scholarly-plus-categories,pages-2,pages-3,
                with-priors,full-combo}
                default: all
    --query-slugs: comma-separated subset of fixture query slugs
                   default: q3,csat,knee-lay,creatine,clin-mod,finance-team
"""
import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from lib import pipeline as _pipeline
from lib import search as _search

# ---------------------------------------------------------------------------
# Fixture queries: 3 kinds × 2 queries. Chosen so the lever sweep covers
# both the ceiling case (academic phrasing → baseline already at the
# science/editorial ceiling) and the stress cases where SEO/marketing
# surface dominates and the levers have something to push against.
#   - academic:    q3, csat               (ceiling reference)
#   - consumer:    knee-lay, creatine     (health + product SEO/affiliate)
#   - operational: clin-mod, finance-team (policy/business; sparse .gov +
#                                          professional sources buried under
#                                          consultant blogs and LinkedIn)
# q3 ↔ knee-lay are the same topic in academic vs lay phrasing, so phrasing
# alone is isolatable as a confound.
# ---------------------------------------------------------------------------

FIXTURE_QUERIES = {
    "q3": (
        "A cyclist develops stubborn medial knee pain that comes on during long rides "
        "and lingers for days afterward. What are the most likely diagnoses, how do "
        "bike-fit and biomechanical factors contribute to each, and what clinical features "
        "would help distinguish between them?"
    ),
    "csat": (
        "Are there validated research methodologies for measuring customer satisfaction, "
        "and what do they measure?"
    ),
    "knee-lay": (
        "what's the best treatment for knee pain from cycling"
    ),
    "creatine": (
        "is creatine safe to take long term"
    ),
    "clin-mod": (
        "is it common for contract mods to move money between CLINs on a firm fixed "
        "price contract"
    ),
    "finance-team": (
        "is it unusual for a 60-person software consulting company to have a 4-person "
        "finance team"
    ),
}

# ---------------------------------------------------------------------------
# Lever variant configurations.
# ---------------------------------------------------------------------------

VARIANTS = {
    "baseline": {
        "env": {"EXPAND_PROMPT_NAME": "generic", "SCHOLARLY_MODE": ""},
        "search_kwargs": {},
    },
    "scholarly-tilt": {
        "env": {"EXPAND_PROMPT_NAME": "scholarly-tilt", "SCHOLARLY_MODE": ""},
        "search_kwargs": {},
    },
    "anti-seo": {
        "env": {"EXPAND_PROMPT_NAME": "anti-seo", "SCHOLARLY_MODE": ""},
        "search_kwargs": {},
    },
    "science-categories": {
        "env": {"EXPAND_PROMPT_NAME": "generic", "SCHOLARLY_MODE": "1"},
        "search_kwargs": {},
    },
    "scholarly-plus-categories": {
        "env": {"EXPAND_PROMPT_NAME": "scholarly-tilt", "SCHOLARLY_MODE": "1"},
        "search_kwargs": {},
    },
    "pages-2": {
        "env": {"EXPAND_PROMPT_NAME": "generic", "SCHOLARLY_MODE": ""},
        "search_kwargs": {"pages": 2},
    },
    "pages-3": {
        "env": {"EXPAND_PROMPT_NAME": "generic", "SCHOLARLY_MODE": ""},
        "search_kwargs": {"pages": 3},
    },
    "with-priors": {
        "env": {"EXPAND_PROMPT_NAME": "generic", "SCHOLARLY_MODE": ""},
        "search_kwargs": {},
        "note": "priors are always applied (Phase 5 default); this variant is identical to baseline but serves as the explicit 'priors-on' label",
    },
    "full-combo": {
        "env": {"EXPAND_PROMPT_NAME": "scholarly-tilt", "SCHOLARLY_MODE": "1"},
        "search_kwargs": {"pages": 2},
        "note": "scholarly-tilt + science-categories + pages-2 + priors — expected winning combination",
    },
}

LABEL_CHOICES = "science, editorial-considered, seo-fluff, listicle, marketing, other"


def run_variant(query_slug: str, query: str, variant_name: str, variant: dict, out_dir: pathlib.Path) -> None:
    out_path = out_dir / f"{variant_name}-{query_slug}.json"
    if out_path.exists():
        print(f"  skip (exists): {out_path.name}")
        return

    print(f"  running {variant_name} / {query_slug} ...", flush=True)
    t0 = time.monotonic()

    env = variant.get("env", {})
    old_env = {k: os.environ.get(k, "") for k in env}
    os.environ.update(env)

    try:
        import importlib
        import lib.expand
        import lib.pipeline
        importlib.reload(lib.expand)
        importlib.reload(lib.pipeline)

        result = lib.pipeline.gather_sources(query, n_expansions=4, n_per_query=20, top_k=15)
    finally:
        os.environ.update(old_env)
        importlib.reload(lib.expand)
        importlib.reload(lib.pipeline)

    elapsed = round(time.monotonic() - t0, 1)

    record = {
        "query_slug": query_slug,
        "query": query,
        "variant": variant_name,
        "variant_config": variant,
        "elapsed_s": elapsed,
        "expansions": result["expansions"],
        "raw_count": len(result["raw_results"]),
        "ranked": [
            {
                "rank": i + 1,
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "engine": r.get("engine", ""),
                "rerank_score": round(r.get("rerank_score", 0.0), 4),
                "prior_adj": round(r.get("prior_adj", 0.0), 4),
                "label": "",   # fill in by hand: science, editorial-considered, seo-fluff, listicle, marketing, other
            }
            for i, r in enumerate(result["ranked"])
        ],
        "label_choices": LABEL_CHOICES,
        "timings": result["timings"],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"    -> {out_path} ({elapsed}s, {len(result['ranked'])} ranked)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture source-bias eval data")
    parser.add_argument(
        "--variants",
        default=",".join(VARIANTS.keys()),
        help="comma-separated variant names (default: all)",
    )
    parser.add_argument(
        "--query-slugs",
        default=",".join(FIXTURE_QUERIES.keys()),
        help="comma-separated fixture query slugs (default: q3,csat,knee-lay,creatine,clin-mod,finance-team)",
    )
    parser.add_argument(
        "--out-dir",
        default="/sessions/source-bias-eval",
        help="output directory for JSON files (default: /sessions/source-bias-eval)",
    )
    args = parser.parse_args()

    selected_variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    selected_queries = [q.strip() for q in args.query_slugs.split(",") if q.strip()]
    out_dir = pathlib.Path(args.out_dir)

    unknown_variants = [v for v in selected_variants if v not in VARIANTS]
    if unknown_variants:
        print(f"Unknown variants: {unknown_variants}. Available: {list(VARIANTS.keys())}", file=sys.stderr)
        sys.exit(1)

    unknown_queries = [q for q in selected_queries if q not in FIXTURE_QUERIES]
    if unknown_queries:
        print(f"Unknown query slugs: {unknown_queries}. Available: {list(FIXTURE_QUERIES.keys())}", file=sys.stderr)
        sys.exit(1)

    print(f"Capturing {len(selected_variants)} variants × {len(selected_queries)} queries")
    print(f"Output: {out_dir}\n")

    for query_slug in selected_queries:
        query = FIXTURE_QUERIES[query_slug]
        for variant_name in selected_variants:
            run_variant(query_slug, query, variant_name, VARIANTS[variant_name], out_dir)

    print(f"\nDone. Hand-label the 'label' field in each JSON file using: {LABEL_CHOICES}")
    print("Then run compare.py to generate MANIFEST.md.")


if __name__ == "__main__":
    main()
