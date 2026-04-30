"""
Per-round digest: synthesise what was learned, what domains were touched,
and what remains unanswered. Written to rounds/<n>/digest.md.
"""
import pathlib

from lib.config import NOTES_MODEL
from lib import omlx

_PROMPT = """\
You are a research analyst summarising one round of web research.

Original query: {seed_query}
Round: {round_idx}

Sources retrieved this round ({source_count} total):
{source_list}

Write an 800–1200 word digest covering:
1. What was learned: key facts, claims, and findings from the sources
2. Which subdomains or angles were covered (medical, biomechanical, training, etc.)
3. What remains unanswered or needs further investigation

Use [r.N] to cite individual sources by their index in the list above.
Write in clear, analytical prose. Do not use bullet lists for the main body."""


def digest_round(
    round_idx: int,
    source_metas: list[dict],
    round_dir: pathlib.Path,
    seed_query: str = "",
) -> pathlib.Path:
    """
    Generate an 800–1200 word digest for a completed round.

    Only successful sources (error is None) are included in the prompt.
    Writes rounds/<n>/digest.md and returns the path.
    """
    successful = [m for m in source_metas if not m.get("error")]

    source_list = "\n".join(
        f"[r.{i + 1}] {m.get('title', 'Untitled')} — {m.get('url', '')}"
        for i, m in enumerate(successful)
    )

    prompt = _PROMPT.format(
        seed_query=seed_query,
        round_idx=round_idx,
        source_count=len(successful),
        source_list=source_list or "(no sources)",
    )

    text = omlx.chat(NOTES_MODEL, [{"role": "user", "content": prompt}])

    round_dir.mkdir(parents=True, exist_ok=True)
    digest_path = round_dir / "digest.md"
    digest_path.write_text(text, encoding="utf-8")
    return digest_path
