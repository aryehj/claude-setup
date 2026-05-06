#!/usr/bin/env python3
"""
Phase 6 iteration harness — agent-as-loop SearXNG tuning.

Throwaway. Runs inside the claude-agent container after start-agent.sh has
bind-mounted ~/.claude-agent/searxng/ at /host/searxng/ and exposed the docker
socket. The agent reads /host/searxng/settings.yml, edits it directly, then
calls this script with Bash to restart searxng, run the 3 fixture queries
against http://searxng:8080, and append a row to iterations.jsonl. The agent
then Edits the row in-place to add `rationale` and `kept_or_reverted`.

Stdlib-only: no pipeline, no rerank, no LLM judge. The agent IS the judge.

Usage:
    python3 tests/local-research/eval/searxng_config/iterate.py \\
        --restart --top-n 15 \\
        --axis-touched engine_list \\
        --mutation-summary "added pubmed engine"
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request


SETTINGS_PATH = pathlib.Path("/host/searxng/settings.yml")
SEARXNG_URL = "http://searxng:8080"
ITERATIONS_PATH = pathlib.Path(__file__).parent / "iterations.jsonl"

QUERIES = {
    "q3": (
        "A cyclist develops stubborn medial knee pain that comes on during long rides "
        "and lingers for days afterward. What are the most likely diagnoses, how do "
        "bike-fit and biomechanical factors contribute to each, and what clinical features "
        "would help distinguish between them?"
    ),
    "creatine": "is creatine safe to take long term",
    "finance-team": (
        "is it unusual for a 60-person software consulting company to have a 4-person "
        "finance team"
    ),
}


def settings_sha(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def restart_searxng(deadline_s: int = 60) -> None:
    subprocess.run(
        ["docker", "restart", "searxng"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + deadline_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"{SEARXNG_URL}/search?q=test&format=json",
                headers={"User-Agent": "phase6-iterate"},
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as exc:
            last_err = exc
        time.sleep(1)
    raise RuntimeError(f"searxng did not come up within {deadline_s}s (last error: {last_err})")


def search(query: str, top_n: int) -> list[dict]:
    url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
    req = urllib.request.Request(url, headers={"User-Agent": "phase6-iterate"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    out = []
    for x in (data.get("results") or [])[:top_n]:
        engines = x.get("engines") or ([x.get("engine")] if x.get("engine") else [])
        out.append({
            "url": x.get("url", ""),
            "title": x.get("title", ""),
            "content": (x.get("content") or "")[:400],
            "engines": engines,
        })
    return out


def next_iter(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    last = -1
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            last = max(last, int(json.loads(line).get("iter", -1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return last + 1


def main() -> None:
    p = argparse.ArgumentParser(description="Phase 6 iteration harness")
    p.add_argument("--restart", action="store_true", help="docker restart searxng before querying")
    p.add_argument("--top-n", type=int, default=15)
    p.add_argument("--axis-touched", default="", help="e.g. engine_list, weights, hostnames, plugins, search, timeout")
    p.add_argument("--mutation-summary", default="", help="one-line description of the change")
    p.add_argument("--queries", default=",".join(QUERIES), help="comma-separated subset of fixture slugs")
    args = p.parse_args()

    slugs = [s.strip() for s in args.queries.split(",") if s.strip()]
    unknown = [s for s in slugs if s not in QUERIES]
    if unknown:
        print(f"unknown query slugs: {unknown}; valid: {list(QUERIES)}", file=sys.stderr)
        sys.exit(2)

    settings_text = SETTINGS_PATH.read_text() if SETTINGS_PATH.exists() else ""
    sha = settings_sha(settings_text)

    if args.restart:
        print(f"restarting searxng (sha={sha}) ...", file=sys.stderr)
        restart_searxng()

    iter_n = next_iter(ITERATIONS_PATH)
    top_per: dict[str, list[dict]] = {}
    for slug in slugs:
        print(f"  query {slug} ...", file=sys.stderr)
        top_per[slug] = search(QUERIES[slug], args.top_n)

    row = {
        "iter": iter_n,
        "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "settings_sha": sha,
        "top_n": args.top_n,
        "axis_touched": args.axis_touched,
        "mutation_summary": args.mutation_summary,
        "rationale": "",
        "kept_or_reverted": "",
        "top_ranked_per_query": top_per,
    }

    ITERATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ITERATIONS_PATH.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "iter": iter_n,
        "settings_sha": sha,
        "queries": slugs,
        "result_counts": {s: len(top_per[s]) for s in slugs},
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
