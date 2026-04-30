"""
SearXNG client.
"""
import requests

from lib.config import SEARXNG_URL


def search(
    query: str,
    n: int = 20,
    categories: str | None = None,
    engines: str | None = None,
    pages: int = 1,
) -> list[dict]:
    """GET SearXNG and return up to n results across pages pages.

    categories: SearXNG category string (e.g. "science") or None for default.
    engines: comma-separated engine list or None for default.
    pages: number of result pages to fetch; duplicates are dropped.
    """
    params: dict = {"q": query, "format": "json"}
    if categories is not None:
        params["categories"] = categories
    if engines is not None:
        params["engines"] = engines

    seen_urls: set[str] = set()
    results: list[dict] = []

    for pageno in range(1, pages + 1):
        params["pageno"] = pageno
        resp = requests.get(f"{SEARXNG_URL}/search", params=params, timeout=30)
        resp.raise_for_status()
        for r in resp.json().get("results", []):
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(r)
                if len(results) >= n:
                    return results

    return results
