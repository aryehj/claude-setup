"""Pick a winner + ablations from a graded cheap-phase run.

Usage:
    uv run python tests/vane-eval/select_winners.py \\
        --from tests/vane-eval/results/cheap-<run>

`SCORES.md` rows are per-(query × cell). Cells that win a single easy
query (e.g. q4 in the thinking sweep, where many cells got 15/15) would
otherwise dominate the top-row pick even when their other-query scores
are mediocre. We aggregate per cell — `(model, prompt_style, temperature,
thinking)` — sum totals across queries, and pick the cell with the best
sweep-wide score. Tie-breaks prefer `thinking=False` (saves Vane-phase
latency) and lower temperature (more deterministic replays).

If `SCORES.md` is absent, a hand-edit template is written so the user can
fill in `winners.json` manually.

Output: `tests/vane-eval/results/cheap-<run>/winners.json`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


_SCORES_HEADER_RE = re.compile(r"\|\s*file\s*\|", re.IGNORECASE)


def parse_scores_md(text: str) -> list[dict[str, Any]]:
    """Parse the Markdown table inside SCORES.md.

    Expected columns (case-insensitive header):
        file, label, model, prompt_style, temperature, thinking, total

    Extra columns are ignored. Rows are returned sorted by total (desc).
    Raises ValueError if no recognisable table is found.
    """
    lines = [line.rstrip() for line in text.splitlines()]

    header_idx = None
    for i, line in enumerate(lines):
        if _SCORES_HEADER_RE.search(line):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("SCORES.md: no table header containing 'file' column")

    header_cells = [c.strip().lower() for c in lines[header_idx].strip("|").split("|")]
    rows: list[dict[str, Any]] = []
    for line in lines[header_idx + 2:]:  # skip header + separator
        if not line.strip().startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < len(header_cells):
            continue
        row = dict(zip(header_cells, cells))
        try:
            row["total"] = float(row.get("total", "0"))
        except ValueError:
            continue
        try:
            row["temperature"] = float(row.get("temperature", "0"))
        except ValueError:
            row["temperature"] = 0.0
        thinking_raw = (row.get("thinking") or "").strip().lower()
        row["thinking"] = thinking_raw in ("true", "yes", "on", "1")
        rows.append(row)

    rows.sort(key=lambda r: r["total"], reverse=True)
    # Convert total back to int when it's whole
    for r in rows:
        if r["total"] == int(r["total"]):
            r["total"] = int(r["total"])
    return rows


def _row_to_cell(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": row.get("label", ""),
        "model": row.get("model", ""),
        "prompt_style": row.get("prompt_style", ""),
        "temperature": float(row.get("temperature", 0.0)),
        "thinking": bool(row.get("thinking", False)),
    }


def aggregate_cells(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate per-query rows into per-cell totals.

    Groups by `(model, prompt_style, temperature, thinking)` and sums
    `total` across queries. Returns one dict per cell with an extra `n`
    count and the summed `total`, sorted by aggregate total descending.

    Tie-break order (deterministic): `thinking=False` before True (Vane
    replays are slower with thinking on, and the thinking sweep's wash-up
    found no measurable benefit on these queries), then lower temperature,
    then model and prompt_style alphabetical. The cell-level `label`
    column in SCORES.md is already query-independent, so we keep the first
    row's label for each group.
    """
    by_cell: dict[tuple[str, str, float, bool], dict[str, Any]] = {}
    for r in rows:
        key = (
            r.get("model", ""),
            r.get("prompt_style", ""),
            float(r.get("temperature", 0.0)),
            bool(r.get("thinking", False)),
        )
        cell = by_cell.get(key)
        if cell is None:
            cell = {
                "label": r.get("label", ""),
                "model": key[0],
                "prompt_style": key[1],
                "temperature": key[2],
                "thinking": key[3],
                "total": 0.0,
                "n": 0,
            }
            by_cell[key] = cell
        cell["total"] += float(r.get("total", 0))
        cell["n"] += 1

    cells = list(by_cell.values())
    cells.sort(key=lambda c: (
        -c["total"],
        c["thinking"],
        c["temperature"],
        c["model"],
        c["prompt_style"],
    ))
    for c in cells:
        if c["total"] == int(c["total"]):
            c["total"] = int(c["total"])
    return cells


def build_winners_json(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Top cell → winner; next ≤2 cells → ablations.

    Aggregates per-query rows into per-cell totals (`aggregate_cells`)
    before ranking, so the winner is the cell with the best sweep-wide
    score — not whichever single per-question row scored highest.
    """
    if not rows:
        return winners_template()
    cells = aggregate_cells(rows)
    return {
        "winner": _row_to_cell(cells[0]),
        "ablations": [_row_to_cell(c) for c in cells[1:3]],
    }


def winners_template() -> dict[str, Any]:
    return {
        "winner": {
            "label": "<fill in>",
            "model": "<model-key>",
            "prompt_style": "structured",
            "temperature": 0.3,
            "thinking": False,
        },
        "ablations": [],
    }


def _print_manifest(run_dir: Path) -> None:
    manifest = run_dir / "MANIFEST.md"
    if manifest.exists():
        print(f"\n— {manifest} —\n")
        print(manifest.read_text())
    else:
        print(f"(no MANIFEST.md in {run_dir})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pick winner + ablations from a cheap-phase run")
    parser.add_argument("--from", dest="from_dir", required=True, help="cheap-phase run dir")
    args = parser.parse_args(argv)

    run_dir = Path(args.from_dir)
    if not run_dir.is_dir():
        sys.exit(f"Not a directory: {run_dir}")

    out_path = run_dir / "winners.json"
    scores_path = run_dir / "SCORES.md"

    if scores_path.exists():
        rows = parse_scores_md(scores_path.read_text())
        if not rows:
            print(f"SCORES.md found but empty; writing template to {out_path}")
            data = winners_template()
        else:
            cells = aggregate_cells(rows)
            counts = {c["n"] for c in cells}
            if len(counts) > 1:
                print(
                    f"WARN: cells have inconsistent query counts {sorted(counts)} — "
                    f"some cells were graded for fewer queries than others. "
                    f"Aggregate totals are not directly comparable."
                )
            data = build_winners_json(rows)
            top = cells[0]
            print(
                f"Pre-filled winners.json from SCORES.md "
                f"({len(rows)} rows → {len(cells)} cells, "
                f"winner aggregate {top['total']} over n={top['n']} queries)."
            )
    else:
        print("SCORES.md not found; writing hand-edit template.")
        _print_manifest(run_dir)
        data = winners_template()

    out_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"\nWrote {out_path}")
    print("Edit it before running run_vane.py if needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
