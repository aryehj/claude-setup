"""Phase 3: Vane confirm sweep.

Replays the cheap-phase winner + 1–2 ablations through Vane's full pipeline
(SearXNG → scrape → cite → answer) on a query subset, so the human grader can
check whether the cheap winner survives once Vane is in the path.

## Resolved unknowns (probed 2026-04-26 against Vane master src)

Unknown #1 — Vane HTTP API:
  GET  http://localhost:3000/api/providers
       → {"providers": [{"id": str, "name": str,
                         "chatModels": [{"name", "key"}],
                         "embeddingModels": [{"name", "key"}]}, ...]}
  POST http://localhost:3000/api/search
       Body fields (Vane src/app/api/search/route.ts):
         chatModel:        {providerId, key}    (required)
         embeddingModel:   {providerId, key}    (required)
         query:            str                  (required)
         sources:          ["web"|...]          (required)
         optimizationMode: "speed"|"balanced"|"quality"   (default speed)
         history:          [["human"|"assistant", str], ...]   (default [])
         systemInstructions: str                (optional)
         stream:           bool                 (default false)
       Response (stream=false):
         {"message": str, "sources": [{content, metadata: {title, url}}, ...]}

Unknown #2 — Vane forwards neither `temperature` nor a thinking flag in its
request body. Knobs map as follows:

  temperature  → not exposed. Vane runs the underlying model at temperature=1
                 every call regardless of any config setting, so this script
                 deliberately does not send, mutate, or restart on temperature.
                 The matrix is therefore (model × prompt_style × thinking).
  thinking     → not exposed by the API or config, and a per-request
                 `chat_template_kwargs: {enable_thinking: true}` is silently
                 ignored by omlx for Gemma models (cells.py docstring).
                 The only working knob is omlx's per-loaded-model server
                 config. So we group cells by thinking state into phases
                 (OFF, then ON) and prompt the human before each phase
                 exactly like run_thinking.py, so they can flip omlx in
                 between. The same model can appear in both phases
                 (e.g. cheap winner thinking=False + thinking-phase
                 ablation thinking=True on the same model) — we just
                 prompt twice.
  prompt_style → research_system uses `systemInstructions`; structured
                 prepends a format hint to `query`; bare leaves both empty.

This script is intended to be run on the macOS host (where `localhost:3000`
resolves to Vane and `docker` reaches the research VM). Invoking it from
inside `start-claude.sh`'s microVM will fail at the Vane HTTP and `docker`
hops.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lib.cells import Cell, _RESEARCH_SYSTEM, _STRUCTURED_HINT  # noqa: E402
from lib.queries import load as load_queries  # noqa: E402

_DEFAULT_VANE_URL = "http://localhost:3000"
_DEFAULT_QUERIES = ["q1", "q3", "q5"]
_DEFAULT_RESEARCH_DIR = Path(os.path.expanduser("~/.research"))


# ── HTTP helpers ───────────────────────────────────────────────────────────────


def _http_get_json(url: str, timeout_s: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def _http_post_json(url: str, body: dict, timeout_s: int = 1200) -> tuple[int, Any]:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read())
        except Exception:  # noqa: BLE001
            err_body = {"raw": str(exc)}
        return exc.code, err_body


# ── Provider lookup / body construction ────────────────────────────────────────


def find_provider_id(providers: list[dict], model_key: str) -> str:
    """Return the provider id whose chatModels include `model_key`."""
    for p in providers:
        for m in p.get("chatModels") or []:
            if m.get("key") == model_key:
                return p["id"]
    available = sorted(
        m["key"]
        for p in providers
        for m in (p.get("chatModels") or [])
        if "key" in m
    )
    raise ValueError(
        f"Model {model_key!r} not exposed by any Vane provider. "
        f"Available chat models: {available}"
    )


def find_embedding_model(providers: list[dict]) -> dict[str, str]:
    """Pick the first available embedding model. Returns {providerId, key}."""
    for p in providers:
        for m in p.get("embeddingModels") or []:
            if m.get("key"):
                return {"providerId": p["id"], "key": m["key"]}
    raise ValueError("No embedding model available on any Vane provider.")


def build_vane_body(
    cell: dict,
    query: str,
    provider_id: str,
    embedding: dict[str, str],
) -> dict:
    """Construct a POST /api/search body for one cell × query."""
    style = cell["prompt_style"]
    user_query = query
    system = ""
    if style == "structured":
        user_query = f"{_STRUCTURED_HINT}\n\n{query}"
    elif style == "research_system":
        system = _RESEARCH_SYSTEM
    elif style != "bare":
        raise ValueError(f"Unknown prompt_style: {style!r}")

    body: dict[str, Any] = {
        "chatModel": {"providerId": provider_id, "key": cell["model"]},
        "embeddingModel": embedding,
        "query": user_query,
        "sources": ["web"],
        "optimizationMode": "balanced",
        "history": [],
        "stream": False,
    }
    if system:
        body["systemInstructions"] = system
    return body


# ── Metrics & denylist ─────────────────────────────────────────────────────────


_EDU_GOV_WIKI_SUFFIXES = (".edu", ".gov")


def _hostname(url: str) -> str:
    # urllib.parse handles missing scheme poorly; do it manually.
    if "://" in url:
        url = url.split("://", 1)[1]
    return url.split("/", 1)[0].lower()


def _matches_denylist(host: str, denylist_domains: set[str]) -> bool:
    """Match host against the denylist using suffix-matching at label boundaries."""
    if host in denylist_domains:
        return True
    for d in denylist_domains:
        if host.endswith("." + d):
            return True
    return False


def compute_metrics(
    sources: list[dict],
    denylist_domains: set[str],
) -> dict[str, Any]:
    """Derive citation_count, edu_gov_wiki_share, denylist_hits from Vane sources."""
    if not sources:
        return {"citation_count": 0, "edu_gov_wiki_share": 0.0, "denylist_hits": 0}

    citation_count = len(sources)
    edu_gov_wiki = 0
    denylist_hits = 0
    for src in sources:
        url = (src.get("metadata") or {}).get("url", "")
        host = _hostname(url)
        if not host:
            continue
        if (
            host.endswith(_EDU_GOV_WIKI_SUFFIXES)
            or "wikipedia.org" in host
        ):
            edu_gov_wiki += 1
        if _matches_denylist(host, denylist_domains):
            denylist_hits += 1

    return {
        "citation_count": citation_count,
        "edu_gov_wiki_share": edu_gov_wiki / citation_count,
        "denylist_hits": denylist_hits,
    }


def load_denylist_domains(research_dir: Path) -> set[str]:
    """Compose the denylist as (cached-upstream ∪ additions) − overrides.

    Mirrors research.py:compose_denylist but stays import-free so this script
    can run anywhere `~/.research/` is mounted.
    """
    domains: set[str] = set()
    cache_dir = research_dir / "denylist-cache"
    if cache_dir.is_dir():
        for cached in sorted(cache_dir.glob("*.txt")):
            domains.update(_read_domain_lines(cached))
    additions = research_dir / "denylist-additions.txt"
    if additions.exists():
        domains.update(_read_domain_lines(additions))
    overrides = research_dir / "denylist-overrides.txt"
    if overrides.exists():
        domains -= set(_read_domain_lines(overrides))
    return domains


def _read_domain_lines(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # Strip leading "." which research.py uses for Squid suffix-match
        if line.startswith("."):
            line = line[1:]
        out.append(line.lower())
    return out


# ── Thinking-state phases ──────────────────────────────────────────────────────


def split_phases(
    cells_with_pids: list[tuple[Cell, str]],
) -> list[tuple[bool, list[tuple[Cell, str]]]]:
    """Group cells by thinking state into ordered phases (OFF first, then ON).

    omlx wires thinking server-side per loaded model and silently ignores
    `chat_template_kwargs.enable_thinking`. The same model with thinking ON
    and OFF is therefore two distinct loaded configurations; the human must
    flip omlx between phases. Returning OFF first matches run_thinking.py
    and reduces the chance of leftover-warm-thinking contamination.
    """
    off = [(c, p) for c, p in cells_with_pids if not c.thinking]
    on = [(c, p) for c, p in cells_with_pids if c.thinking]
    phases: list[tuple[bool, list[tuple[Cell, str]]]] = []
    if off:
        phases.append((False, off))
    if on:
        phases.append((True, on))
    return phases


def prompt_thinking_phase(thinking: bool, models: list[str]) -> bool:
    """Block until the human confirms omlx is configured for this phase.

    Returns False on 'skip' (skip this phase). Returns False on EOF (no TTY)
    so the caller can fall back to --assume-configured guidance.
    """
    state = "ON" if thinking else "OFF"
    print()
    print(f"━━ thinking={state} phase ━━")
    print("  Vane has no per-request thinking toggle and omlx ignores")
    print("  `chat_template_kwargs.enable_thinking`, so configure omlx now")
    print(f"  so thinking is {state} for ALL of these models:")
    for m in models:
        print(f"    - {m}")
    print("  Then press Enter to continue, or type 'skip' to skip this phase.")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        print("  (no TTY; treating as skip — pass --assume-configured to bypass)")
        return False
    return answer != "skip"


# ── Status classification ──────────────────────────────────────────────────────


_SEARXNG_MISS_MARKERS = (
    "could not find",
    "couldn't find",
    "no relevant information",
    "no relevant results",
    "no information",
)


def classify_vane_status(result: dict[str, Any], sources: list[dict]) -> str:
    """Worst-wins status for one Vane cell.

      error             — HTTP/transport failure
      error:no-content  — request succeeded but text is empty/whitespace.
      error:no-results  — Vane ran the search and got nothing usable: text
                          present but matches the "sorry I could not find any
                          relevant information" fallback AND sources is empty.
                          A real retrieval miss; the SearXNG/scrape side of
                          Vane is the suspect.
      error:skip-search — Substantive answer text but zero sources. Vane's
                          agent decided the question was answerable from
                          parametric memory and did not invoke SearXNG at all.
                          This is a research-quality regression masquerading
                          as success — DO NOT bury it under no-content again.
      ok                — none of the above

    The skip-search vs no-results split exists because the prior single
    "error:no-content" bucket conflated two very different failure modes
    in earlier runs and let one of them go unexamined.
    """
    if result.get("error"):
        return "error"
    text = (result.get("text") or "").strip()
    if not text:
        return "error:no-content"
    if not sources:
        lowered = text.lower()
        if any(marker in lowered for marker in _SEARXNG_MISS_MARKERS):
            return "error:no-results"
        return "error:skip-search"
    return "ok"


def compute_source_run(winners_path: Path) -> str | None:
    """Return the MANIFEST `source:` link for a winners.json path.

    Recognises both `cheap-` (run_cheap.py) and `thinking-` (run_thinking.py)
    upstream run dirs. Returns None for unrelated dirs so MANIFEST omits
    the line.
    """
    parent_name = winners_path.parent.name
    if parent_name.startswith(("cheap-", "thinking-")):
        return f"../{parent_name}/"
    return None


# ── Cell file writer ───────────────────────────────────────────────────────────


def _safe_label(label: str) -> str:
    s = (
        label.replace(" ", "_")
        .replace("·", "-")
        .replace("=", "")
        .replace("/", "-")
        .replace(":", "")
    )
    return "".join(c if c.isalnum() or c in "-_." else "" for c in s)


def write_vane_cell_output(
    run_dir: Path,
    cell: Cell,
    query_id: str,
    query_text: str,
    reference_text: str,
    result: dict[str, Any],
    sources: list[dict],
    metrics: dict[str, Any],
) -> Path:
    """Write a single .md per Vane cell, with citations + metrics block."""
    filename = f"{query_id}_{_safe_label(cell.label)}.md"
    out_path = run_dir / filename
    run_dir.mkdir(parents=True, exist_ok=True)

    status = classify_vane_status(result, sources)

    front = [
        "---",
        f"query_id: {query_id}",
        f"model: {cell.model!r}",
        f"prompt_style: {cell.prompt_style!r}",
        f"thinking: {str(cell.thinking).lower()}",
        f"label: {cell.label!r}",
        f"latency_s: {result.get('latency_s', 0.0):.2f}",
        f"status: {status}",
        f"phase: vane",
        f"run_dir: {run_dir.name}",
        "---",
    ]

    parts = [
        "\n".join(front),
        "",
        f"## Query\n\n{query_text}",
        "",
        f"## Reference\n\n{reference_text}",
        "",
        f"## Response\n\n{result.get('text') or '*(empty)*'}",
        "",
        "## Citations",
        "",
    ]
    if sources:
        for i, s in enumerate(sources, 1):
            md = s.get("metadata") or {}
            parts.append(f"{i}. [{md.get('title', '(no title)')}]({md.get('url', '')})")
    else:
        parts.append("*(no sources returned)*")

    parts += [
        "",
        "## Metrics",
        "",
        f"- citation_count: {metrics['citation_count']}",
        f"- edu_gov_wiki_share: {metrics['edu_gov_wiki_share']:.3f}",
        f"- denylist_hits: {metrics['denylist_hits']}",
    ]

    if result.get("error"):
        parts += ["", f"## Error\n\n```\n{result['error']}\n```"]

    raw = result.get("raw") or {}
    parts += [
        "",
        "## Raw response (JSON)",
        "",
        f"```json\n{json.dumps(raw, indent=2)}\n```",
    ]

    out_path.write_text("\n".join(parts) + "\n")
    return out_path


# ── Manifest ───────────────────────────────────────────────────────────────────


def write_vane_manifest(
    run_dir: Path,
    cell_results: list[dict],
    cells: list[Cell],
    queries: list[str],
    base_url: str,
    source_run: str | None,
    wall_s: float,
) -> Path:
    lines = [
        "# MANIFEST (Vane phase)",
        "",
    ]
    if source_run:
        lines.append(f"source: {source_run}")
        lines.append("")
    lines += [
        "## Run configuration",
        "",
        f"- **Vane base URL:** `{base_url}`",
        f"- **queries:** {queries}",
        f"- **cells:** {[c.label for c in cells]}",
        f"- **total wall-clock:** {wall_s:.1f}s",
        "",
        "## Cells",
        "",
        "| file | label | status |",
        "|------|-------|--------|",
    ]
    for r in cell_results:
        lines.append(f"| `{r['file']}` | {r['label']} | {r['status']} |")

    path = run_dir / "MANIFEST.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_thinking_arg(value: str) -> list[bool]:
    """Parse '--thinking off,on' (or 'on' / 'off') into a bool list."""
    out: list[bool] = []
    for tok in value.split(","):
        t = tok.strip().lower()
        if t == "off":
            out.append(False)
        elif t == "on":
            out.append(True)
        else:
            raise ValueError(f"--thinking values must be 'off' or 'on', got {tok!r}")
    return out


def build_matrix_cells(
    models: list[str],
    prompt_styles: list[str],
    thinking_states: list[bool],
) -> list[Cell]:
    """Cross-product matrix flags into Cells.

    Order: model → prompt_style → thinking (OFF before ON within a thinking
    pair). Temperature is not an axis — Vane runs models at temperature=1
    server-side regardless, so the script neither sweeps nor sets it. Cells
    carry temperature=0.0 as an unused placeholder. The thinking-OFF-first
    inner ordering keeps split_phases happy without re-sorting and matches
    what run_thinking.py does.
    """
    sorted_thinking = sorted(set(thinking_states))  # False (0) before True (1)
    cells: list[Cell] = []
    for model in models:
        for style in prompt_styles:
            for thinking in sorted_thinking:
                label = (
                    f"{model} · {style} · think={'on' if thinking else 'off'}"
                )
                cells.append(Cell(
                    query_id="",
                    model=model,
                    prompt_style=style,
                    temperature=0.0,
                    thinking=bool(thinking),
                    label=label,
                ))
    return cells


def _winners_to_cells(winners: dict) -> list[Cell]:
    """Build cells from a winners.json. Any temperature field is ignored
    (Vane pins temperature=1 server-side)."""
    out: list[Cell] = []
    for d in [winners["winner"]] + list(winners.get("ablations") or []):
        label = d.get("label") or (
            f"{d['model']} · {d['prompt_style']} · "
            f"think={'on' if d['thinking'] else 'off'}"
        )
        out.append(Cell(
            query_id="",
            model=d["model"],
            prompt_style=d["prompt_style"],
            temperature=0.0,
            thinking=bool(d["thinking"]),
            label=label,
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vane confirm sweep")
    parser.add_argument(
        "--winners",
        default=None,
        help="path to winners.json (winner + ablations mode). Mutually "
        "exclusive with the matrix flags below.",
    )
    parser.add_argument(
        "--models",
        default=None,
        help="comma-separated model keys (matrix mode)",
    )
    parser.add_argument(
        "--prompt-styles",
        default=None,
        help="comma-separated prompt styles: bare,structured,research_system",
    )
    parser.add_argument(
        "--thinking",
        default=None,
        help="comma-separated thinking states: off,on (or just 'off' or 'on')",
    )
    parser.add_argument(
        "--queries",
        default=",".join(_DEFAULT_QUERIES),
        help=f"comma-separated query IDs (default: {','.join(_DEFAULT_QUERIES)})",
    )
    parser.add_argument(
        "--base-url",
        default=_DEFAULT_VANE_URL,
        help=f"Vane base URL (default: {_DEFAULT_VANE_URL})",
    )
    parser.add_argument(
        "--research-dir",
        default=str(_DEFAULT_RESEARCH_DIR),
        help="research dir for denylist composition",
    )
    parser.add_argument("--out", default="", help="output dir (default: results/vane-<UTC-ts>)")
    parser.add_argument(
        "--assume-configured",
        action="store_true",
        help="skip the interactive omlx-thinking-state prompt (CI/non-TTY use)",
    )
    args = parser.parse_args(argv)

    if not args.base_url.startswith(("http://", "https://")):
        sys.exit(f"--base-url must be an http(s) URL: {args.base_url!r}")

    matrix_flags = [args.models, args.prompt_styles, args.thinking]
    matrix_mode = any(matrix_flags)
    winners_path: Path | None = None

    if matrix_mode and args.winners:
        sys.exit("Pass --winners OR matrix flags (--models/--prompt-styles/"
                 "--thinking), not both.")
    if not matrix_mode and not args.winners:
        sys.exit("One of --winners or the matrix flags "
                 "(--models, --prompt-styles, --thinking) is required.")

    if matrix_mode:
        missing = [n for n, v in zip(
            ("--models", "--prompt-styles", "--thinking"),
            matrix_flags,
        ) if not v]
        if missing:
            sys.exit(f"Matrix mode requires all of: {', '.join(missing)} also set.")
        try:
            thinking_list = parse_thinking_arg(args.thinking)
        except ValueError as exc:
            sys.exit(str(exc))
        cells = build_matrix_cells(
            models=[m.strip() for m in args.models.split(",") if m.strip()],
            prompt_styles=[s.strip() for s in args.prompt_styles.split(",") if s.strip()],
            thinking_states=thinking_list,
        )
        print(f"Matrix mode: {len(cells)} cells "
              f"({len(cells) // max(len(thinking_list), 1)} per thinking state).")
    else:
        winners_path = Path(args.winners)
        if not winners_path.exists():
            sys.exit(f"winners.json not found: {winners_path}")
        winners = json.loads(winners_path.read_text())
        cells = _winners_to_cells(winners)
        if len(cells) > 3:
            cells = cells[:3]
            print(f"Capping to {len(cells)} cells (winner + ≤2 ablations).")

    # Load queries
    queries_path = _HERE / "queries.md"
    all_queries = load_queries(queries_path)
    requested = {q.strip() for q in args.queries.split(",") if q.strip()}
    active_queries = [q for q in all_queries if q.id in requested]
    missing_qs = requested - {q.id for q in active_queries}
    if missing_qs:
        sys.exit(f"Unknown query ID(s): {', '.join(sorted(missing_qs))}")
    if not active_queries:
        sys.exit("No queries selected.")

    # Output dir
    if args.out:
        run_dir = Path(args.out)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = _HERE / "results" / f"vane-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Probe providers
    print(f"Querying {args.base_url}/api/providers …")
    providers_resp = _http_get_json(f"{args.base_url.rstrip('/')}/api/providers")
    providers = providers_resp["providers"]
    embedding = find_embedding_model(providers)

    # Resolve provider id per cell up-front (fail loud before any restarts)
    cell_provider_ids: list[str] = []
    for c in cells:
        pid = find_provider_id(providers, c.model)
        cell_provider_ids.append(pid)

    # Group cells by thinking state into phases. omlx wires thinking
    # server-side per loaded model and silently ignores
    # `chat_template_kwargs.enable_thinking`, so the human must flip omlx
    # between phases. We prompt before each phase exactly like
    # run_thinking.py.
    phases = split_phases(list(zip(cells, cell_provider_ids)))

    # Denylist for metrics
    denylist = load_denylist_domains(Path(args.research_dir))
    print(f"Loaded {len(denylist)} denylist domains for metrics.")

    # Sweep
    cell_results: list[dict] = []
    t_start = time.monotonic()

    total = len(cells) * len(active_queries)
    done = 0

    for thinking, phase_cells in phases:
        phase_models = sorted({c.model for c, _ in phase_cells})
        if args.assume_configured:
            state = "ON" if thinking else "OFF"
            print(f"Skipping thinking={state} prompt (--assume-configured).")
            for m in phase_models:
                print(f"  · assuming {m} loaded with thinking {state}")
        else:
            if not prompt_thinking_phase(thinking, phase_models):
                state = "ON" if thinking else "OFF"
                print(f"  (thinking={state} phase skipped)")
                done += len(phase_cells) * len(active_queries)
                continue

        for cell, provider_id in phase_cells:
            for query in active_queries:
                done += 1
                cell_with_q = copy.copy(cell)
                cell_with_q.query_id = query.id
                print(f"[{done}/{total}] {query.id}  {cell.label}", end=" … ", flush=True)

                body = build_vane_body(
                    cell={
                        "model": cell.model,
                        "prompt_style": cell.prompt_style,
                        "thinking": cell.thinking,
                    },
                    query=query.query,
                    provider_id=provider_id,
                    embedding=embedding,
                )

                t0 = time.monotonic()
                error: str | None = None
                raw: dict = {}
                text = ""
                sources: list[dict] = []

                try:
                    status, raw = _http_post_json(
                        f"{args.base_url.rstrip('/')}/api/search",
                        body=body,
                    )
                    if status != 200:
                        error = f"HTTP {status}: {raw}"
                    else:
                        text = raw.get("message") or ""
                        sources = raw.get("sources") or []
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)

                latency_s = time.monotonic() - t0
                metrics = compute_metrics(sources, denylist)

                result = {
                    "text": text,
                    "raw": raw,
                    "latency_s": latency_s,
                    "error": error,
                }
                out_path = write_vane_cell_output(
                    run_dir=run_dir,
                    cell=cell_with_q,
                    query_id=query.id,
                    query_text=query.query,
                    reference_text=query.reference,
                    result=result,
                    sources=sources,
                    metrics=metrics,
                )
                status = classify_vane_status(result, sources)
                cell_results.append({
                    "file": str(out_path.relative_to(run_dir)),
                    "label": cell.label,
                    "status": status,
                })
                print(status)

    wall_s = time.monotonic() - t_start

    source_run = compute_source_run(winners_path) if winners_path else None

    write_vane_manifest(
        run_dir=run_dir,
        cell_results=cell_results,
        cells=cells,
        queries=[q.id for q in active_queries],
        base_url=args.base_url,
        source_run=source_run,
        wall_s=wall_s,
    )
    print(f"\nVane sweep complete in {wall_s:.1f}s → {run_dir}")

    error_count = sum(1 for r in cell_results if r["status"] == "error")
    if error_count:
        print(f"{error_count}/{total} cells errored.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
