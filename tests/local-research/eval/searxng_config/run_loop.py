#!/usr/bin/env python3
"""
SearXNG-config tuning loop — runs on the macOS host (not inside the container).

Usage:
    ./tests/local-research/eval/searxng_config/run_loop.py [--max-minutes N] [--out-dir DIR]

Prerequisites:
    - research Colima profile running: ./research.py --backend=omlx
    - docker context colima-research accessible (bootstrap.sh already ensures this)
    - OMLX_API_KEY set; omlx reachable at localhost:8000 (or $OMLX_HOST)
    - NOTES_MODEL, EXPAND_MODEL env vars honoured (same as bootstrap.sh)

What it does:
    1. Reads the current ~/.research/searxng/settings.yml as baseline (iteration 0).
    2. Each iteration: calls the LLM to pick the next mutation → validates no constrained
       fields touched → writes ~/.research/searxng/settings.yml → docker restart
       research-searxng → polls until ready → runs score.py inside research-runner
       container → parses score → decides keep or revert → appends to iterations.jsonl.
    3. Stops when: wall-clock >= MAX_MINUTES, OR no improvement in 5 consecutive
       iterations, OR score within 5% of rolling best for 10 consecutive iterations.
    4. Writes RESULTS.md summary.

Mutation search space (one axis per iteration):
    - engine_list: add/remove engines from use_default_settings.engines.keep_only
    - engine_weights: adjust per-engine weight float
    - plugins: add/remove entries from enabled_plugins / disabled_plugins
    - hostnames: add entries to hostnames.low_priority / hostnames.high_priority / hostnames.remove
    - safe_search: set search.safe_search to 0, 1, or 2
    - engine_categories: reassign per-engine categories
    - engine_timeout: bump slow-but-quality engine timeouts

Constrained fields (never touched):
    - server.secret_key
    - server.base_url
    - outgoing.proxies
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import datetime
import hashlib

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

RESEARCH_DIR = pathlib.Path.home() / ".research"
SEARXNG_SETTINGS = RESEARCH_DIR / "searxng" / "settings.yml"
CONTAINER_SEARXNG = "research-searxng"
RESEARCH_NET = "research-net"
IMAGE = "research-runner:latest"

SCRIPT_DIR = pathlib.Path(__file__).parent
ITERATIONS_FILE = SCRIPT_DIR / "iterations.jsonl"

# How long to wait for SearXNG after restart before declaring failure.
SEARXNG_POLL_TIMEOUT_S = 60
SEARXNG_POLL_INTERVAL_S = 2

# Stop conditions.
DEFAULT_MAX_MINUTES = 60
NO_IMPROVEMENT_LIMIT = 5
NEAR_BEST_LIMIT = 10
NEAR_BEST_PCT = 0.05


# ---------------------------------------------------------------------------
# docker helpers
# ---------------------------------------------------------------------------

def _docker(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["docker", "--context", "colima-research"] + list(args)
    kwargs: dict = {"check": check}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(cmd, **kwargs)


def switch_docker_context() -> None:
    result = subprocess.run(
        ["docker", "context", "use", "colima-research"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print("warning: could not switch to colima-research context; assuming current context is correct", file=sys.stderr)


def restart_searxng() -> None:
    print("  restarting research-searxng ...", file=sys.stderr)
    _docker("restart", CONTAINER_SEARXNG)


def poll_searxng_ready(bridge_ip: str, searxng_port: int = 8080) -> bool:
    """Poll SearXNG inside the research-runner until it responds or times out."""
    deadline = time.monotonic() + SEARXNG_POLL_TIMEOUT_S
    # Use a throwaway container to curl SearXNG — avoids needing to know the
    # host-side port mapping (container is on research-net, not exposed to host).
    cmd_prefix = [
        "docker", "--context", "colima-research",
        "run", "--rm", "--network", RESEARCH_NET,
        "curlimages/curl:latest",
    ]
    while time.monotonic() < deadline:
        result = subprocess.run(
            cmd_prefix + [
                "-sf", "--max-time", "3",
                f"http://{CONTAINER_SEARXNG}:{searxng_port}/search?q=test&format=json",
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                if isinstance(data.get("results"), list):
                    return True
            except json.JSONDecodeError:
                pass
        time.sleep(SEARXNG_POLL_INTERVAL_S)
    return False


def run_score(out_dir: pathlib.Path, settings_file: pathlib.Path, bridge_ip: str) -> dict | None:
    """Run score.py inside research-runner. Returns parsed JSON or None on failure."""
    squid_port = 8888
    omlx_base_url = os.environ.get("OMLX_BASE_URL") or "http://host.docker.internal:8000/v1"
    omlx_api_key = os.environ.get("OMLX_API_KEY") or ""
    notes_model = os.environ.get("NOTES_MODEL") or "gemma-4-26b-a4b-it-8bit"

    cmd = [
        "docker", "--context", "colima-research",
        "run", "--rm",
        "--network", RESEARCH_NET,
        "--add-host", "host.docker.internal:host-gateway",
        "-v", f"{out_dir}:/app/eval",
        "-v", f"{pathlib.Path.home() / '.research' / 'sessions'}:/sessions",
        "-e", f"HTTP_PROXY=http://{bridge_ip}:{squid_port}",
        "-e", f"HTTPS_PROXY=http://{bridge_ip}:{squid_port}",
        "-e", f"NO_PROXY={CONTAINER_SEARXNG},host.docker.internal,localhost,127.0.0.1",
        "-e", f"OMLX_BASE_URL={omlx_base_url}",
        "-e", f"OMLX_API_KEY={omlx_api_key}",
        "-e", f"NOTES_MODEL={notes_model}",
        IMAGE,
        "python", "-m", "eval.searxng_config.score",
        "--settings-file", f"/app/eval/searxng_config/current_settings.yml",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0:
            print(f"    score.py exited {result.returncode}: {result.stderr[-500:]}", file=sys.stderr)
            return None
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError) as exc:
        print(f"    score failed: {exc}", file=sys.stderr)
        return None


def get_bridge_ip() -> str:
    result = _docker("network", "inspect", "bridge",
                     "-f", "{{(index .IPAM.Config 0).Gateway}}",
                     capture=True, check=False)
    ip = result.stdout.strip() if result.returncode == 0 else ""
    return ip or "172.17.0.1"


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def read_settings() -> str:
    return SEARXNG_SETTINGS.read_text()


def write_settings(content: str) -> None:
    SEARXNG_SETTINGS.write_text(content)


def settings_sha(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


# Constrained-field patterns — same logic as score.py.
_CONSTRAINED_PATTERNS = [
    re.compile(r"secret_key\s*:\s*(.+)"),
    re.compile(r"base_url\s*:\s*(.+)"),
    re.compile(r"all://\s*:\s*(.+)"),  # proxy URL value
]


def validate_candidate(baseline: str, candidate: str) -> tuple[bool, str]:
    for pat in _CONSTRAINED_PATTERNS:
        if pat.findall(baseline) != pat.findall(candidate):
            field = pat.pattern.split(r"\s")[0].replace("\\", "")
            return False, f"constrained field changed: {field}"
    return True, ""


# ---------------------------------------------------------------------------
# Iteration record
# ---------------------------------------------------------------------------

def append_iteration(record: dict) -> None:
    with ITERATIONS_FILE.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_iterations() -> list[dict]:
    if not ITERATIONS_FILE.exists():
        return []
    records = []
    for line in ITERATIONS_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


# ---------------------------------------------------------------------------
# LLM mutation picker
# ---------------------------------------------------------------------------

_MUTATION_SYSTEM = """\
You are a SearXNG-config tuning assistant. Your job is to pick the next \
settings.yml mutation to try, based on the iteration history so far.

Search space — vary ONE axis per iteration:
  engine_list: add or remove engines from use_default_settings.engines.keep_only
               Valid engine names: google, bing, duckduckgo, brave, qwant, wikipedia,
               arxiv, "google scholar", "semantic scholar", pubmed, crossref, peertube
  engine_weights: add per-engine weight floats (boost: arxiv, "semantic scholar", pubmed;
                  deweight: brave, qwant for science queries)
  plugins: add tracker_url_remover or oa_doi_rewrite to enabled_plugins;
           remove problematic plugins from disabled_plugins
  hostnames: add regex patterns to hostnames.low_priority (SEO sites) or
             hostnames.high_priority (authoritative sources)
  safe_search: set search.safe_search to 0, 1, or 2
  engine_categories: reassign engines to categories list (science vs general)
  engine_timeout: bump timeout_limit for slow-but-quality engines (semantic scholar, pubmed)

Constrained fields — NEVER change:
  server.secret_key, server.base_url, outgoing.proxies

Score = (science + editorial-considered) - (seo-fluff + listicle + marketing) across
3 fixture queries (q3 medical, creatine consumer, finance-team business).
Higher is better.

Rules:
  - One axis per iteration
  - If last 3 iterations on same axis didn't help, try a different axis
  - Prefer axes not yet tried before narrowing down within an axis
  - Revert any change that dropped score; keep what improved or held steady
"""

_MUTATION_PROMPT_TEMPLATE = """\
Current settings.yml:
```yaml
{current_settings}
```

Iteration history (last {n_shown} of {n_total} iterations):
```jsonl
{history_excerpt}
```

Running best score: {best_score} (iteration {best_idx})
Iterations so far: {n_total}
Elapsed minutes: {elapsed_min:.1f}

Pick the next mutation. Output a JSON object on ONE line with these fields:
  axis_touched: one of engine_list|engine_weights|plugins|hostnames|safe_search|engine_categories|engine_timeout
  mutation_summary: one short phrase (e.g. "add pubmed to engine list")
  rationale: one sentence why this change should help
  new_settings_yaml: the complete new settings.yml content (must preserve constrained fields)
"""


def pick_mutation(current_settings: str, iterations: list[dict], elapsed_min: float, omlx_base_url: str, omlx_api_key: str, notes_model: str) -> dict | None:
    """Call the LLM to pick the next mutation. Returns dict with new_settings_yaml + metadata."""
    import urllib.request
    import urllib.error

    n_total = len(iterations)
    n_shown = min(10, n_total)
    history_excerpt = "\n".join(
        json.dumps(r, ensure_ascii=False)
        for r in iterations[-n_shown:]
    )
    best_score = max((r.get("score", -999) for r in iterations), default=0)
    best_idx = next(
        (i for i, r in enumerate(iterations) if r.get("score") == best_score),
        0,
    )

    prompt = _MUTATION_PROMPT_TEMPLATE.format(
        current_settings=current_settings[:4000],  # trim if huge
        n_shown=n_shown,
        n_total=n_total,
        history_excerpt=history_excerpt,
        best_score=best_score,
        best_idx=best_idx,
        elapsed_min=elapsed_min,
    )

    headers = {"Content-Type": "application/json"}
    if omlx_api_key:
        headers["Authorization"] = f"Bearer {omlx_api_key}"

    payload = json.dumps({
        "model": notes_model,
        "messages": [
            {"role": "system", "content": _MUTATION_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.3,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{omlx_base_url}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        text = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"    LLM call failed: {exc}", file=sys.stderr)
        return None

    # Extract the JSON object from the response.
    # LLMs sometimes wrap it in ```json ... ``` fences.
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())

    # Find the first { ... } block.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        print(f"    LLM did not return a JSON object:\n{text[:300]}", file=sys.stderr)
        return None

    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        print(f"    JSON parse error: {exc}\n{text[:300]}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------

def should_stop(
    iterations: list[dict],
    start_time: float,
    max_minutes: float,
) -> tuple[bool, str]:
    elapsed_min = (time.monotonic() - start_time) / 60

    if elapsed_min >= max_minutes:
        return True, "wall_clock"

    if len(iterations) < 2:
        return False, ""

    scores = [r.get("score", -999) for r in iterations]
    best = max(scores)

    # 5 consecutive no-improvement
    if len(iterations) >= NO_IMPROVEMENT_LIMIT:
        last_n = scores[-NO_IMPROVEMENT_LIMIT:]
        if all(s <= scores[-(NO_IMPROVEMENT_LIMIT + 1)] for s in last_n):
            return True, "no_improvement"

    # 10 iterations within 5% of rolling best
    if len(iterations) >= NEAR_BEST_LIMIT:
        last_n = scores[-NEAR_BEST_LIMIT:]
        threshold = best * (1 - NEAR_BEST_PCT) if best > 0 else best - abs(best) * NEAR_BEST_PCT
        if all(s >= threshold for s in last_n):
            return True, "converged"

    return False, ""


# ---------------------------------------------------------------------------
# RESULTS.md writer
# ---------------------------------------------------------------------------

def write_results_md(out_dir: pathlib.Path, iterations: list[dict], stop_reason: str, baseline_score: int) -> None:
    path = out_dir / "RESULTS.md"

    lines = [
        "# SearXNG-config tuning results",
        "",
        f"Stop reason: **{stop_reason}**  ",
        f"Total iterations: {len(iterations)}  ",
        f"Baseline score: {baseline_score}  ",
    ]

    if iterations:
        scores = [r.get("score", 0) for r in iterations]
        best = max(scores)
        best_idx = scores.index(best)
        lines += [
            f"Best score: {best} (iteration {best_idx})  ",
            "",
            "## Score trajectory",
            "",
            "| iter | axis | mutation | score | kept? |",
            "|------|------|----------|-------|-------|",
        ]
        for i, r in enumerate(iterations):
            lines.append(
                f"| {i} | {r.get('axis_touched','')} | {r.get('mutation_summary','')} "
                f"| {r.get('score','')} | {r.get('kept_or_reverted','')} |"
            )

        # Per-axis marginals
        from collections import defaultdict
        axis_scores: dict[str, list[int]] = defaultdict(list)
        for r in iterations:
            axis = r.get("axis_touched", "unknown")
            axis_scores[axis].append(r.get("score", 0))

        lines += [
            "",
            "## Per-axis marginal effect (mean score when this axis was touched)",
            "",
            "| axis | mean score | n |",
            "|------|-----------|---|",
        ]
        for axis, sc in sorted(axis_scores.items(), key=lambda x: -sum(x[1]) / len(x[1])):
            mean = sum(sc) / len(sc)
            lines.append(f"| {axis} | {mean:.1f} | {len(sc)} |")

        # Kept knobs
        kept = [r for r in iterations if r.get("kept_or_reverted") == "kept"]
        if kept:
            lines += [
                "",
                "## Kept mutations",
                "",
            ]
            for r in kept:
                lines.append(f"- **{r['axis_touched']}**: {r['mutation_summary']} — {r['rationale']}")

    lines += [
        "",
        "## Downstream-orchestration follow-ups",
        "",
        "_Derive from specific iteration rows after the run completes._",
        "",
        "## Notes",
        "",
        "_Surprising results and LLM-judge re-labelings recorded here after the run._",
    ]

    path.write_text("\n".join(lines) + "\n")
    print(f"  wrote {path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SearXNG-config LLM-driven tuning loop (host-side)")
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES)
    parser.add_argument("--out-dir", default=str(SCRIPT_DIR), help="directory for iterations.jsonl and RESULTS.md")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Override ITERATIONS_FILE path with out_dir.
    global ITERATIONS_FILE
    ITERATIONS_FILE = out_dir / "iterations.jsonl"

    omlx_base_url = os.environ.get("OMLX_BASE_URL") or "http://localhost:8000/v1"
    omlx_api_key = os.environ.get("OMLX_API_KEY") or ""
    notes_model = os.environ.get("NOTES_MODEL") or "gemma-4-26b-a4b-it-8bit"

    if not omlx_api_key:
        print("warning: OMLX_API_KEY is not set; LLM calls may fail", file=sys.stderr)

    switch_docker_context()
    bridge_ip = get_bridge_ip()
    print(f"Bridge IP: {bridge_ip}", file=sys.stderr)

    if not SEARXNG_SETTINGS.exists():
        print(f"error: {SEARXNG_SETTINGS} does not exist. Run ./research.py --backend=omlx first.", file=sys.stderr)
        sys.exit(1)

    start_time = time.monotonic()
    baseline_settings = read_settings()
    current_settings = baseline_settings

    # Score baseline (iteration 0).
    print("Scoring baseline (iteration 0) ...", file=sys.stderr)
    # Copy settings to a path accessible by the container.
    settings_copy = out_dir / "current_settings.yml"
    settings_copy.write_text(current_settings)

    baseline_result = run_score(out_dir, settings_copy, bridge_ip)
    baseline_score = baseline_result["score"] if baseline_result else 0

    ts_now = datetime.datetime.utcnow().isoformat() + "Z"
    baseline_record = {
        "ts": ts_now,
        "settings_sha": settings_sha(current_settings),
        "axis_touched": "baseline",
        "mutation_summary": "baseline (Phase 5 defaults)",
        "rationale": "reference point",
        "score": baseline_score,
        "label_dist_per_query": (baseline_result or {}).get("label_dist_per_query", {}),
        "kept_or_reverted": "kept",
    }
    append_iteration(baseline_record)
    print(f"Baseline score: {baseline_score}", file=sys.stderr)

    stop_reason = "max_iterations"

    while True:
        iterations = read_iterations()
        stop, stop_reason = should_stop(iterations, start_time, args.max_minutes)
        if stop:
            print(f"Stop condition: {stop_reason}", file=sys.stderr)
            break

        elapsed_min = (time.monotonic() - start_time) / 60
        print(f"\nIteration {len(iterations)} (elapsed: {elapsed_min:.1f} min) ...", file=sys.stderr)

        mutation = pick_mutation(
            current_settings, iterations, elapsed_min,
            omlx_base_url, omlx_api_key, notes_model,
        )
        if mutation is None:
            print("  LLM returned no mutation; skipping iteration", file=sys.stderr)
            time.sleep(5)
            continue

        candidate_yaml = mutation.get("new_settings_yaml", "")
        if not candidate_yaml:
            print("  mutation missing new_settings_yaml", file=sys.stderr)
            continue

        ok, reason = validate_candidate(baseline_settings, candidate_yaml)
        if not ok:
            print(f"  REJECTED: {reason}", file=sys.stderr)
            record = {
                "ts": datetime.datetime.utcnow().isoformat() + "Z",
                "settings_sha": settings_sha(candidate_yaml),
                "axis_touched": mutation.get("axis_touched", "unknown"),
                "mutation_summary": mutation.get("mutation_summary", ""),
                "rationale": mutation.get("rationale", ""),
                "score": None,
                "label_dist_per_query": {},
                "kept_or_reverted": "reverted",
                "rejection_reason": reason,
            }
            append_iteration(record)
            continue

        # Apply mutation.
        write_settings(candidate_yaml)
        settings_copy.write_text(candidate_yaml)

        restart_searxng()
        ready = poll_searxng_ready(bridge_ip)
        if not ready:
            print("  SearXNG did not become ready in time; reverting", file=sys.stderr)
            write_settings(current_settings)
            settings_copy.write_text(current_settings)
            restart_searxng()
            poll_searxng_ready(bridge_ip)
            record = {
                "ts": datetime.datetime.utcnow().isoformat() + "Z",
                "settings_sha": settings_sha(candidate_yaml),
                "axis_touched": mutation.get("axis_touched", "unknown"),
                "mutation_summary": mutation.get("mutation_summary", ""),
                "rationale": mutation.get("rationale", ""),
                "score": None,
                "label_dist_per_query": {},
                "kept_or_reverted": "reverted",
                "rejection_reason": "searxng_startup_timeout",
            }
            append_iteration(record)
            continue

        # Score.
        score_result = run_score(out_dir, settings_copy, bridge_ip)
        if score_result is None:
            print("  scoring failed; reverting", file=sys.stderr)
            write_settings(current_settings)
            settings_copy.write_text(current_settings)
            restart_searxng()
            poll_searxng_ready(bridge_ip)
            record = {
                "ts": datetime.datetime.utcnow().isoformat() + "Z",
                "settings_sha": settings_sha(candidate_yaml),
                "axis_touched": mutation.get("axis_touched", "unknown"),
                "mutation_summary": mutation.get("mutation_summary", ""),
                "rationale": mutation.get("rationale", ""),
                "score": None,
                "label_dist_per_query": {},
                "kept_or_reverted": "reverted",
                "rejection_reason": "scoring_failed",
            }
            append_iteration(record)
            continue

        new_score = score_result["score"]
        prev_best = max(
            (r.get("score") or -999 for r in read_iterations()),
            default=-999,
        )

        kept = new_score >= prev_best
        if not kept:
            # Revert.
            write_settings(current_settings)
            settings_copy.write_text(current_settings)
            restart_searxng()
            poll_searxng_ready(bridge_ip)
        else:
            current_settings = candidate_yaml
            print(f"  KEPT (score {new_score} >= prev best {prev_best})", file=sys.stderr)

        record = {
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
            "settings_sha": settings_sha(candidate_yaml),
            "axis_touched": mutation.get("axis_touched", "unknown"),
            "mutation_summary": mutation.get("mutation_summary", ""),
            "rationale": mutation.get("rationale", ""),
            "score": new_score,
            "label_dist_per_query": score_result.get("label_dist_per_query", {}),
            "kept_or_reverted": "kept" if kept else "reverted",
        }
        append_iteration(record)
        print(f"  score={new_score} {'KEPT' if kept else 'reverted'}", file=sys.stderr)

    # Mark final record with stop_reason.
    iterations = read_iterations()
    if iterations:
        last = iterations[-1]
        last["stop_reason"] = stop_reason
        # Rewrite last line.
        lines = ITERATIONS_FILE.read_text().rstrip().splitlines()
        lines[-1] = json.dumps(last, ensure_ascii=False)
        ITERATIONS_FILE.write_text("\n".join(lines) + "\n")

    write_results_md(out_dir, iterations, stop_reason, baseline_score)

    # Write winning settings back (already written; confirm it's in place).
    print(f"\nFinal settings at: {SEARXNG_SETTINGS}", file=sys.stderr)
    print(f"Iterations: {len(iterations)}, best score: {max((r.get('score') or -999 for r in iterations), default=0)}", file=sys.stderr)
    print("Run complete. Update render_searxng_settings() in research.py with the winning settings.", file=sys.stderr)


if __name__ == "__main__":
    main()
