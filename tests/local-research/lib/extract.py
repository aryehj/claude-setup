"""
HTML → markdown extraction.
Primary: trafilatura.
Fallback: regex strip-tags + <title>, used when trafilatura returns None or <500 chars.
"""
import re

import trafilatura

_MIN_CHARS = 500


def _strip_tags(html: str) -> str:
    # Remove <script> and <style> blocks entirely
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def extract(html: str, url: str) -> str:
    """
    Extract readable markdown from html.
    Falls back to a regex strip-tags approach when trafilatura yields nothing useful.
    """
    result = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=False,
        include_comments=False,
        url=url,
    )
    if result and len(result) >= _MIN_CHARS:
        return result

    # Fallback: strip tags, prepend title
    text = _strip_tags(html)
    title = _title(html)
    if title:
        return f"# {title}\n\n{text}"
    return text or "[extraction failed]"
