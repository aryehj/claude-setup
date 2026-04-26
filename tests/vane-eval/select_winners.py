"""Pick a winner + ablations from a graded cheap-phase run.

Usage:
    uv run python tests/vane-eval/select_winners.py \\
        --from tests/vane-eval/results/cheap-<run>

If `SCORES.md` is present in the run dir (produced by Phase 4 grading), the
top-scoring row becomes the winner and the next two become ablations. Otherwise
a hand-edit template is written so the user can fill in `winners.json`
manually.

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


def build_winners_json(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Top row → winner; next ≤2 → ablations."""
    if not rows:
        return winners_template()
    return {
        "winner": _row_to_cell(rows[0]),
        "ablations": [_row_to_cell(r) for r in rows[1:3]],
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
            data = build_winners_json(rows)
            print(f"Pre-filled winners.json from SCORES.md (top of {len(rows)} rows).")
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
