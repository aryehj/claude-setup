"""Unit tests for select_winners.py and run_vane.py (Phase 3).

Network/docker side-effects are exercised manually per the plan's Testing section;
these tests cover only pure helpers.
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _load(name: str):
    path = _HERE / f"{name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sw = _load("select_winners")
_rv = _load("run_vane")

skip_if_no_sw = pytest.mark.skipif(
    _sw is None, reason="select_winners.py not yet implemented"
)
skip_if_no_rv = pytest.mark.skipif(
    _rv is None, reason="run_vane.py not yet implemented"
)


# ── select_winners.parse_scores_md ─────────────────────────────────────────────

SCORES_MD = textwrap.dedent("""\
    # SCORES

    | file | label | model | prompt_style | temperature | thinking | total |
    |------|-------|-------|--------------|-------------|----------|-------|
    | q1_a.md | A | m1 | structured | 0.3 | false | 14 |
    | q1_b.md | B | m2 | bare       | 0.0 | false | 12 |
    | q1_c.md | C | m1 | research_system | 0.7 | true | 11 |
    | q1_d.md | D | m3 | bare       | 0.0 | false |  9 |
""")


@skip_if_no_sw
def test_parse_scores_md_count_and_order():
    rows = _sw.parse_scores_md(SCORES_MD)
    assert len(rows) == 4
    # Sorted by total descending
    assert [r["total"] for r in rows] == [14, 12, 11, 9]
    assert rows[0]["label"] == "A"


@skip_if_no_sw
def test_parse_scores_md_extracts_cell_fields():
    rows = _sw.parse_scores_md(SCORES_MD)
    top = rows[0]
    assert top["model"] == "m1"
    assert top["prompt_style"] == "structured"
    assert top["temperature"] == 0.3
    assert top["thinking"] is False


@skip_if_no_sw
def test_build_winners_json_shape():
    rows = _sw.parse_scores_md(SCORES_MD)
    winners = _sw.build_winners_json(rows)
    assert "winner" in winners
    assert "ablations" in winners
    assert winners["winner"]["label"] == "A"
    assert len(winners["ablations"]) == 2  # cap at 2
    assert winners["ablations"][0]["label"] == "B"
    assert winners["ablations"][1]["label"] == "C"


@skip_if_no_sw
def test_build_winners_json_only_winner_when_no_ablations_available():
    rows = _sw.parse_scores_md(
        textwrap.dedent("""\
            | file | label | model | prompt_style | temperature | thinking | total |
            |------|-------|-------|--------------|-------------|----------|-------|
            | q1_a.md | A | m1 | structured | 0.3 | false | 14 |
        """)
    )
    winners = _sw.build_winners_json(rows)
    assert winners["winner"]["label"] == "A"
    assert winners["ablations"] == []


@skip_if_no_sw
def test_winners_template_shape():
    """Template returned when no SCORES.md exists; user hand-edits."""
    tpl = _sw.winners_template()
    assert "winner" in tpl
    assert set(tpl["winner"].keys()) >= {"model", "prompt_style", "temperature", "thinking"}
    assert tpl["ablations"] == []


# ── select_winners.aggregate_cells ─────────────────────────────────────────────

_MULTI_QUERY_SCORES = textwrap.dedent("""\
    | file | label | model | prompt_style | temperature | thinking | total |
    |------|-------|-------|--------------|-------------|----------|-------|
    | q1_a.md | A | m1 | structured | 0.3 | false | 10 |
    | q2_a.md | A | m1 | structured | 0.3 | false | 12 |
    | q1_b.md | B | m2 | bare       | 0.0 | false | 14 |
""")


@skip_if_no_sw
def test_aggregate_cells_sums_across_queries():
    rows = _sw.parse_scores_md(_MULTI_QUERY_SCORES)
    cells = _sw.aggregate_cells(rows)
    assert len(cells) == 2
    # m1/structured aggregate 10+12=22 outranks m2/bare's 14.
    assert cells[0]["model"] == "m1"
    assert cells[0]["total"] == 22
    assert cells[0]["n"] == 2
    assert cells[1]["model"] == "m2"
    assert cells[1]["total"] == 14
    assert cells[1]["n"] == 1


@skip_if_no_sw
def test_build_winners_json_picks_aggregate_winner_not_per_query_max():
    """A single-query row with a high total must NOT win if its cell's
    sweep-wide aggregate is below another cell's aggregate. This is the
    bug that put a q4-only winner in the thinking sweep's winners.json.
    """
    rows = _sw.parse_scores_md(textwrap.dedent("""\
        | file | label | model | prompt_style | temperature | thinking | total |
        |------|-------|-------|--------------|-------------|----------|-------|
        | q1_b.md | B | m2 | bare       | 0.0 | false | 15 |
        | q2_b.md | B | m2 | bare       | 0.0 | false |  5 |
        | q1_a.md | A | m1 | structured | 0.3 | false | 12 |
        | q2_a.md | A | m1 | structured | 0.3 | false | 12 |
    """))
    winners = _sw.build_winners_json(rows)
    # m1/structured aggregate=24 beats m2/bare aggregate=20, even though
    # m2/bare has the single highest per-query row (15).
    assert winners["winner"]["model"] == "m1"
    assert winners["winner"]["prompt_style"] == "structured"


@skip_if_no_sw
def test_aggregate_cells_tiebreak_prefers_no_thinking():
    rows = _sw.parse_scores_md(textwrap.dedent("""\
        | file | label | model | prompt_style | temperature | thinking | total |
        |------|-------|-------|--------------|-------------|----------|-------|
        | q1_a.md | A | m1 | structured | 0.3 | true  | 14 |
        | q1_b.md | B | m1 | structured | 0.3 | false | 14 |
    """))
    cells = _sw.aggregate_cells(rows)
    assert cells[0]["thinking"] is False
    assert cells[1]["thinking"] is True


@skip_if_no_sw
def test_aggregate_cells_tiebreak_prefers_lower_temperature_when_thinking_matches():
    rows = _sw.parse_scores_md(textwrap.dedent("""\
        | file | label | model | prompt_style | temperature | thinking | total |
        |------|-------|-------|--------------|-------------|----------|-------|
        | q1_a.md | A | m1 | structured | 1.0 | true | 14 |
        | q1_b.md | B | m1 | structured | 0.2 | true | 14 |
        | q1_c.md | C | m1 | structured | 0.6 | true | 14 |
    """))
    cells = _sw.aggregate_cells(rows)
    assert [c["temperature"] for c in cells] == [0.2, 0.6, 1.0]


# ── run_vane helpers ───────────────────────────────────────────────────────────

PROVIDERS_RESP = {
    "providers": [
        {
            "id": "uuid-omlx",
            "name": "omlx",
            "chatModels": [
                {"name": "Gemma 4 31B", "key": "gemma-4-31b-it-6bit"},
                {"name": "Gemma 4 26B", "key": "gemma-4-26b-a4b-it-8bit"},
            ],
            "embeddingModels": [
                {"name": "Nomic Embed", "key": "nomicai-modernbert-embed-base-bf16"},
            ],
        },
    ],
}


@skip_if_no_rv
def test_find_provider_id_for_known_model():
    pid = _rv.find_provider_id(PROVIDERS_RESP["providers"], "gemma-4-31b-it-6bit")
    assert pid == "uuid-omlx"


@skip_if_no_rv
def test_find_provider_id_unknown_model_raises():
    with pytest.raises(ValueError, match="not exposed"):
        _rv.find_provider_id(PROVIDERS_RESP["providers"], "nonexistent")


@skip_if_no_rv
def test_find_embedding_model_picks_first():
    eb = _rv.find_embedding_model(PROVIDERS_RESP["providers"])
    assert eb["providerId"] == "uuid-omlx"
    assert eb["key"] == "nomicai-modernbert-embed-base-bf16"


@skip_if_no_rv
def test_find_embedding_model_none_available_raises():
    with pytest.raises(ValueError, match="embedding"):
        _rv.find_embedding_model([
            {"id": "uuid-x", "name": "x", "chatModels": [{"key": "m"}], "embeddingModels": []}
        ])


@skip_if_no_rv
def test_build_vane_body_research_system_uses_system_instructions():
    cell = {
        "model": "gemma-4-31b-it-6bit",
        "prompt_style": "research_system",
        "temperature": 0.3,
        "thinking": False,
    }
    body = _rv.build_vane_body(
        cell=cell,
        query="What is X?",
        provider_id="uuid-omlx",
        embedding={"providerId": "uuid-omlx", "key": "emb-k"},
    )
    assert body["chatModel"] == {"providerId": "uuid-omlx", "key": "gemma-4-31b-it-6bit"}
    assert body["embeddingModel"] == {"providerId": "uuid-omlx", "key": "emb-k"}
    assert body["query"] == "What is X?"
    assert body["systemInstructions"]
    assert "research" in body["systemInstructions"].lower() or "analyst" in body["systemInstructions"].lower()
    assert body["stream"] is False
    assert "web" in body["sources"]


@skip_if_no_rv
def test_build_vane_body_structured_prepends_hint_to_query():
    cell = {
        "model": "m",
        "prompt_style": "structured",
        "temperature": 0.3,
        "thinking": False,
    }
    body = _rv.build_vane_body(
        cell=cell,
        query="What is X?",
        provider_id="uuid",
        embedding={"providerId": "uuid", "key": "emb"},
    )
    assert "What is X?" in body["query"]
    # structured-style hint must be present
    assert any(p in body["query"].lower() for p in ("concise", "cite"))
    assert not body.get("systemInstructions")


@skip_if_no_rv
def test_build_vane_body_bare_passes_query_unchanged():
    cell = {
        "model": "m",
        "prompt_style": "bare",
        "temperature": 0.3,
        "thinking": False,
    }
    body = _rv.build_vane_body(
        cell=cell,
        query="What is X?",
        provider_id="uuid",
        embedding={"providerId": "uuid", "key": "emb"},
    )
    assert body["query"] == "What is X?"
    assert not body.get("systemInstructions")


# ── compute_metrics ────────────────────────────────────────────────────────────

DENYLIST = {"badads.com", "tracker.io"}

SOURCES_OK = [
    {"metadata": {"url": "https://en.wikipedia.org/wiki/Foo", "title": "Foo"}, "content": "..."},
    {"metadata": {"url": "https://www.harvard.edu/page", "title": "H"}, "content": "..."},
    {"metadata": {"url": "https://nasa.gov/x", "title": "N"}, "content": "..."},
    {"metadata": {"url": "https://example.com/article", "title": "E"}, "content": "..."},
]


@skip_if_no_rv
def test_compute_metrics_citation_count():
    m = _rv.compute_metrics(SOURCES_OK, denylist_domains=DENYLIST)
    assert m["citation_count"] == 4


@skip_if_no_rv
def test_compute_metrics_edu_gov_wiki_share():
    m = _rv.compute_metrics(SOURCES_OK, denylist_domains=DENYLIST)
    # 3 of 4 are .edu/.gov/wikipedia → 0.75
    assert abs(m["edu_gov_wiki_share"] - 0.75) < 1e-6


@skip_if_no_rv
def test_compute_metrics_denylist_hits_zero_for_clean():
    m = _rv.compute_metrics(SOURCES_OK, denylist_domains=DENYLIST)
    assert m["denylist_hits"] == 0


@skip_if_no_rv
def test_compute_metrics_denylist_hits_detects_bad_domain():
    bad_sources = SOURCES_OK + [
        {"metadata": {"url": "https://www.badads.com/x", "title": "B"}, "content": "..."},
        {"metadata": {"url": "https://tracker.io/y", "title": "T"}, "content": "..."},
    ]
    m = _rv.compute_metrics(bad_sources, denylist_domains=DENYLIST)
    assert m["denylist_hits"] == 2


@skip_if_no_rv
def test_compute_metrics_empty_sources():
    m = _rv.compute_metrics([], denylist_domains=DENYLIST)
    assert m == {"citation_count": 0, "edu_gov_wiki_share": 0.0, "denylist_hits": 0}


# ── temperature config mutation ────────────────────────────────────────────────

@skip_if_no_rv
def test_mutate_temperature_writes_options(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        '{"version":1,"modelProviders":[{"id":"uuid-omlx","type":"openai_compatible",'
        '"name":"omlx","config":{"apiKey":"x","baseURL":"http://h:8000/v1","model":""}}]}'
    )
    changed = _rv.mutate_temperature(cfg, provider_id="uuid-omlx", temperature=0.7)
    assert changed is True
    import json
    data = json.loads(cfg.read_text())
    assert data["modelProviders"][0]["config"]["options"]["temperature"] == 0.7


@skip_if_no_rv
def test_mutate_temperature_idempotent(tmp_path):
    """Re-applying the same value returns False (no write needed)."""
    cfg = tmp_path / "config.json"
    cfg.write_text(
        '{"modelProviders":[{"id":"u","config":{"options":{"temperature":0.3}}}]}'
    )
    changed = _rv.mutate_temperature(cfg, provider_id="u", temperature=0.3)
    assert changed is False


@skip_if_no_rv
def test_mutate_temperature_unknown_provider_raises(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"modelProviders":[{"id":"u","config":{}}]}')
    with pytest.raises(ValueError, match="provider"):
        _rv.mutate_temperature(cfg, provider_id="missing", temperature=0.3)


# ── load_denylist_domains ──────────────────────────────────────────────────────

@skip_if_no_rv
def test_load_denylist_domains_reads_cache_and_additions(tmp_path):
    cache = tmp_path / "denylist-cache"
    cache.mkdir()
    (cache / "feed1.txt").write_text("badads.com\ntracker.io\n# comment\n")
    (cache / "feed2.txt").write_text("evil.example\n")
    (tmp_path / "denylist-additions.txt").write_text("doubleclick.net\n")
    (tmp_path / "denylist-overrides.txt").write_text("evil.example\n")

    domains = _rv.load_denylist_domains(tmp_path)
    assert "badads.com" in domains
    assert "tracker.io" in domains
    assert "doubleclick.net" in domains
    assert "evil.example" not in domains  # override


@skip_if_no_rv
def test_load_denylist_domains_missing_dir_returns_empty(tmp_path):
    domains = _rv.load_denylist_domains(tmp_path / "absent")
    assert domains == set()
