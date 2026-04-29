"""
Per-round search + rerank pipeline.
"""
import time

from lib import expand as _expand_mod
from lib import search as _search_mod
from lib import rerank as _rerank_mod


def gather_sources(
    query: str,
    exclude_urls: set[str] | None = None,
    n_expansions: int = 4,
    n_per_query: int = 20,
    top_k: int = 15,
) -> dict:
    """
    expand → search-each → flatten → dedupe → rerank

    Returns:
        {
            query: str,
            expansions: list[str],   # includes original at index 0
            raw_results: list[dict], # deduped union before rerank
            ranked: list[dict],      # top_k after rerank
            timings: dict,
        }
    """
    exclude_urls = exclude_urls or set()
    t0 = time.monotonic()

    expansions = _expand_mod.expand(query, n=n_expansions)
    t_expand = time.monotonic()

    # Search each expansion, flatten, dedupe by URL.
    seen_urls: set[str] = set(exclude_urls)
    raw: list[dict] = []
    for q in expansions:
        for r in _search_mod.search(q, n=n_per_query):
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                raw.append(r)
    t_search = time.monotonic()

    ranked = _rerank_mod.rerank(query, raw, top_k=top_k, exclude_urls=exclude_urls)
    t_rerank = time.monotonic()

    return {
        "query": query,
        "expansions": expansions,
        "raw_results": raw,
        "ranked": ranked,
        "timings": {
            "expand_s": round(t_expand - t0, 2),
            "search_s": round(t_search - t_expand, 2),
            "rerank_s": round(t_rerank - t_search, 2),
            "total_s": round(t_rerank - t0, 2),
        },
    }
