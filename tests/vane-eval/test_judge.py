"""Tests that JUDGE.md is well-formed enough to drive Phase 4 grading.

The doc itself is human-read, but a few invariants are load-bearing:

  - The GRADING PROMPT block exists and is self-contained.
  - The run-glob pattern points at the right results subdirs.
  - It references queries.md so the judge reads reference paragraphs.
  - The three cheap-phase rubric axes are named (coverage, accuracy,
    succinctness).
  - The SCORES.md column header it instructs the judge to emit matches the
    columns `select_winners.parse_scores_md` consumes — otherwise the next
    step in the workflow silently produces an empty winners.json.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_JUDGE = _HERE / "JUDGE.md"

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

skip_if_no_judge = pytest.mark.skipif(
    not _JUDGE.exists(), reason="JUDGE.md not yet written"
)
skip_if_no_sw = pytest.mark.skipif(
    _sw is None, reason="select_winners.py not yet implemented"
)


# Required SCORES.md columns, in the order the GRADING PROMPT should emit them.
# `parse_scores_md` is case-insensitive and tolerant of extra columns, but
# these names must all be present.
_REQUIRED_SCORE_COLUMNS = [
    "file",
    "label",
    "model",
    "prompt_style",
    "temperature",
    "thinking",
    "total",
]


@skip_if_no_judge
def test_judge_md_exists_and_nonempty():
    text = _JUDGE.read_text()
    assert len(text) > 200, "JUDGE.md should be a substantive doc, not a stub"


@skip_if_no_judge
def test_judge_has_grading_prompt_section():
    text = _JUDGE.read_text()
    assert re.search(r"^##\s+GRADING PROMPT", text, re.MULTILINE), (
        "Expected a `## GRADING PROMPT` section heading so users can find what "
        "to paste."
    )


@skip_if_no_judge
def test_judge_has_how_to_use_section():
    text = _JUDGE.read_text()
    assert re.search(r"^##\s+How to use", text, re.MULTILINE | re.IGNORECASE)


@skip_if_no_judge
def test_judge_has_rubric_details_section():
    text = _JUDGE.read_text()
    assert re.search(r"^##\s+Rubric", text, re.MULTILINE | re.IGNORECASE)


@skip_if_no_judge
def test_judge_references_queries_md():
    """Judge must read references from queries.md to grade fairly."""
    text = _JUDGE.read_text()
    assert "queries.md" in text


@skip_if_no_judge
def test_judge_run_glob_includes_thinking_and_cheap_dirs():
    """Cheap-phase data is produced by run_thinking.py (dirs prefixed
    `thinking-`); run_cheap.py uses `cheap-`. Both must be discoverable.
    """
    text = _JUDGE.read_text()
    # We accept either a brace expansion or two separate globs; the load-bearing
    # thing is that both prefixes appear in the run-discovery instruction.
    assert "thinking-" in text
    assert "cheap-" in text


@skip_if_no_judge
def test_judge_names_three_cheap_rubric_axes():
    text = _JUDGE.read_text().lower()
    for axis in ("coverage", "accuracy", "succinctness"):
        assert axis in text, f"Missing rubric axis: {axis}"


@skip_if_no_judge
def test_judge_specifies_total_out_of_15_for_cheap_phase():
    """Three axes × 5 = 15. The /15 cap signals to the judge that citation
    is intentionally not in this rubric.
    """
    text = _JUDGE.read_text()
    assert "/15" in text or "out of 15" in text.lower()


@skip_if_no_judge
def test_judge_instructs_writing_scores_md():
    text = _JUDGE.read_text()
    assert "SCORES.md" in text


@skip_if_no_judge
def test_judge_lists_all_select_winners_columns():
    """If the GRADING PROMPT omits any column select_winners expects, the
    pre-fill silently degrades."""
    text = _JUDGE.read_text().lower()
    for col in _REQUIRED_SCORE_COLUMNS:
        assert col in text, f"GRADING PROMPT must mention column '{col}'"


@skip_if_no_judge
def test_judge_credits_correct_but_unanticipated_answers():
    """The reference paragraph is a floor, not a ceiling."""
    text = _JUDGE.read_text().lower()
    # Accept any of several wordings.
    assert any(
        phrase in text
        for phrase in (
            "floor, not a ceiling",
            "floor not a ceiling",
            "unanticipated",
            "not penalize",
            "do not penalise",
            "credit correct",
        )
    ), "JUDGE.md must tell the judge to credit answers beyond the reference."


@skip_if_no_judge
def test_judge_has_phase_4b_pointer_for_vane():
    """The judge must know that citation grading isn't applicable yet so
    they don't invent a column for it.
    """
    text = _JUDGE.read_text().lower()
    assert "vane" in text, (
        "JUDGE.md should at least mention Vane to scope the grader's "
        "expectations (citation rubric arrives in Phase 4b)."
    )


# ── Phase 4b: Vane-phase rubric extension ─────────────────────────────────────


@skip_if_no_judge
def test_judge_has_vane_rubric_section():
    """A Vane-phase rubric block must exist as its own section so the grader
    can find it without parsing the whole doc."""
    text = _JUDGE.read_text()
    assert re.search(
        r"^##+\s+.*Vane.*[Rr]ubric|^##+\s+[Vv]ane[ -][Pp]hase",
        text,
        re.MULTILINE,
    ), "Expected a Vane-phase rubric section heading."


@skip_if_no_judge
def test_judge_names_citation_axis():
    text = _JUDGE.read_text().lower()
    assert "citation" in text, "Vane rubric must add a citation axis."


@skip_if_no_judge
def test_judge_specifies_total_out_of_20_for_vane_phase():
    """Four axes × 5 = 20 once citation joins coverage/accuracy/succinctness."""
    text = _JUDGE.read_text()
    assert "/20" in text or "out of 20" in text.lower()


@skip_if_no_judge
def test_judge_grading_prompt_globs_vane_runs():
    """STEP 1 must also pick up vane-*/MANIFEST.md so a confirm run is graded
    rather than silently skipped."""
    text = _JUDGE.read_text()
    assert "vane-" in text, (
        "GRADING PROMPT must mention the vane-* run prefix so it can locate "
        "Vane confirm-phase runs."
    )


@skip_if_no_judge
def test_judge_grading_prompt_detects_run_type_from_manifest():
    """Auto-detect: the cheap rubric is /15, the Vane rubric is /20. The
    GRADING PROMPT must tell the grader how to pick which one applies — the
    natural signal is MANIFEST.md's title (e.g. '# MANIFEST (Vane phase)').
    """
    text = _JUDGE.read_text()
    assert "MANIFEST (Vane phase)" in text, (
        "GRADING PROMPT must reference the Vane-phase MANIFEST title so it "
        "can switch rubrics."
    )


@skip_if_no_judge
def test_judge_cheap_phase_citation_column_is_na():
    """Cheap-phase rows have no citations; spell out that the column is 'n/a'
    so the assembled SCORES.md is self-consistent across phases."""
    text = _JUDGE.read_text().lower()
    assert "n/a" in text, (
        "JUDGE.md should specify the cheap-phase citation column as 'n/a'."
    )


# Round-trip: a minimal SCORES.md following the JUDGE.md schema must parse.

_SAMPLE_SCORES_TABLE = textwrap.dedent("""\
    # SCORES (sample)

    | file | label | model | prompt_style | temperature | thinking | coverage | accuracy | succinctness | total |
    |------|-------|-------|--------------|-------------|----------|----------|----------|--------------|-------|
    | q1_m1.md | m1 · structured · t=0.2 · think=off | m1 | structured | 0.2 | false | 5 | 5 | 4 | 14 |
    | q1_m2.md | m2 · bare · t=0.6 · think=on        | m2 | bare       | 0.6 | true  | 4 | 4 | 4 | 12 |
""")


@skip_if_no_sw
def test_sample_scores_table_round_trips_through_select_winners():
    rows = _sw.parse_scores_md(_SAMPLE_SCORES_TABLE)
    assert len(rows) == 2
    assert rows[0]["total"] == 14
    assert rows[0]["model"] == "m1"
    assert rows[0]["thinking"] is False
    winners = _sw.build_winners_json(rows)
    assert winners["winner"]["model"] == "m1"
    assert len(winners["ablations"]) == 1
    assert winners["ablations"][0]["model"] == "m2"
