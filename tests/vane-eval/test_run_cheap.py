"""Unit tests for run_cheap.py (Phase 2)."""
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ── import helpers from run_cheap ──────────────────────────────────────────────

def _import():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_cheap", _HERE / "run_cheap.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _rc = _import()
    build_ofat_cells = _rc.build_ofat_cells
    validate_shortlist = _rc.validate_shortlist
    IMPORT_OK = True
except Exception:
    IMPORT_OK = False

skip_if_missing = pytest.mark.skipif(
    not IMPORT_OK, reason="run_cheap.py not yet implemented"
)


# ── build_ofat_cells ───────────────────────────────────────────────────────────

@skip_if_missing
def test_ofat_cell_count_three_models():
    """M=3 → 1 default + 2 extra models + 2 extra prompts + 2 extra temps = 7 unique cells."""
    cells = build_ofat_cells(
        models=["m1", "m2", "m3"],
        default_model="m1",
    )
    assert len(cells) == 7, f"expected 7, got {len(cells)}: {[c.label for c in cells]}"


@skip_if_missing
def test_ofat_cell_count_one_model():
    """M=1 → 1 default + 0 extra models + 2 extra prompts + 2 extra temps = 5 unique cells."""
    cells = build_ofat_cells(
        models=["m1"],
        default_model="m1",
    )
    assert len(cells) == 5, f"expected 5, got {len(cells)}: {[c.label for c in cells]}"


@skip_if_missing
def test_ofat_no_duplicate_default():
    """The default cell must appear exactly once, regardless of axis overlap."""
    cells = build_ofat_cells(
        models=["m1", "m2"],
        default_model="m1",
        default_prompt_style="structured",
        default_temperature=0.3,
    )
    default_hits = [
        c for c in cells
        if c.model == "m1"
        and c.prompt_style == "structured"
        and c.temperature == 0.3
    ]
    assert len(default_hits) == 1, (
        f"default cell appears {len(default_hits)} times (expected 1)"
    )


@skip_if_missing
def test_ofat_all_models_covered():
    """Every model in the input list appears at least once."""
    models = ["alpha", "beta", "gamma"]
    cells = build_ofat_cells(models=models, default_model="alpha")
    covered = {c.model for c in cells}
    assert set(models) <= covered, f"missing models: {set(models) - covered}"


@skip_if_missing
def test_ofat_all_prompt_styles_covered():
    """All three prompt styles appear in cells."""
    cells = build_ofat_cells(models=["m1"], default_model="m1")
    styles = {c.prompt_style for c in cells}
    assert styles == {"bare", "structured", "research_system"}, (
        f"prompt styles: {styles}"
    )


@skip_if_missing
def test_ofat_all_temperatures_covered():
    """All three temperatures appear in cells."""
    cells = build_ofat_cells(models=["m1"], default_model="m1")
    temps = {c.temperature for c in cells}
    assert {0.0, 0.3, 0.7} <= temps, f"temperatures: {temps}"


@skip_if_missing
def test_ofat_thinking_axis_omitted():
    """Thinking axis is not swept — every cheap cell records thinking=False."""
    cells = build_ofat_cells(models=["m1", "m2"], default_model="m1")
    assert all(c.thinking is False for c in cells), (
        f"thinking values: {[c.thinking for c in cells]}"
    )


@skip_if_missing
def test_ofat_labels_non_empty():
    """Every cell has a non-empty label."""
    cells = build_ofat_cells(models=["m1", "m2"], default_model="m1")
    for c in cells:
        assert c.label, f"cell has empty label: {c}"


@skip_if_missing
def test_ofat_query_id_placeholder():
    """build_ofat_cells returns cells with query_id as empty string (set by caller)."""
    cells = build_ofat_cells(models=["m1"], default_model="m1")
    for c in cells:
        assert c.query_id == "", f"expected empty query_id, got {c.query_id!r}"


# ── validate_shortlist ─────────────────────────────────────────────────────────

@skip_if_missing
def test_validate_shortlist_ok():
    """validate_shortlist passes silently when all entries are in discovered list."""
    validate_shortlist(
        shortlist=["m1", "m2"],
        discovered=["m1", "m2", "m3"],
    )


@skip_if_missing
def test_validate_shortlist_missing_entry():
    """validate_shortlist raises SystemExit or ValueError for unknown model."""
    with pytest.raises((SystemExit, ValueError)):
        validate_shortlist(
            shortlist=["m1", "ghost-model"],
            discovered=["m1", "m2"],
        )


@skip_if_missing
def test_validate_shortlist_error_names_missing_model():
    """The error message must name the missing model."""
    try:
        validate_shortlist(
            shortlist=["m1", "ghost-model"],
            discovered=["m1", "m2"],
        )
        pytest.fail("expected exception not raised")
    except (SystemExit, ValueError) as exc:
        assert "ghost-model" in str(exc), (
            f"error message doesn't name missing model: {exc}"
        )


# ── cell-count guard ───────────────────────────────────────────────────────────

@skip_if_missing
def test_cell_count_guard_passes_under_limit():
    """No error when total cells × queries ≤ 90."""
    _rc.check_cell_count(cells=7, queries=6, force=False)  # 42 ≤ 90


@skip_if_missing
def test_cell_count_guard_raises_over_limit():
    """Raises SystemExit when cells × queries > 90 and force=False."""
    with pytest.raises(SystemExit):
        _rc.check_cell_count(cells=16, queries=6, force=False)  # 96 > 90


@skip_if_missing
def test_cell_count_guard_force_bypasses():
    """--force bypasses the 90-call guard."""
    _rc.check_cell_count(cells=16, queries=6, force=True)  # should not raise
