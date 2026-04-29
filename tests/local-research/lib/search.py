"""
SearXNG client.
"""
import requests

from lib.config import SEARXNG_URL


def search(query: str, n: int = 20) -> list[dict]:
    """GET SearXNG and return the results array (up to n entries)."""
    resp = requests.get(
        f"{SEARXNG_URL}/search",
        params={"q": query, "format": "json", "pageno": 1},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])[:n]
