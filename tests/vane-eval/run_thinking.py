"""Interactive thinking-axis comparison against omlx.

omlx exposes no per-request thinking toggle; thinking is a server-side per-loaded-
model setting. To compare thinking on/off for the same logical model, the human
must reload the model in omlx between phases. This script pauses for that.

Usage:
    uv run python tests/vane-eval/run_thinking.py [options]

Options:
    --base-url URL       omlx base URL (default: http://0.0.0.0:8000/v1)
    --model NAME         model id as exposed by /v1/models (required)
    --queries q1,q3      comma-separated query subset (default: all six)
    --prompt-style S     bare | structured | research_system (default: structured)
    --temperature F      sampling temperature (default: 0.3)
    --out PATH           output directory (default: results/thinking-<UTC-ts>)
    --skip-off           skip the thinking=off phase
    --skip-on            skip the thinking=on phase
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

from lib.cells import Cell, call_omlx, discover_omlx_models, write_cell_output
from lib.queries import load as load_queries

_DEFAULT_BASE_URL = "http://0.0.0.0:8000/v1"


def _prompt_user(model: str, thinking: bool) -> bool:
    """Block until the human confirms omlx is configured. Returns False to skip."""
    state = "ON" if thinking else "OFF"
    print()
    print(f"━━ thinking={state} phase ━━")
    print(f"  Configure omlx so model {model!r} has thinking {state}.")
    print(f"  Then press Enter to continue, or type 'skip' to skip this phase.")
    answer = input("> ").strip().lower()
    return answer != "skip"


def _run_phase(
    base_url: str,
    model: str,
    prompt_style: str,
    temperature: float,
    thinking: bool,
    queries: list,
    run_dir: Path,
) -> list[dict]:
    rows: list[dict] = []
    for i, query in enumerate(queries, start=1):
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
        print(f"  [{i}/{len(queries)}] {query.id}", end=" … ", flush=True)
        result = call_omlx(base_url=base_url, cell=cell, query=query.query)
        out_path = write_cell_output(
            run_dir=run_dir,
            cell=cell,
            query_id=query.id,
            query_text=query.query,
            reference_text=query.reference,
            result=result,
        )
        if result["error"]:
            status = "error"
        elif thinking and result["reasoning"] is None:
            status = "skip:no-thinking-support"
        elif (not thinking) and result["reasoning"] is not None:
            status = "warn:reasoning-leaked"
        else:
            status = "ok"
        rows.append({
            "file": str(out_path.relative_to(run_dir)),
            "label": cell.label,
            "status": status,
        })
        print(status)
    return rows


def _write_manifest(
    run_dir: Path,
    model: str,
    prompt_style: str,
    temperature: float,
    base_url: str,
    rows: list[dict],
    wall_s: float,
) -> Path:
    lines = [
        "# MANIFEST (run_thinking)",
        "",
        "## Run configuration",
        "",
        f"- **omlx base URL:** `{base_url}`",
        f"- **model:** `{model}`",
        f"- **prompt style:** `{prompt_style}`",
        f"- **temperature:** `{temperature}`",
        f"- **total wall-clock:** {wall_s:.1f}s",
        "",
        "Reasoning-leak status (`warn:reasoning-leaked`) means the human said "
        "thinking was OFF but `reasoning_content` came back populated — re-check "
        "the omlx model load.",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive thinking on/off comparison against omlx"
    )
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    parser.add_argument("--model", required=True, help="model id (must match /v1/models)")
    parser.add_argument("--queries", default="", help="comma-separated query subset")
    parser.add_argument(
        "--prompt-style", default="structured",
        choices=["bare", "structured", "research_system"],
    )
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--out", default="")
    parser.add_argument("--skip-off", action="store_true")
    parser.add_argument("--skip-on", action="store_true")
    args = parser.parse_args(argv)

    if not args.base_url.startswith(("http://", "https://")):
        sys.exit(f"--base-url must start with http:// or https://; got: {args.base_url!r}")

    # Discover models — validates connectivity and confirms the named model exists
    print(f"Discovering models at {args.base_url} …")
    try:
        discovered = discover_omlx_models(args.base_url)
    except RuntimeError as exc:
        sys.exit(str(exc))
    if args.model not in discovered:
        sys.exit(f"Model {args.model!r} not found in /v1/models. Available: {discovered}")

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

    # Output dir
    if args.out:
        run_dir = Path(args.out)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = _HERE / "results" / f"thinking-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output → {run_dir}")
    print(f"Model: {args.model} · prompt={args.prompt_style} · t={args.temperature}")
    print(f"Queries ({len(active_queries)}): {[q.id for q in active_queries]}")

    rows: list[dict] = []
    t_start = time.monotonic()

    phases: list[tuple[bool, bool]] = [
        (False, args.skip_off),
        (True, args.skip_on),
    ]
    for thinking, skip_flag in phases:
        if skip_flag:
            print(f"\n(skipping thinking={'ON' if thinking else 'OFF'} phase via --skip flag)")
            continue
        if not _prompt_user(args.model, thinking):
            print("  (skipped by user)")
            continue
        rows.extend(_run_phase(
            base_url=args.base_url,
            model=args.model,
            prompt_style=args.prompt_style,
            temperature=args.temperature,
            thinking=thinking,
            queries=active_queries,
            run_dir=run_dir,
        ))

    wall_s = time.monotonic() - t_start
    print(f"\nDone in {wall_s:.1f}s")

    manifest = _write_manifest(
        run_dir=run_dir,
        model=args.model,
        prompt_style=args.prompt_style,
        temperature=args.temperature,
        base_url=args.base_url,
        rows=rows,
        wall_s=wall_s,
    )
    print(f"Manifest → {manifest}")

    error_count = sum(1 for r in rows if r["status"] == "error")
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
