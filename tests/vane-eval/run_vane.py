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

  temperature  → mutate `~/.research/vane-data/data/config.json` under
                 `modelProviders[*].config.options.temperature`, then
                 `docker restart research-vane` and wait for /api 200.
                 Vane's openaiLLM falls back to this value when the agent
                 calls generateText without an explicit `options.temperature`.
  thinking     → not exposed by the API or config, and a per-request
                 `chat_template_kwargs: {enable_thinking: true}` is silently
                 ignored by omlx for Gemma models (cells.py docstring).
                 The only working knob is omlx's per-loaded-model server
                 config. So we prompt the human before the sweep — exactly
                 like run_thinking.py — to configure omlx so each winner
                 model has its required thinking state. If the same model
                 is required in both ON and OFF states across the winners
                 we abort, because omlx cannot serve both simultaneously.
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
import subprocess
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
_DEFAULT_VANE_DATA_DIR = Path(os.path.expanduser("~/.research/vane-data"))
_DEFAULT_RESEARCH_DIR = Path(os.path.expanduser("~/.research"))
_HEALTH_TIMEOUT_S = 60


# ── HTTP helpers ───────────────────────────────────────────────────────────────


def _http_get_json(url: str, timeout_s: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def _http_post_json(url: str, body: dict, timeout_s: int = 300) -> tuple[int, Any]:
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


# ── Temperature mutation + container restart ──────────────────────────────────


def mutate_temperature(
    config_path: Path,
    provider_id: str,
    temperature: float,
) -> bool:
    """Set modelProviders[id=provider_id].config.options.temperature.

    Returns True if the file was written, False if the existing value already
    matched (no-op). Raises ValueError if the provider is missing.
    """
    data = json.loads(config_path.read_text())
    providers = data.get("modelProviders") or []
    target = next((p for p in providers if p.get("id") == provider_id), None)
    if target is None:
        raise ValueError(
            f"provider id {provider_id!r} not in {config_path} modelProviders"
        )
    cfg = target.setdefault("config", {})
    options = cfg.setdefault("options", {})
    if options.get("temperature") == temperature:
        return False
    options["temperature"] = temperature
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def restart_vane_container(container: str = "research-vane") -> None:
    """Run `docker restart <container>`. Raises CalledProcessError on failure."""
    subprocess.run(["docker", "restart", container], check=True, capture_output=True)


def wait_vane_healthy(base_url: str, timeout_s: int = _HEALTH_TIMEOUT_S) -> None:
    """Poll {base_url}/api/providers until 200 or timeout."""
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _http_get_json(f"{base_url.rstrip('/')}/api/providers", timeout_s=5)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1)
    raise TimeoutError(f"Vane did not become healthy at {base_url} ({last_err})")


# ── Thinking-state prompt ──────────────────────────────────────────────────────


def required_thinking_states(cells: list[Cell]) -> dict[str, str]:
    """Return {model: 'ON'|'OFF'} for the thinking state each model must serve.

    Raises ValueError if a model is required in both ON and OFF states — omlx
    cannot serve both at once for the same loaded model.
    """
    needs: dict[str, str] = {}
    for c in cells:
        state = "ON" if c.thinking else "OFF"
        existing = needs.get(c.model)
        if existing is None:
            needs[c.model] = state
        elif existing != state:
            raise ValueError(
                f"Model {c.model!r} is required in both thinking ON and OFF "
                f"across the winners. omlx serves one thinking state per "
                f"loaded model; split the run."
            )
    return needs


def prompt_thinking_config(needs: dict[str, str]) -> bool:
    """Block until the human confirms omlx is configured. Returns False on 'skip'."""
    print()
    print("━━ omlx thinking configuration check ━━")
    print("  Vane has no per-request thinking toggle and omlx ignores")
    print("  `chat_template_kwargs.enable_thinking`. Configure omlx so each")
    print("  model below is loaded with the indicated thinking state:")
    for model, state in needs.items():
        print(f"    - {model}: thinking {state}")
    print("  Then press Enter to continue, or type 'skip' to abort.")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        print("  (no TTY; treating as skip — pass --assume-configured to bypass)")
        return False
    return answer != "skip"


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

    status = "error" if result.get("error") else "ok"

    front = [
        "---",
        f"query_id: {query_id}",
        f"model: {cell.model!r}",
        f"prompt_style: {cell.prompt_style!r}",
        f"temperature: {cell.temperature}",
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


def _winners_to_cells(winners: dict) -> list[Cell]:
    out: list[Cell] = []
    for d in [winners["winner"]] + list(winners.get("ablations") or []):
        label = d.get("label") or (
            f"{d['model']} · {d['prompt_style']} · t={d['temperature']} · "
            f"think={'on' if d['thinking'] else 'off'}"
        )
        out.append(Cell(
            query_id="",
            model=d["model"],
            prompt_style=d["prompt_style"],
            temperature=float(d["temperature"]),
            thinking=bool(d["thinking"]),
            label=label,
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vane confirm sweep")
    parser.add_argument("--winners", required=True, help="path to winners.json")
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
        "--vane-data-dir",
        default=str(_DEFAULT_VANE_DATA_DIR),
        help="Vane persistent state dir (host path)",
    )
    parser.add_argument(
        "--research-dir",
        default=str(_DEFAULT_RESEARCH_DIR),
        help="research dir for denylist composition",
    )
    parser.add_argument("--out", default="", help="output dir (default: results/vane-<UTC-ts>)")
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="skip docker restart between cells (assumes temperature=fixed)",
    )
    parser.add_argument(
        "--assume-configured",
        action="store_true",
        help="skip the interactive omlx-thinking-state prompt (CI/non-TTY use)",
    )
    args = parser.parse_args(argv)

    if not args.base_url.startswith(("http://", "https://")):
        sys.exit(f"--base-url must be an http(s) URL: {args.base_url!r}")

    # Load winners.json
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

    # Thinking-state attestation: omlx wires thinking server-side per loaded
    # model and ignores per-request `chat_template_kwargs.enable_thinking`,
    # so the human must configure omlx before the sweep starts. Otherwise
    # cells with thinking=true silently produce non-thinking output while
    # the manifest claims thinking was on.
    try:
        thinking_needs = required_thinking_states(cells)
    except ValueError as exc:
        sys.exit(str(exc))
    if args.assume_configured:
        print("Skipping thinking-config prompt (--assume-configured).")
        for model, state in thinking_needs.items():
            print(f"  · assuming {model} loaded with thinking {state}")
    else:
        if not prompt_thinking_config(thinking_needs):
            sys.exit("Aborted: omlx thinking state not confirmed.")

    # Denylist for metrics
    denylist = load_denylist_domains(Path(args.research_dir))
    print(f"Loaded {len(denylist)} denylist domains for metrics.")

    # Vane data config (for temperature mutation)
    config_path = Path(args.vane_data_dir) / "data" / "config.json"
    config_writable = config_path.exists() and not args.no_restart

    # Sweep
    cell_results: list[dict] = []
    t_start = time.monotonic()
    last_temperature_for_provider: dict[str, float | None] = {}

    total = len(cells) * len(active_queries)
    done = 0

    for cell, provider_id in zip(cells, cell_provider_ids):
        # Apply temperature for this cell's provider, restart if changed
        if config_writable:
            try:
                changed = mutate_temperature(config_path, provider_id, cell.temperature)
                if changed and last_temperature_for_provider.get(provider_id) is not None:
                    print(f"  ↻ restart research-vane (temp → {cell.temperature})")
                    restart_vane_container()
                    wait_vane_healthy(args.base_url)
                elif changed:
                    print(f"  · temp set to {cell.temperature} (no restart needed; first cell)")
                last_temperature_for_provider[provider_id] = cell.temperature
            except Exception as exc:  # noqa: BLE001
                print(f"  ! temperature mutation failed: {exc}", file=sys.stderr)

        for query in active_queries:
            done += 1
            cell_with_q = copy.copy(cell)
            cell_with_q.query_id = query.id
            print(f"[{done}/{total}] {query.id}  {cell.label}", end=" … ", flush=True)

            body = build_vane_body(
                cell={
                    "model": cell.model,
                    "prompt_style": cell.prompt_style,
                    "temperature": cell.temperature,
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
            cell_results.append({
                "file": str(out_path.relative_to(run_dir)),
                "label": cell.label,
                "status": "error" if error else "ok",
            })
            print("error" if error else "ok")

    wall_s = time.monotonic() - t_start

    source_run: str | None = None
    if winners_path.parent.name.startswith("cheap-"):
        source_run = f"../{winners_path.parent.name}/"

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
