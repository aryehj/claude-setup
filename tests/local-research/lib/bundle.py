"""
Write per-source markdown files to the session directory tree.
"""
import re
import pathlib


def _slug(url: str, max_len: int = 40) -> str:
    """Derive a filesystem-safe slug from a URL."""
    # Strip scheme and www
    s = re.sub(r"^https?://(www\.)?", "", url)
    # Replace non-alphanumeric runs with hyphens
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s)
    return s[:max_len].strip("-")


def write_source(
    *,
    round_dir: pathlib.Path,
    idx: int,
    url: str,
    title: str,
    round_idx: int,
    branch_label: str,
    engine: str,
    rerank_score: float,
    note: str,
    extracted_text: str,
    timings: dict,
    model: str,
) -> pathlib.Path:
    """
    Write rounds/<n>/sources/<idx:02d>-<slug>.md with YAML frontmatter,
    a note section, and an extracted-text section.

    Returns the path of the written file.
    """
    sources_dir = round_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    slug = _slug(url)
    filename = f"{idx:02d}-{slug}.md"
    path = sources_dir / filename

    # Build YAML frontmatter
    def _yaml_str(v: str) -> str:
        escaped = v.replace('"', '\\"')
        return f'"{escaped}"'

    timings_yaml = "\n".join(f"    {k}: {v}" for k, v in timings.items())

    content = f"""\
---
url: {_yaml_str(url)}
title: {_yaml_str(title)}
round: {round_idx}
branch: {_yaml_str(branch_label)}
engine: {_yaml_str(engine)}
rerank_score: {round(rerank_score, 4)}
model: {_yaml_str(model)}
timings:
{timings_yaml}
---

## Notes

{note}

## Extracted text

{extracted_text}
"""
    path.write_text(content, encoding="utf-8")
    return path
