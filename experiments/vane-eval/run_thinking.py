# DEPRECATED / UNMAINTAINED. Archived from tests/vane-eval/ on the
# test-vane-models branch wrap-up. Imports its `lib/` siblings, which
# were deleted in the same commit; the script will not run as-is.
# Kept for reference only — see git history before this commit for
# the runnable form.

"""Interactive thinking-axis sweep against omlx.

omlx exposes no per-request thinking toggle; thinking is a server-side
per-loaded-model setting. Toggling it requires the human to update each
model's omlx configuration. This script asks the human exactly twice — once
before the thinking=OFF phase (set every listed model to thinking off) and
once before the thinking=ON phase — and then sweeps model × prompt ×
temperature × query within each phase. omlx will swap models in and out of
RAM automatically as requests come in; the human's time is what we minimize.

Usage:
    uv run python tests/vane-eval/run_thinking.py [options]

Options:
    --base-url URL         omlx base URL (default: http://0.0.0.0:8000/v1)
    --models a,b,c         comma-separated model shortlist (default: all discovered)
    --prompt-styles s1,s2  subset of bare,structured,research_system (default: all)
    --temperatures 0,0.3   comma-separated temperatures (default: 0.2,0.6,1.0)
    --queries q1,q3        comma-separated query subset (default: all six)
    --out PATH             output directory (default: results/thinking-<UTC-ts>)
    --skip-off             skip every thinking=off phase
    --skip-on              skip every thinking=on phase
    --force                bypass the cell-count guard
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lib.cells import Cell, call_omlx, classify_status, discover_omlx_models, write_cell_output
from lib.queries import load as load_queries

_DEFAULT_BASE_URL = "http://0.0.0.0:8000/v1"
_PROMPT_STYLES = ["bare", "structured", "research_system"]
_TEMPERATURES = [0.2, 0.6, 1.0]
_MAX_CELLS_DEFAULT = 400
_INTER_MODEL_PAUSE_S = 5


def _prompt_user(thinking: bool, models: list[str]) -> bool:
    """Block until the human confirms omlx is configured for the whole phase.

    Returns False to skip the entire phase.
    """
    state = "ON" if thinking else "OFF"
    print()
    print(f"━━ thinking={state} phase ━━")
    print(f"  Configure omlx so thinking is {state} for ALL of these models:")
    for m in models:
        print(f"    - {m}")
    print(f"  Then press Enter to continue, or type 'skip' to skip this phase.")
    answer = input("> ").strip().lower()
    return answer != "skip"


def _run_phase(
    base_url: str,
    model: str,
    thinking: bool,
    prompt_styles: list[str],
    temperatures: list[float],
    queries: list,
    run_dir: Path,
    counter_start: int,
    counter_total: int,
) -> tuple[list[dict], int]:
    """Run a full prompt × temperature × query grid for one (model, thinking) phase.

    Inner loop order is (prompt, temperature, query) so that consecutive cells
    share the system-prompt prefix and benefit from omlx's KV cache.
    """
    rows: list[dict] = []
    done = counter_start
    first_cell_done = False
    for prompt_style in prompt_styles:
        for temperature in temperatures:
            for query in queries:
                done += 1
                cell = Cell(
                    query_id=query.id,
                    model=model,
                    prompt_style=prompt_style,
                    temperature=temperature,
                    thinking=thinking,
                    label=(
                        f"{model} · {prompt_style} · t={temperature} · "
                        f"think={'on' if thinking else 'off'}"
                    ),
                )
                print(
                    f"  [{done}/{counter_total}] {query.id}  "
                    f"{prompt_style} t={temperature}",
                    end=" … ",
                    flush=True,
                )
                result = call_omlx(base_url=base_url, cell=cell, query=query.query)
                if not first_cell_done and not result["error"]:
                    first_cell_done = True
                    if thinking and result["reasoning"] is None:
                        raise RuntimeError(
                            f"omlx misconfiguration: thinking=ON phase for {model!r} "
                            f"but first cell returned no reasoning_content. "
                            f"Reload the model with thinking enabled and retry."
                        )
                out_path = write_cell_output(
                    run_dir=run_dir,
                    cell=cell,
                    query_id=query.id,
                    query_text=query.query,
                    reference_text=query.reference,
                    result=result,
                )
                status = classify_status(cell, result)
                rows.append({
                    "file": str(out_path.relative_to(run_dir)),
                    "label": cell.label,
                    "status": status,
                })
                print(status)
    return rows, done


_STATUS_ORDER = [
    "error",
    "error:no-content",
    "warn:truncated",
    "warn:reasoning-leaked",
    "ok",
]


def _write_manifest(
    run_dir: Path,
    base_url: str,
    models: list[str],
    prompt_styles: list[str],
    temperatures: list[float],
    query_ids: list[str],
    rows: list[dict],
    wall_s: float,
) -> Path:
    from collections import Counter
    counts = Counter(r["status"] for r in rows)

    lines = [
        "# MANIFEST (run_thinking)",
        "",
        "## Run configuration",
        "",
        f"- **omlx base URL:** `{base_url}`",
        f"- **models:** {models}",
        f"- **prompt styles:** {prompt_styles}",
        f"- **temperatures:** {temperatures}",
        f"- **queries:** {query_ids}",
        f"- **total wall-clock:** {wall_s:.1f}s",
        "",
        "Reasoning-leak status (`warn:reasoning-leaked`) means the human said "
        "thinking was OFF but `reasoning_content` came back populated — re-check "
        "the omlx model load.",
        "",
        "## Status summary",
        "",
    ]
    for status in _STATUS_ORDER:
        if counts.get(status, 0):
            lines.append(f"- {status}: {counts[status]}")
    for status in sorted(counts):
        if status not in _STATUS_ORDER and counts[status]:
            lines.append(f"- {status}: {counts[status]}")
    lines += [
        "",
        "## Cells",
        "",
        "| file | label | status |",
        "|------|-------|--------|",
    ]
    for row in rows:
        lines.append(f"| `{row['file']}` | {row['label']} | {row['status']} |")
    manifest_path = run_dir / "MANIFEST.md"
    manifest_path.write_text("\n".join(lines) + "\n")
    return manifest_path


def _parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive thinking-axis sweep across models against omlx"
    )
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    parser.add_argument("--models", default="", help="comma-separated model shortlist")
    parser.add_argument(
        "--prompt-styles", default="",
        help=f"comma-separated subset of {_PROMPT_STYLES} (default: all)",
    )
    parser.add_argument(
        "--temperatures", default="",
        help=f"comma-separated temperatures (default: {_TEMPERATURES})",
    )
    parser.add_argument("--queries", default="", help="comma-separated query subset")
    parser.add_argument("--out", default="")
    parser.add_argument("--skip-off", action="store_true")
    parser.add_argument("--skip-on", action="store_true")
    parser.add_argument(
        "--force", action="store_true",
        help=f"bypass the {_MAX_CELLS_DEFAULT}-cell guard",
    )
    args = parser.parse_args(argv)

    if not args.base_url.startswith(("http://", "https://")):
        sys.exit(f"--base-url must start with http:// or https://; got: {args.base_url!r}")

    # Discover models
    print(f"Discovering models at {args.base_url} …")
    try:
        discovered = discover_omlx_models(args.base_url)
    except RuntimeError as exc:
        sys.exit(str(exc))
    if not discovered:
        sys.exit("No models returned by /v1/models. Is omlx running?")

    # Resolve axes
    if args.models:
        active_models = _parse_csv(args.models)
        missing = [m for m in active_models if m not in discovered]
        if missing:
            sys.exit(f"Model(s) not found in /v1/models: {', '.join(missing)}")
    else:
        active_models = discovered

    if args.prompt_styles:
        active_prompts = _parse_csv(args.prompt_styles)
        bad = [p for p in active_prompts if p not in _PROMPT_STYLES]
        if bad:
            sys.exit(f"Unknown prompt style(s): {', '.join(bad)} (allowed: {_PROMPT_STYLES})")
    else:
        active_prompts = list(_PROMPT_STYLES)

    if args.temperatures:
        try:
            active_temps = [float(t) for t in _parse_csv(args.temperatures)]
        except ValueError as exc:
            sys.exit(f"--temperatures parse error: {exc}")
    else:
        active_temps = list(_TEMPERATURES)

    queries_path = _HERE / "queries.md"
    all_queries = load_queries(queries_path)
    if args.queries:
        requested = set(_parse_csv(args.queries))
        active_queries = [q for q in all_queries if q.id in requested]
        missing_qs = requested - {q.id for q in active_queries}
        if missing_qs:
            sys.exit(f"Unknown query ID(s): {', '.join(sorted(missing_qs))}")
    else:
        active_queries = all_queries
    if not active_queries:
        sys.exit("No queries selected.")

    # Phase list (off, then on, honoring skip flags)
    phases: list[bool] = []
    if not args.skip_off:
        phases.append(False)
    if not args.skip_on:
        phases.append(True)
    if not phases:
        sys.exit("Both --skip-off and --skip-on were passed; nothing to do.")

    # Cell-count guard
    cells_per_phase = len(active_prompts) * len(active_temps) * len(active_queries)
    total_cells = len(active_models) * len(phases) * cells_per_phase
    if total_cells > _MAX_CELLS_DEFAULT and not args.force:
        sys.exit(
            f"Cell count {total_cells} exceeds the {_MAX_CELLS_DEFAULT}-cell guard "
            f"({len(active_models)} models × {len(phases)} phases × "
            f"{len(active_prompts)} prompts × {len(active_temps)} temps × "
            f"{len(active_queries)} queries). Pass --force to override."
        )

    # Output dir
    if args.out:
        run_dir = Path(args.out)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = _HERE / "results" / f"thinking-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output → {run_dir}")
    print(f"Models ({len(active_models)}): {active_models}")
    print(f"Prompt styles: {active_prompts}")
    print(f"Temperatures: {active_temps}")
    print(f"Queries ({len(active_queries)}): {[q.id for q in active_queries]}")
    print(f"Phases per model: {['ON' if p else 'OFF' for p in phases]}")
    print(f"Total cells: {total_cells}  (human prompts: {len(phases)})")

    rows: list[dict] = []
    t_start = time.monotonic()
    done = 0

    for thinking in phases:
        if not _prompt_user(thinking, active_models):
            print("  (phase skipped by user)")
            done += len(active_models) * cells_per_phase
            continue
        for model_idx, model in enumerate(active_models):
            if model_idx > 0:
                print(
                    f"  (pausing {_INTER_MODEL_PAUSE_S}s to let omlx evict the "
                    f"previous model)"
                )
                time.sleep(_INTER_MODEL_PAUSE_S)
            phase_rows, done = _run_phase(
                base_url=args.base_url,
                model=model,
                thinking=thinking,
                prompt_styles=active_prompts,
                temperatures=active_temps,
                queries=active_queries,
                run_dir=run_dir,
                counter_start=done,
                counter_total=total_cells,
            )
            rows.extend(phase_rows)

    wall_s = time.monotonic() - t_start
    print(f"\nDone in {wall_s:.1f}s")

    manifest = _write_manifest(
        run_dir=run_dir,
        base_url=args.base_url,
        models=active_models,
        prompt_styles=active_prompts,
        temperatures=active_temps,
        query_ids=[q.id for q in active_queries],
        rows=rows,
        wall_s=wall_s,
    )
    print(f"Manifest → {manifest}")

    error_count = sum(1 for r in rows if r["status"] == "error")
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
