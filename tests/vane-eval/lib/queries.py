"""Parser for tests/vane-eval/queries.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = Path(__file__).parent.parent / "queries.md"

_FIELD_PATTERNS = {
    "query":     re.compile(r"\*\*Query:\*\*\s*(.+?)(?=\n\n\*\*|\Z)", re.DOTALL),
    "reference": re.compile(r"\*\*Reference:\*\*\s*(.+?)(?=\n\n\*\*|\Z)", re.DOTALL),
    "key_facts": re.compile(r"\*\*Key facts:\*\*\s*(.+?)(?=\n---|\Z)", re.DOTALL),
}


@dataclass
class Query:
    id: str
    title: str
    query: str
    reference: str
    key_facts: list[str] = field(default_factory=list)


def load(path: Optional[Path] = None) -> list[Query]:
    """Parse queries.md and return a list of Query objects.

    Raises ValueError if any H2 section is missing required fields.
    """
    if path is None:
        path = _DEFAULT_PATH
    text = path.read_text()

    # Split on H2 headings; first element is the file header (before first ##)
    sections = re.split(r"\n(?=## )", text)
    queries: list[Query] = []

    for section in sections:
        heading_match = re.match(r"## (Q\d+): (.+)", section)
        if not heading_match:
            continue  # preamble or non-query section

        slug = heading_match.group(1).lower()  # "Q1" → "q1"
        title = heading_match.group(2).strip()

        def _extract(field_name: str) -> str:
            m = _FIELD_PATTERNS[field_name].search(section)
            if not m:
                raise ValueError(
                    f"Section {slug!r} is missing required field **{field_name.title()}:**"
                )
            return m.group(1).strip()

        query_text = _extract("query")
        reference_text = _extract("reference")

        key_facts_raw = _FIELD_PATTERNS["key_facts"].search(section)
        if not key_facts_raw:
            raise ValueError(
                f"Section {slug!r} is missing required field **Key facts:**"
            )
        key_facts = [
            line.lstrip("- ").strip()
            for line in key_facts_raw.group(1).splitlines()
            if line.strip().startswith("-")
        ]
        if not key_facts:
            raise ValueError(
                f"Section {slug!r} has empty Key facts list"
            )

        queries.append(Query(
            id=slug,
            title=title,
            query=query_text,
            reference=reference_text,
            key_facts=key_facts,
        ))

    return queries
