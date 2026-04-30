#!/usr/bin/env python3
"""
Read hand-labeled JSON files from capture.py and write MANIFEST.md.

Usage:
    python eval/source-bias/compare.py [--data-dir /sessions/source-bias-eval]

Reads all *-<query_slug>.json files in the data dir that have at least one
non-empty label field, computes label distributions per (variant × query),
and writes MANIFEST.md to the same directory.
"""
import argparse
import json
import pathlib
import sys
from collections import defaultdict

POSITIVE_LABELS = {"science", "editorial-considered"}
NEGATIVE_LABELS = {"seo-fluff", "listicle", "marketing"}

BASELINE_VARIANT = "baseline"


def load_cells(data_dir: pathlib.Path) -> list[dict]:
    cells = []
    for path in sorted(data_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            print(f"Skip {path.name}: {e}", file=sys.stderr)
            continue
        ranked = data.get("ranked", [])
        labeled = [r for r in ranked if r.get("label", "").strip()]
        if not labeled:
            continue
        labels = [r["label"].strip() for r in labeled]
        n = len(labels)
        pos = sum(1 for l in labels if l in POSITIVE_LABELS)
        neg = sum(1 for l in labels if l in NEGATIVE_LABELS)
        cells.append({
            "file": path.name,
            "query_slug": data.get("query_slug", ""),
            "variant": data.get("variant", ""),
            "n": n,
            "pos": pos,
            "neg": neg,
            "pos_pct": round(100 * pos / n) if n else 0,
            "neg_pct": round(100 * neg / n) if n else 0,
            "ranked": ranked,
            "expansions": data.get("expansions", []),
            "elapsed_s": data.get("elapsed_s", 0),
        })
    return cells


def compute_per_lever_marginals(cells: list[dict]) -> dict:
    """Average pos_pct and neg_pct per variant across all queries."""
    totals: dict[str, dict] = defaultdict(lambda: {"pos_sum": 0, "neg_sum": 0, "count": 0})
    for c in cells:
        v = c["variant"]
        totals[v]["pos_sum"] += c["pos_pct"]
        totals[v]["neg_sum"] += c["neg_pct"]
        totals[v]["count"] += 1
    return {
        v: {
            "mean_pos_pct": round(d["pos_sum"] / d["count"]) if d["count"] else 0,
            "mean_neg_pct": round(d["neg_sum"] / d["count"]) if d["count"] else 0,
            "n_queries": d["count"],
        }
        for v, d in totals.items()
    }


def delta_vs_baseline(cells: list[dict]) -> dict:
    """
    Return {variant: {query_slug: {pos_delta, neg_delta}}} vs baseline.
    """
    baseline_map = {c["query_slug"]: c for c in cells if c["variant"] == BASELINE_VARIANT}
    deltas: dict = defaultdict(dict)
    for c in cells:
        if c["variant"] == BASELINE_VARIANT:
            continue
        bsl = baseline_map.get(c["query_slug"])
        if not bsl:
            continue
        deltas[c["variant"]][c["query_slug"]] = {
            "pos_delta": c["pos_pct"] - bsl["pos_pct"],
            "neg_delta": c["neg_pct"] - bsl["neg_pct"],
        }
    return deltas


def write_manifest(cells: list[dict], out_path: pathlib.Path) -> None:
    marginals = compute_per_lever_marginals(cells)
    deltas = delta_vs_baseline(cells)

    lines = ["# Source-bias eval — MANIFEST\n"]

    # Per-axis marginal means
    lines.append("## Per-lever marginal means (avg across queries)\n")
    lines.append("| variant | mean pos% | mean neg% | Δpos vs baseline | Δneg vs baseline | queries |")
    lines.append("|---------|-----------|-----------|-----------------|------------------|---------|")
    bsl_pos = marginals.get(BASELINE_VARIANT, {}).get("mean_pos_pct", 0)
    bsl_neg = marginals.get(BASELINE_VARIANT, {}).get("mean_neg_pct", 0)
    for variant, m in sorted(marginals.items()):
        dp = m["mean_pos_pct"] - bsl_pos
        dn = m["mean_neg_pct"] - bsl_neg
        lines.append(
            f"| {variant} | {m['mean_pos_pct']}% | {m['mean_neg_pct']}% | "
            f"{'+' if dp >= 0 else ''}{dp}pp | {'+' if dn >= 0 else ''}{dn}pp | {m['n_queries']} |"
        )
    lines.append("")

    # Per-cell detail
    lines.append("## Per-cell detail\n")
    lines.append("Rubric: `science + editorial-considered` = positive; `seo-fluff + listicle + marketing` = negative.\n")
    lines.append("| query | variant | n labeled | pos% | neg% | Δpos | Δneg | note |")
    lines.append("|-------|---------|-----------|------|------|------|------|------|")

    for c in sorted(cells, key=lambda x: (x["query_slug"], x["variant"])):
        d_entry = deltas.get(c["variant"], {}).get(c["query_slug"])
        if d_entry:
            dp = f"{'+' if d_entry['pos_delta'] >= 0 else ''}{d_entry['pos_delta']}pp"
            dn = f"{'+' if d_entry['neg_delta'] >= 0 else ''}{d_entry['neg_delta']}pp"
        else:
            dp = dn = "—"
        lines.append(
            f"| {c['query_slug']} | {c['variant']} | {c['n']} | {c['pos_pct']}% | {c['neg_pct']}% "
            f"| {dp} | {dn} | |"
        )
    lines.append("")

    # Acceptance-criteria check
    lines.append("## Acceptance-criteria check\n")
    met = False
    for variant, query_map in deltas.items():
        for query_slug, d in query_map.items():
            if d["pos_delta"] >= 30 and d["neg_delta"] <= -30:
                lines.append(
                    f"**PASS**: {variant} / {query_slug} raised pos% by {d['pos_delta']}pp "
                    f"and lowered neg% by {abs(d['neg_delta'])}pp (target: ≥30pp each)."
                )
                met = True
    if not met:
        lines.append(
            "_No cell yet meets the ≥30pp pos increase AND ≥30pp neg decrease criterion. "
            "Run more variants or add labeling data._"
        )
    lines.append("")

    # Default recommendation placeholder
    lines.append("## Default recommendation\n")
    lines.append(
        "_After reviewing the table, update these fields in-place (no compat shim):_\n\n"
        "- `lib/expand.py` — set `_ACTIVE_PROMPT` default to the winning prompt name\n"
        "- `lib/config.py` — set `SCHOLARLY_MODE` default to `True`/`False` as appropriate\n"
        "- `lib/search.py` — update default `pages` value\n"
        "- `lib/source_priors.py` — adjust `_BOOST_ADJ`/`_PENALIZE_ADJ` if calibration data suggests\n"
    )

    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MANIFEST.md from labeled capture JSONs")
    parser.add_argument(
        "--data-dir",
        default="/sessions/source-bias-eval",
        help="directory containing labeled JSON files (default: /sessions/source-bias-eval)",
    )
    args = parser.parse_args()

    data_dir = pathlib.Path(args.data_dir)
    cells = load_cells(data_dir)

    if not cells:
        print("No labeled JSON files found. Run capture.py first, then fill in label fields.")
        sys.exit(0)

    print(f"Loaded {len(cells)} labeled cells")
    out_path = data_dir / "MANIFEST.md"
    write_manifest(cells, out_path)


if __name__ == "__main__":
    main()
