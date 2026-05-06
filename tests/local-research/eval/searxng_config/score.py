"""
SearXNG-config scoring module.

Provides:
  - compute_score(labeled_results) -> int
  - label_distribution(labeled_results) -> dict[str, int]
  - regex_label(url, title="") -> str | None
  - validate_candidate(baseline_yaml, candidate_yaml) -> (bool, str)
  - missing_record_fields(record) -> set[str]
  - validate_record(record, is_final=False) -> list[str]
  - settings_sha(content) -> str

Also runnable as a CLI:
  python -m eval.searxng_config.score [--queries q3,creatine,finance-team]
  Outputs JSON to stdout: {score, label_dist_per_query, settings_sha, per_query}

The CLI runs inside the research-runner container (via bootstrap.sh --score-searxng).
Pure-library functions have no side effects and are safe to import in unit tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import pathlib

# ---------------------------------------------------------------------------
# Score helpers (no I/O; importable in unit tests)
# ---------------------------------------------------------------------------

_POSITIVE_LABELS = {"science", "editorial-considered"}
_NEGATIVE_LABELS = {"seo-fluff", "listicle", "marketing"}
_REQUIRED_RECORD_FIELDS = {
    "ts", "settings_sha", "axis_touched", "mutation_summary",
    "rationale", "score", "label_dist_per_query", "kept_or_reverted",
}
_VALID_KEPT_VALUES = {"kept", "reverted"}


def compute_score(labeled_results: list[dict]) -> int:
    """(science + editorial-considered) - (seo-fluff + listicle + marketing)."""
    pos = sum(1 for r in labeled_results if r.get("label") in _POSITIVE_LABELS)
    neg = sum(1 for r in labeled_results if r.get("label") in _NEGATIVE_LABELS)
    return pos - neg


def label_distribution(labeled_results: list[dict]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in labeled_results:
        lbl = r.get("label", "")
        if lbl:
            dist[lbl] = dist.get(lbl, 0) + 1
    return dist


def settings_sha(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Regex auto-labeler
# ---------------------------------------------------------------------------

# Science: peer-reviewed / preprint / clinical
_SCIENCE_HOSTS = re.compile(
    r"^(.*\.)?(arxiv\.org|pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov"
    r"|biorxiv\.org|medrxiv\.org|plos\.org|nature\.com|science\.org"
    r"|sciencedirect\.com|thelancet\.com|bmj\.com|nejm\.org|jamanetwork\.com"
    r"|cochranelibrary\.com|semanticscholar\.org|crossref\.org"
    r"|europepmc\.org|journals\.plos\.org|elifesciences\.org"
    r"|oup\.com|karger\.com|springer\.com|wiley\.com|tandfonline\.com)"
)

# Editorial / considered
_EDITORIAL_HOSTS = re.compile(
    r"^(.*\.)?(\.edu|\.gov)$"
)
_EDITORIAL_HOST_EXACT = re.compile(
    r"\.(edu|gov)(/|$)"
)
_SUBSTACK = re.compile(r"\.substack\.com/")

# Other: forums, social, reference
_OTHER_HOSTS = re.compile(
    r"(.*\.)?wikipedia\.org$|"
    r"^(www\.)?(reddit\.com|twitter\.com|x\.com"
    r"|youtube\.com|facebook\.com|linkedin\.com|quora\.com"
    r"|stackexchange\.com|stackoverflow\.com|news\.ycombinator\.com"
    r"|lobste\.rs|slashdot\.org|hackernews\.com)"
)

# SEO patterns (URL substrings)
_LISTICLE_PATH = re.compile(r"/(best-|top-\d+|top\d+|best-\d+|guide-to|how-to-|versus-|vs-)")
_MARKETING_PARAMS = re.compile(r"[?&](utm_|gclid=|fbclid=|srsltid=)")


def regex_label(url: str, title: str = "") -> str | None:
    """Return a label string or None if no regex rule matches."""
    url_lower = url.lower()
    host_match = re.match(r"https?://([^/]+)", url_lower)
    host = host_match.group(1) if host_match else ""

    if _SCIENCE_HOSTS.match(host):
        return "science"
    if _EDITORIAL_HOST_EXACT.search(url_lower):
        return "editorial-considered"
    if _SUBSTACK.search(url_lower):
        return "editorial-considered"
    if _OTHER_HOSTS.match(host):
        return "other"
    if _LISTICLE_PATH.search(url_lower):
        return "listicle"
    if _MARKETING_PARAMS.search(url_lower):
        return "marketing"
    return None


# ---------------------------------------------------------------------------
# Constrained-field validator
# ---------------------------------------------------------------------------

# These strings must not change value between baseline and candidate.
_CONSTRAINED_PATTERNS = [
    re.compile(r"secret_key\s*:\s*(.+)"),
    re.compile(r"base_url\s*:\s*(.+)"),
    re.compile(r"all://\s*:\s*(.+)"),  # proxy URL value
]


def validate_candidate(baseline_yaml: str, candidate_yaml: str) -> tuple[bool, str]:
    """Return (True, "") if candidate is safe to apply, else (False, reason)."""
    for pat in _CONSTRAINED_PATTERNS:
        base_matches = pat.findall(baseline_yaml)
        cand_matches = pat.findall(candidate_yaml)
        if base_matches != cand_matches:
            field = pat.pattern.split(r"\s")[0].replace("\\", "")
            return False, f"constrained field changed: {field}"
    return True, ""


# ---------------------------------------------------------------------------
# Iteration record validation
# ---------------------------------------------------------------------------

def missing_record_fields(record: dict) -> set[str]:
    return _REQUIRED_RECORD_FIELDS - set(record.keys())


def validate_record(record: dict, is_final: bool = False) -> list[str]:
    errors: list[str] = []
    missing = missing_record_fields(record)
    if missing:
        errors.append(f"missing fields: {missing}")
    kept = record.get("kept_or_reverted", "")
    if kept not in _VALID_KEPT_VALUES:
        errors.append(f"kept_or_reverted must be one of {_VALID_KEPT_VALUES}, got {kept!r}")
    if is_final and "stop_reason" not in record:
        errors.append("final row must have stop_reason field")
    return errors


# ---------------------------------------------------------------------------
# Fixture queries (same as Phase 5; subset used for Phase 6)
# ---------------------------------------------------------------------------

PHASE6_QUERIES = {
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


# ---------------------------------------------------------------------------
# LLM labeler (used when regex_label returns None)
# ---------------------------------------------------------------------------

def llm_label(url: str, title: str, omlx_base_url: str, omlx_api_key: str, notes_model: str) -> str:
    """Call NOTES_MODEL with a fixed labeling prompt. Returns one of the 6 label strings."""
    import requests

    prompt = (
        "Label this search result with EXACTLY ONE of: "
        "science, editorial-considered, seo-fluff, listicle, marketing, other\n\n"
        "Definitions:\n"
        "  science: peer-reviewed paper, preprint, clinical guideline, systematic review, "
        "or content from a university/govt research page\n"
        "  editorial-considered: long-form bylined editorial, Substack analysis, policy brief, trade journal\n"
        "  seo-fluff: generic informational page with no original analysis; exists to rank for search traffic\n"
        "  listicle: 'Top 10...', 'Best X...', numbered list without substantive entries\n"
        "  marketing: product/service promotion, affiliate content, brand blog with sales agenda\n"
        "  other: Wikipedia, forum, social media, video, directory\n\n"
        f"URL: {url}\n"
        f"Title: {title}\n\n"
        "Reply with only the label word, nothing else."
    )

    headers = {"Content-Type": "application/json"}
    if omlx_api_key:
        headers["Authorization"] = f"Bearer {omlx_api_key}"

    resp = requests.post(
        f"{omlx_base_url}/chat/completions",
        headers=headers,
        json={
            "model": notes_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "temperature": 0.0,
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip().lower()
    # Tolerate "science." → "science"
    text = text.rstrip(".").strip()
    valid = {"science", "editorial-considered", "seo-fluff", "listicle", "marketing", "other"}
    return text if text in valid else "other"


def auto_label(result: dict, omlx_base_url: str, omlx_api_key: str, notes_model: str) -> str:
    """Regex first, then LLM fallback."""
    url = result.get("url", "")
    title = result.get("title", "")
    lbl = regex_label(url, title)
    if lbl is not None:
        return lbl
    return llm_label(url, title, omlx_base_url, omlx_api_key, notes_model)


# ---------------------------------------------------------------------------
# CLI entry point (runs inside container)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Score current SearXNG config against fixture queries")
    parser.add_argument(
        "--queries",
        default=",".join(PHASE6_QUERIES.keys()),
        help="comma-separated query slugs (default: q3,creatine,finance-team)",
    )
    parser.add_argument(
        "--settings-file",
        default=None,
        help="path to the settings.yml being evaluated (for SHA only; not applied here)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=15,
        help="top-K results to score per query (default: 15)",
    )
    args = parser.parse_args()

    # Resolve env vars (same pattern as lib/config.py).
    omlx_base_url = os.environ.get("OMLX_BASE_URL") or "http://host.docker.internal:8000/v1"
    omlx_api_key = os.environ.get("OMLX_API_KEY") or ""
    notes_model = os.environ.get("NOTES_MODEL") or "gemma-4-26b-a4b-it-8bit"

    query_slugs = [q.strip() for q in args.queries.split(",") if q.strip()]
    unknown = [q for q in query_slugs if q not in PHASE6_QUERIES]
    if unknown:
        print(f"Unknown query slugs: {unknown}. Available: {list(PHASE6_QUERIES)}", file=sys.stderr)
        sys.exit(1)

    # SHA of current settings file (for record keeping).
    sha = ""
    if args.settings_file and pathlib.Path(args.settings_file).exists():
        sha = settings_sha(pathlib.Path(args.settings_file).read_text())

    # Import pipeline inside main so tests can import this module without docker.
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
    from lib.pipeline import gather_sources  # noqa: PLC0415

    overall_score = 0
    per_query: dict[str, dict] = {}

    for slug in query_slugs:
        query = PHASE6_QUERIES[slug]
        print(f"  scoring {slug} ...", file=sys.stderr)
        t0 = time.monotonic()
        try:
            result = gather_sources(query, n_expansions=2, n_per_query=20, top_k=args.top_k)
            ranked = result["ranked"]
        except Exception as exc:
            print(f"    gather_sources failed: {exc}", file=sys.stderr)
            per_query[slug] = {"error": str(exc), "score": 0, "label_dist": {}, "labeled": []}
            continue

        labeled = []
        for r in ranked:
            lbl = auto_label(r, omlx_base_url, omlx_api_key, notes_model)
            labeled.append({**r, "label": lbl})

        q_score = compute_score(labeled)
        q_dist = label_distribution(labeled)
        overall_score += q_score

        per_query[slug] = {
            "score": q_score,
            "label_dist": q_dist,
            "labeled": [
                {
                    "rank": i + 1,
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "engine": r.get("engine", ""),
                    "rerank_score": round(r.get("rerank_score", 0.0), 4),
                    "label": r.get("label", ""),
                }
                for i, r in enumerate(labeled)
            ],
            "elapsed_s": round(time.monotonic() - t0, 2),
        }

    output = {
        "score": overall_score,
        "settings_sha": sha,
        "label_dist_per_query": {slug: per_query[slug].get("label_dist", {}) for slug in query_slugs},
        "per_query": per_query,
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
