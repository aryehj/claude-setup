"""
Gap-driven branch proposal: read accumulated digests, propose K follow-up queries.
"""
import re

from lib.config import NOTES_MODEL
from lib import omlx

_PROMPT = """\
You are a research strategist planning the next round of web research.

Original query: {seed_query}

Accumulated round digests:
---
{digests_block}
---

Based on the facts gathered so far, identify {k} follow-up search queries that target:
- Still-missing facts or unanswered sub-questions
- Unexplored peripheral domains or alternative framings
- Conflicting claims that need clarification

Output exactly {k} lines, each in the format:
  search query | one-sentence rationale

Do not include any other text, headers, or explanations."""


def propose_branches(
    seed_query: str,
    accumulated_digests: list[str],
    k: int = 4,
) -> list[dict]:
    """
    Call NOTES_MODEL to propose K follow-up queries targeting gaps in the
    accumulated digests.

    Returns a list of dicts: [{query: str, rationale: str}, ...]

    Round 1 (no digests yet) returns the seed query as the sole branch.
    Parser is tolerant: strips leading bullets/numbers, splits on first '|'.
    Lines without a '|' are skipped.
    """
    if not accumulated_digests:
        return [{"query": seed_query, "rationale": "initial seed query"}]

    digests_block = "\n\n---\n\n".join(
        f"[Round {i + 1}]\n{d}" for i, d in enumerate(accumulated_digests)
    )

    prompt = _PROMPT.format(
        seed_query=seed_query,
        digests_block=digests_block,
        k=k,
    )

    raw = omlx.chat(NOTES_MODEL, [{"role": "user", "content": prompt}])
    return _parse_branches(raw)


# Leading bullet/number patterns to strip before parsing.
_STRIP_PREFIX = re.compile(r"^[\-\*\d]+[\.\)]*\s*")


def _parse_branches(raw: str) -> list[dict]:
    results = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        line = _STRIP_PREFIX.sub("", line).strip()
        if "|" not in line:
            continue
        query, _, rationale = line.partition("|")
        query = query.strip()
        rationale = rationale.strip()
        if query:
            results.append({"query": query, "rationale": rationale})
    return results
