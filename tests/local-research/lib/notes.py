"""
Per-source note generation via NOTES_MODEL.
Sequential per Unknowns #3 (omlx may serialise concurrent calls).
"""
from lib.config import NOTES_MODEL
from lib import omlx

# Per Unknowns #9: per-round digest ≤5k tokens; per-source note input budget
# set conservatively at ~4k tokens ≈ 16 000 chars.
_SOURCE_CHAR_LIMIT = 16_000

_PROMPT = """\
You are a research assistant extracting key facts from a source document.

Query: {query}
Source URL: {url}
Source title: {title}

Source text (may be truncated):
---
{source_text}
---

Write 4–8 concise bullet points covering:
- The central claim or finding of this source
- Specific facts, statistics, or quotes relevant to the query
- Caveats, limitations, or contradictions
If the source is not relevant to the query, write a single line: "Irrelevant to query."

Output only the bullet points, one per line starting with "-"."""


def note_for_source(query: str, source_text: str, meta: dict) -> str:
    """
    Generate a note for a single source.
    Truncates source_text to _SOURCE_CHAR_LIMIT before sending.
    Returns a string of bullet points.
    """
    truncated = source_text[:_SOURCE_CHAR_LIMIT]
    prompt = _PROMPT.format(
        query=query,
        url=meta.get("url", ""),
        title=meta.get("title", ""),
        source_text=truncated,
    )
    return omlx.chat(NOTES_MODEL, [{"role": "user", "content": prompt}])
