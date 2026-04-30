"""
URL-dedupe registry and round accumulator for the research orchestrator.
"""
import pathlib
import pickle
import re
import urllib.parse


# Tracking query parameters to strip before URL comparison.
_STRIP_PARAMS = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_term",
                           "utm_content", "utm_id", "srsltid", "fbclid", "gclid", "ref"})


def canonicalize_url(url: str) -> str:
    """
    Return a canonical form of url for deduplication:
    - lowercase scheme + host
    - strip tracking query params (utm_*, srsltid, fbclid, gclid, ref)
    - normalize percent-encoding (%27 → ')
    - drop fragment
    """
    parsed = urllib.parse.urlparse(url)

    # Lowercase scheme and host; preserve path case.
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Decode percent-encoded chars (normalise %27 → apostrophe etc.)
    path = urllib.parse.unquote(parsed.path)

    # Strip tracking params from query string.
    qs_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in qs_pairs if k not in _STRIP_PARAMS]
    query = urllib.parse.urlencode(filtered)

    # Drop fragment entirely.
    canonical = urllib.parse.urlunparse((scheme, netloc, path, parsed.params, query, ""))
    return canonical


class RoundState:
    """Accumulates source metas and tracks seen URLs across rounds."""

    def __init__(self) -> None:
        self.round_count: int = 0
        self.accumulated_sources: list[dict] = []
        self._seen_canonical: set[str] = set()
        self.digest_paths: list[pathlib.Path] = []
        self.branch_history: list[dict] = []

    @property
    def seen_urls(self) -> set[str]:
        """Canonical URLs seen across all rounds — pass to rerank's exclude_urls."""
        return set(self._seen_canonical)

    def add_source(self, meta: dict) -> bool:
        """
        Register a source meta dict.  Returns True if added, False if the URL
        was already seen (deduplicated).  Canonicalizes the URL before checking.
        """
        raw_url = meta.get("url", "")
        canonical = canonicalize_url(raw_url) if raw_url else ""
        if canonical and canonical in self._seen_canonical:
            return False
        if canonical:
            self._seen_canonical.add(canonical)
        self.accumulated_sources.append(meta)
        return True

    def increment_round(self) -> None:
        self.round_count += 1

    def add_digest_path(self, round_idx: int, path: pathlib.Path) -> None:
        self.digest_paths.append(path)

    def add_branch(self, round_idx: int, query: str, rationale: str = "") -> None:
        self.branch_history.append({
            "round_idx": round_idx,
            "query": query,
            "rationale": rationale,
        })

    def save(self, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: pathlib.Path) -> "RoundState":
        with open(path, "rb") as f:
            return pickle.load(f)
