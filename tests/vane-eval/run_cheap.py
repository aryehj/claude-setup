"""Cheap-phase OFAT sweep against omlx.

Usage:
    uv run python tests/vane-eval/run_cheap.py [options]

Options:
    --base-url URL     omlx base URL (default: http://host.docker.internal:8000/v1)
    --models a,b,c    comma-separated model shortlist (default: all discovered)
    --queries q1,q3   comma-separated query subset (default: all six)
    --out PATH         output directory (default: tests/vane-eval/results/cheap-<UTC-ts>)
    --force            bypass the 90-call count guard
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lib.cells import Cell, call_omlx, discover_omlx_models, write_cell_output
from lib.queries import load as load_queries

_DEFAULT_BASE_URL = "http://0.0.0.0:8000/v1"
_PROMPT_STYLES = ["bare", "structured", "research_system"]
_TEMPERATURES = [0.0, 0.3, 0.7]
_THINKING_VALUES = [False, True]
_MAX_CELLS_DEFAULT = 90


# ── public helpers (imported by tests) ────────────────────────────────────────


def validate_shortlist(shortlist: list[str], discovered: list[str]) -> None:
    """Raise ValueError if any entry in shortlist is absent from discovered."""
    discovered_set = set(discovered)
    missing = [m for m in shortlist if m not in discovered_set]
    if missing:
        raise ValueError(
            f"Model(s) not found in /v1/models: {', '.join(missing)}"
        )


def check_cell_count(cells: int, queries: int, force: bool) -> None:
    """Raise SystemExit if cells × queries > 90 and force is False."""
    total = cells * queries
    if total > _MAX_CELLS_DEFAULT and not force:
        sys.exit(
            f"Cell count {cells} × {queries} queries = {total} calls exceeds "
            f"the {_MAX_CELLS_DEFAULT}-call guard. Pass --force to override."
        )


def build_ofat_cells(
    models: list[str],
    default_model: str,
    default_prompt_style: str = "structured",
    default_temperature: float = 0.3,
    default_thinking: bool = False,
) -> list[Cell]:
    """Return the deduplicated OFAT cell list (query_id left blank; caller fills in).

    Axes swept one-at-a-time, others held at their defaults:
      - model axis:       every model in `models`
      - prompt axis:      bare / structured / research_system
      - temperature axis: 0.0 / 0.3 / 0.7
      - thinking axis:    False / True
    """
    seen: set[tuple] = set()
    cells: list[Cell] = []

    def _add(model: str, prompt_style: str, temperature: float, thinking: bool) -> None:
        key = (model, prompt_style, temperature, thinking)
        if key in seen:
            return
        seen.add(key)
        cells.append(Cell(
            query_id="",
            model=model,
            prompt_style=prompt_style,
            temperature=temperature,
            thinking=thinking,
            label=(
                f"{model} · {prompt_style} · t={temperature} · "
                f"think={'on' if thinking else 'off'}"
            ),
        ))

    # Default cell first (so it is always index 0)
    _add(default_model, default_prompt_style, default_temperature, default_thinking)

    # Model axis
    for m in models:
        _add(m, default_prompt_style, default_temperature, default_thinking)

    # Prompt axis
    for ps in _PROMPT_STYLES:
        _add(default_model, ps, default_temperature, default_thinking)

    # Temperature axis
    for t in _TEMPERATURES:
        _add(default_model, default_prompt_style, t, default_thinking)

    # Thinking axis
    for tk in _THINKING_VALUES:
        _add(default_model, default_prompt_style, default_temperature, tk)

    return cells


# ── internal helpers ───────────────────────────────────────────────────────────


def _print_plan(cells: list[Cell], queries: list, models: list[str]) -> None:
    total = len(cells) * len(queries)
    print(f"\nOFAT sweep plan: {len(cells)} cells × {len(queries)} queries = {total} calls")
    print(f"  model axis    : {models}")
    print(f"  prompt axis   : {_PROMPT_STYLES}")
    print(f"  temperature   : {_TEMPERATURES}")
    print(f"  thinking axis : {_THINKING_VALUES}")
    print()


def _write_manifest(
    run_dir: Path,
    cells: list[Cell],
    query_ids: list[str],
    cell_results: list[dict],
    models: list[str],
    base_url: str,
    wall_s: float,
) -> Path:
    """Write MANIFEST.md in run_dir. Returns the path."""
    lines = [
        "# MANIFEST",
        "",
        "## Run configuration",
        "",
        f"- **omlx base URL:** `{base_url}`",
        f"- **models:** {models}",
        f"- **default:** model=`{cells[0].model if cells else 'n/a'}`, "
        f"prompt=`structured`, temperature=`0.3`, thinking=`false`",
        f"- **total wall-clock:** {wall_s:.1f}s",
        "",
        "## Cells",
        "",
        "| file | label | status |",
        "|------|-------|--------|",
    ]

    for row in cell_results:
        lines.append(f"| `{row['file']}` | {row['label']} | {row['status']} |")

    manifest_path = run_dir / "MANIFEST.md"
    manifest_path.write_text("\n".join(lines) + "\n")
    return manifest_path


# ── main ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cheap-phase OFAT sweep against omlx")
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    parser.add_argument("--models", default="", help="comma-separated model shortlist")
    parser.add_argument("--queries", default="", help="comma-separated query subset (e.g. q1,q3)")
    parser.add_argument(
        "--out",
        default="",
        help="output directory (default: tests/vane-eval/results/cheap-<UTC-ts>)",
    )
    parser.add_argument("--force", action="store_true", help="bypass the 90-call guard")
    args = parser.parse_args(argv)

    if not args.base_url.startswith(("http://", "https://")):
        sys.exit(
            f"--base-url must start with http:// or https://; got: {args.base_url!r}\n"
            "Hint: set OMLX_HOST (e.g. export OMLX_HOST=http://192.168.5.2:8000) "
            "then run: uv run python tests/vane-eval/run_cheap.py --base-url \"$OMLX_HOST/v1\""
        )

    # Discover models
    print(f"Discovering models at {args.base_url} …")
    try:
        discovered = discover_omlx_models(args.base_url)
    except RuntimeError as exc:
        sys.exit(str(exc))

    if not discovered:
        sys.exit("No models returned by /v1/models. Is omlx running?")

    # Resolve active model list
    if args.models:
        shortlist = [m.strip() for m in args.models.split(",") if m.strip()]
        try:
            validate_shortlist(shortlist, discovered)
        except ValueError as exc:
            sys.exit(str(exc))
        active_models = shortlist
    else:
        active_models = discovered

    print(f"Active models ({len(active_models)}): {active_models}")

    default_model = active_models[0]

    # Build OFAT cell list
    cells = build_ofat_cells(
        models=active_models,
        default_model=default_model,
    )

    # Load queries
    queries_path = _HERE / "queries.md"
    all_queries = load_queries(queries_path)
    if args.queries:
        requested = {q.strip() for q in args.queries.split(",") if q.strip()}
        active_queries = [q for q in all_queries if q.id in requested]
        missing_qs = requested - {q.id for q in active_queries}
        if missing_qs:
            sys.exit(f"Unknown query ID(s): {', '.join(sorted(missing_qs))}")
    else:
        active_queries = all_queries

    if not active_queries:
        sys.exit("No queries selected.")

    # Guard
    check_cell_count(len(cells), len(active_queries), args.force)

    _print_plan(cells, active_queries, active_models)

    # Output directory
    if args.out:
        run_dir = Path(args.out)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = _HERE / "results" / f"cheap-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output → {run_dir}\n")

    # Sweep
    t_start = time.monotonic()
    cell_results: list[dict] = []
    total = len(cells) * len(active_queries)
    done = 0

    for cell_template in cells:
        for query in active_queries:
            done += 1
            cell = copy.copy(cell_template)
            cell.query_id = query.id

            print(
                f"[{done}/{total}] {query.id}  {cell.label}",
                end=" … ",
                flush=True,
            )

            try:
                result = call_omlx(
                    base_url=args.base_url,
                    cell=cell,
                    query=query.query,
                )
                out_path = write_cell_output(
                    run_dir=run_dir,
                    cell=cell,
                    query_id=query.id,
                    query_text=query.query,
                    reference_text=query.reference,
                    result=result,
                )
                status = result.get("error") and "error" or (
                    "skip:no-thinking-support"
                    if cell.thinking and result.get("reasoning") is None
                    else "ok"
                )
            except Exception as exc:  # noqa: BLE001
                # write a minimal error cell if write_cell_output itself fails
                status = "error"
                safe = (
                    cell.label.replace(" ", "_")
                    .replace("·", "-")
                    .replace("=", "")
                    .replace("/", "-")
                    .replace(":", "")
                )
                safe = "".join(c if c.isalnum() or c in "-_." else "" for c in safe)
                out_path = run_dir / f"{query.id}_{safe}.md"
                out_path.write_text(
                    f"---\nstatus: error\n---\n\n## Error\n\n```\n{exc}\n```\n"
                )

            rel_path = out_path.relative_to(run_dir)
            cell_results.append(
                {"file": str(rel_path), "label": cell.label, "status": status}
            )
            print(status)

    wall_s = time.monotonic() - t_start
    print(f"\nSweep complete in {wall_s:.1f}s")

    manifest = _write_manifest(
        run_dir=run_dir,
        cells=cells,
        query_ids=[q.id for q in active_queries],
        cell_results=cell_results,
        models=active_models,
        base_url=args.base_url,
        wall_s=wall_s,
    )
    print(f"Manifest → {manifest}")

    error_count = sum(1 for r in cell_results if r["status"] == "error")
    if error_count:
        print(f"\n{error_count}/{total} cells errored.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
