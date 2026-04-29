"""
Embedding-based rerank against the omlx embedder.
"""
import math

from lib.config import EMBED_MODEL
from lib import omlx


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed(texts: list[str]) -> list[list[float]]:
    """Return embedding vectors for a list of texts via omlx."""
    return omlx.embed(EMBED_MODEL, texts)


def rerank(
    query: str,
    results: list[dict],
    top_k: int = 15,
    exclude_urls: set[str] | None = None,
) -> list[dict]:
    """
    Cosine-rank results against the query embedding.

    Dedupes by URL before scoring.  Drops any URL in exclude_urls.
    Adds rerank_score to each returned dict; original engine and score are preserved.
    """
    exclude_urls = exclude_urls or set()

    # Dedupe by URL, preserving first occurrence.
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        url = r.get("url", "")
        if url not in seen and url not in exclude_urls:
            seen.add(url)
            unique.append(r)

    if not unique:
        return []

    texts = [f"{r.get('title', '')}\n{r.get('content', '')}" for r in unique]
    all_texts = [query] + texts
    vecs = embed(all_texts)
    query_vec = vecs[0]
    result_vecs = vecs[1:]

    scored = []
    for r, vec in zip(unique, result_vecs):
        score = _cosine(query_vec, vec)
        entry = dict(r)
        entry["rerank_score"] = score
        scored.append(entry)

    scored.sort(key=lambda x: x["rerank_score"], reverse=True)
    return scored[:top_k]
