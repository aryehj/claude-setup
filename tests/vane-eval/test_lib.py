"""Unit tests for tests/vane-eval/lib modules (Phase 1)."""
import sys
import textwrap
from pathlib import Path

import pytest

# Add tests/vane-eval/ to path so we can import lib as a package.
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lib.cells import Cell, build_prompt
from lib.queries import Query, load as load_queries

# ── Cell dataclass ─────────────────────────────────────────────────────────────

def test_cell_label_populated():
    cell = Cell(
        query_id="q1",
        model="gemma-4-E4B",
        prompt_style="structured",
        temperature=0.3,
        thinking=False,
        label="gemma-4-E4B · structured · t=0.3 · think=off",
    )
    assert cell.label == "gemma-4-E4B · structured · t=0.3 · think=off"
    assert cell.thinking is False


# ── build_prompt ───────────────────────────────────────────────────────────────

def test_build_prompt_bare():
    system, user = build_prompt("What is the speed of light?", "bare")
    assert system is None
    assert "speed of light" in user


def test_build_prompt_structured():
    system, user = build_prompt("What is the speed of light?", "structured")
    assert system is None
    assert "speed of light" in user
    # structured style must inject a format hint
    assert any(phrase in user.lower() for phrase in ("concise", "cite", "cite key", "format"))


def test_build_prompt_research_system():
    system, user = build_prompt("What is the speed of light?", "research_system")
    assert system is not None and len(system) > 0
    assert "speed of light" in user


def test_build_prompt_invalid_style():
    with pytest.raises((ValueError, TypeError)):
        build_prompt("query", "nonexistent_style")


# ── queries.load ───────────────────────────────────────────────────────────────

QUERIES_MD = _HERE / "queries.md"


@pytest.mark.skipif(not QUERIES_MD.exists(), reason="queries.md not yet authored")
def test_load_queries_count():
    qs = load_queries(QUERIES_MD)
    assert len(qs) == 6


@pytest.mark.skipif(not QUERIES_MD.exists(), reason="queries.md not yet authored")
def test_load_queries_ids():
    qs = load_queries(QUERIES_MD)
    assert [q.id for q in qs] == ["q1", "q2", "q3", "q4", "q5", "q6"]


@pytest.mark.skipif(not QUERIES_MD.exists(), reason="queries.md not yet authored")
def test_load_queries_fields_non_empty():
    qs = load_queries(QUERIES_MD)
    for q in qs:
        assert q.query, f"{q.id} has empty query"
        assert q.reference, f"{q.id} has empty reference"
        assert q.key_facts, f"{q.id} has empty key_facts"
        assert len(q.key_facts) >= 2, f"{q.id} should have ≥2 key facts"


@pytest.mark.skipif(not QUERIES_MD.exists(), reason="queries.md not yet authored")
def test_load_queries_title():
    qs = load_queries(QUERIES_MD)
    for q in qs:
        assert q.title, f"{q.id} has no title"


def test_load_queries_missing_field(tmp_path):
    """load() must fail loud on missing required fields."""
    bad_md = tmp_path / "queries.md"
    bad_md.write_text(textwrap.dedent("""\
        ## Q1: test

        **Query:** What is X?

        <!-- no Reference or Key facts -->
    """))
    with pytest.raises(ValueError, match="[Rr]eference|[Kk]ey fact"):
        load_queries(bad_md)


def test_load_queries_inline_fixture(tmp_path):
    """Round-trip a minimal well-formed queries.md."""
    md = tmp_path / "queries.md"
    md.write_text(textwrap.dedent("""\
        ## Q1: alpha

        **Query:** What is alpha?

        **Reference:** Alpha is the first letter of the Greek alphabet, used widely in
        mathematics and science.

        **Key facts:**
        - First letter of Greek alphabet
        - Commonly used in math and science

        ## Q2: beta

        **Query:** What is beta?

        **Reference:** Beta is the second letter of the Greek alphabet.

        **Key facts:**
        - Second letter of Greek alphabet
        - Follows alpha
    """))
    qs = load_queries(md)
    assert len(qs) == 2
    assert qs[0].id == "q1"
    assert qs[1].id == "q2"
    assert "alpha" in qs[0].query.lower()
    assert len(qs[0].key_facts) == 2


# ── discover_omlx_models (live — skip unless env is set) ──────────────────────

@pytest.mark.skipif(
    not __import__("os").environ.get("OMLX_API_KEY"),
    reason="OMLX_API_KEY not set; skip live omlx probe",
)
def test_discover_omlx_models_live():
    import os
    from lib.cells import discover_omlx_models
    omlx_host = os.environ.get("OMLX_HOST", "http://host.docker.internal:8000")
    base = omlx_host.rstrip("/") + "/v1"
    models = discover_omlx_models(base)
    assert isinstance(models, list)
    assert len(models) >= 1
    assert all(isinstance(m, str) for m in models)
