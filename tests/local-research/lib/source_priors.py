"""
Domain-quality priors applied to raw SearXNG results before rerank.

apply_priors(results) returns a new list of result dicts, each with a
prior_adj float field.  The reranker adds prior_adj to cosine similarity
so boosted domains rank higher and penalized domains rank lower.

Built-in lists can be overridden by writing custom files and pointing
SOURCE_PRIORS_BOOST_FILE / SOURCE_PRIORS_PENALIZE_FILE env vars at them.
Each file should contain one entry per line (domain suffix or URL substring).
"""
import os
import re
import urllib.parse


# ---------------------------------------------------------------------------
# Built-in domain/pattern lists
# ---------------------------------------------------------------------------

_BOOST_DOMAINS: list[str] = [
    ".edu",
    ".gov",
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "nih.gov",
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "thelancet.com",
    "bmj.com",
    "nejm.org",
    "jamanetwork.com",
    "ncbi.nlm.nih.gov",
    "cochranelibrary.com",
    "semanticscholar.org",
    "scholar.google.com",
    "researchgate.net",
    "jstor.org",
    "plos.org",
    "biorxiv.org",
    "medrxiv.org",
    "physio-pedia.com",
    "physiopedia.com",
    ".substack.com",        # named long-form editorial
]

_PENALIZE_PATTERNS: list[str] = [
    "/best-",
    "/top-10-",
    "/top-5-",
    "/top-15-",
    "/top-20-",
    "/top-n-",
    "/guide-to-",
    "/how-to-",
    "/vs-",
    "/the-best-",
    "/ultimate-guide",
    "/complete-guide",
    "?utm_",
    "&utm_",
]

_BOOST_ADJ = 0.25
_PENALIZE_ADJ = -0.20


def _load_lines(path: str) -> list[str]:
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def _get_boost_domains() -> list[str]:
    path = os.environ.get("SOURCE_PRIORS_BOOST_FILE") or ""
    if path:
        return _load_lines(path)
    return _BOOST_DOMAINS


def _get_penalize_patterns() -> list[str]:
    path = os.environ.get("SOURCE_PRIORS_PENALIZE_FILE") or ""
    if path:
        return _load_lines(path)
    return _PENALIZE_PATTERNS


def _prior_adj_for(url: str) -> float:
    if not url:
        return 0.0

    parsed = urllib.parse.urlparse(url.lower())
    host = parsed.netloc
    path_and_query = parsed.path + ("?" + parsed.query if parsed.query else "")

    boost_domains = _get_boost_domains()
    penalize_patterns = _get_penalize_patterns()

    # Boost: host ends with a boost domain (suffix match handles subdomains).
    for domain in boost_domains:
        d = domain.lower()
        if host == d.lstrip(".") or host.endswith(d if d.startswith(".") else "." + d):
            return _BOOST_ADJ

    # Penalize: URL path/query contains a penalized pattern.
    url_lower = url.lower()
    for pattern in penalize_patterns:
        if pattern in url_lower:
            return _PENALIZE_ADJ

    return 0.0


def apply_priors(results: list[dict]) -> list[dict]:
    """Return a new list of result dicts, each with prior_adj added."""
    out = []
    for r in results:
        entry = dict(r)
        entry["prior_adj"] = _prior_adj_for(r.get("url", ""))
        out.append(entry)
    return out
