"""
Query expansion via the EXPAND_MODEL.
Returns [original_query, *expansions] so the seed is always searched.
"""
from lib.config import EXPAND_MODEL
from lib import omlx

_PROMPT = """\
Generate {n} alternative phrasings of the following research query. \
Cover different angles: technical vs. lay terminology, narrow vs. broad scope, \
and alternative domain vocabulary. Output exactly one phrasing per line with no \
numbering, bullets, or extra text.

Query: {query}"""


def expand(query: str, n: int = 4) -> list[str]:
    prompt = _PROMPT.format(n=n, query=query)
    raw = omlx.chat(EXPAND_MODEL, [{"role": "user", "content": prompt}])
    expansions = [line.strip() for line in raw.splitlines() if line.strip()]
    return [query, *expansions]
