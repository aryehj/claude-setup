"""
Unit tests for Phase 6 SearXNG-config tuning harness.

Tests: scoring function, regex auto-labeler, constrained-field validator,
iteration record schema.  No docker or network access needed.
"""
import hashlib
import json
import sys
import pathlib
import pytest

# Make eval/searxng-config importable.
REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "local-research"))

from eval.searxng_config import score as _score_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(url, label=""):
    return {"url": url, "title": "t", "engine": "google", "rerank_score": 0.5, "label": label}


# ---------------------------------------------------------------------------
# Score function
# ---------------------------------------------------------------------------

class TestComputeScore:
    def test_all_science(self):
        labeled = [_result(f"https://arxiv.org/{i}", "science") for i in range(5)]
        assert _score_mod.compute_score(labeled) == 5

    def test_all_bad(self):
        labeled = [_result(f"https://marketing.com/{i}", "seo-fluff") for i in range(3)]
        assert _score_mod.compute_score(labeled) == -3

    def test_mixed(self):
        labeled = [
            _result("https://arxiv.org/1", "science"),
            _result("https://arxiv.org/2", "editorial-considered"),
            _result("https://spam.com/best-10", "seo-fluff"),
            _result("https://spam.com/top-10", "listicle"),
            _result("https://brand.com/buy", "marketing"),
            _result("https://wikipedia.org/wiki/foo", "other"),
        ]
        # science(1) + ed(1) - seo(1) - listicle(1) - marketing(1) = -1
        assert _score_mod.compute_score(labeled) == -1

    def test_empty(self):
        assert _score_mod.compute_score([]) == 0

    def test_unlabeled_ignored(self):
        labeled = [_result("https://arxiv.org/1", "science"), _result("https://x.com/1", "")]
        assert _score_mod.compute_score(labeled) == 1

    def test_label_distribution_keys(self):
        labeled = [
            _result("a", "science"),
            _result("b", "seo-fluff"),
            _result("c", "other"),
        ]
        dist = _score_mod.label_distribution(labeled)
        assert dist["science"] == 1
        assert dist["seo-fluff"] == 1
        assert dist["other"] == 1
        assert dist.get("listicle", 0) == 0


# ---------------------------------------------------------------------------
# Regex auto-labeler
# ---------------------------------------------------------------------------

class TestRegexLabel:
    def _label(self, url, title=""):
        return _score_mod.regex_label(url, title)

    def test_arxiv_is_science(self):
        assert self._label("https://arxiv.org/abs/2310.12345") == "science"

    def test_pubmed_is_science(self):
        assert self._label("https://pubmed.ncbi.nlm.nih.gov/12345678") == "science"

    def test_gov_is_editorial_considered(self):
        assert self._label("https://www.cdc.gov/nutrition/index.html") == "editorial-considered"

    def test_edu_is_editorial_considered(self):
        assert self._label("https://pmc.stanford.edu/articles/") == "editorial-considered"

    def test_best_dash_is_listicle(self):
        assert self._label("https://example.com/best-creatine-supplements") == "listicle"

    def test_top_n_is_listicle(self):
        assert self._label("https://example.com/top-10-recovery-tips") == "listicle"

    def test_utm_marketing_url(self):
        # Pure UTM-tagged URL — penalty pattern
        assert self._label("https://shop.com/product?utm_source=google&utm_campaign=sale") == "marketing"

    def test_unknown_returns_none(self):
        assert self._label("https://somearticle.medium.com/interesting-piece-abc123") is None

    def test_nih_is_science(self):
        assert self._label("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234") == "science"

    def test_nature_is_science(self):
        assert self._label("https://www.nature.com/articles/s41467-024-12345-6") == "science"

    def test_wikipedia_is_other(self):
        assert self._label("https://en.wikipedia.org/wiki/Creatine") == "other"

    def test_reddit_is_other(self):
        assert self._label("https://www.reddit.com/r/running/comments/abc") == "other"


# ---------------------------------------------------------------------------
# Constrained-fields validator
# ---------------------------------------------------------------------------

class TestConstrainedFields:
    CONSTRAINED = ("secret_key", "base_url", "outgoing.proxies", "server.secret_key", "server.base_url")

    def test_accepts_engine_change(self):
        baseline = "engines:\n  - name: arxiv\n    weight: 1.0\n"
        candidate = "engines:\n  - name: arxiv\n    weight: 3.0\n"
        ok, reason = _score_mod.validate_candidate(baseline, candidate)
        assert ok, reason

    def test_rejects_secret_key_change(self):
        baseline = 'server:\n  secret_key: "abc"\n'
        candidate = 'server:\n  secret_key: "evil"\n'
        ok, reason = _score_mod.validate_candidate(baseline, candidate)
        assert not ok
        assert "secret_key" in reason

    def test_rejects_base_url_change(self):
        baseline = 'server:\n  base_url: "http://research-searxng:8080/"\n'
        candidate = 'server:\n  base_url: "http://evil.com/"\n'
        ok, reason = _score_mod.validate_candidate(baseline, candidate)
        assert not ok

    def test_rejects_proxy_change(self):
        baseline = 'outgoing:\n  proxies:\n    all://: "http://172.17.0.1:8888"\n'
        candidate = 'outgoing:\n  proxies:\n    all://: "http://attacker.com/"\n'
        ok, reason = _score_mod.validate_candidate(baseline, candidate)
        assert not ok

    def test_accepts_plugin_additions(self):
        baseline = 'enabled_plugins:\n  - tracker_url_remover\n'
        candidate = 'enabled_plugins:\n  - tracker_url_remover\n  - oa_doi_rewrite\n'
        ok, reason = _score_mod.validate_candidate(baseline, candidate)
        assert ok, reason


# ---------------------------------------------------------------------------
# Iteration record schema
# ---------------------------------------------------------------------------

class TestIterationRecord:
    REQUIRED_FIELDS = {
        "ts", "settings_sha", "axis_touched", "mutation_summary",
        "rationale", "score", "label_dist_per_query", "kept_or_reverted",
    }

    def _make_record(self, **overrides):
        base = {
            "ts": "2026-05-06T12:00:00Z",
            "settings_sha": "abc123",
            "axis_touched": "engine_list",
            "mutation_summary": "add pubmed",
            "rationale": "PubMed should surface medical literature",
            "score": 3,
            "label_dist_per_query": {"q3": {"science": 5}, "creatine": {}, "finance-team": {}},
            "kept_or_reverted": "kept",
        }
        base.update(overrides)
        return base

    def test_valid_record_passes(self):
        record = self._make_record()
        missing = _score_mod.missing_record_fields(record)
        assert missing == set()

    def test_missing_score_detected(self):
        record = self._make_record()
        del record["score"]
        missing = _score_mod.missing_record_fields(record)
        assert "score" in missing

    def test_invalid_kept_or_reverted(self):
        record = self._make_record(kept_or_reverted="maybe")
        errs = _score_mod.validate_record(record)
        assert any("kept_or_reverted" in e for e in errs)

    def test_stop_reason_on_final_row(self):
        # Final row must have stop_reason field.
        record = self._make_record(stop_reason="max_rounds")
        errs = _score_mod.validate_record(record, is_final=True)
        assert not errs

    def test_final_row_missing_stop_reason(self):
        record = self._make_record()
        errs = _score_mod.validate_record(record, is_final=True)
        assert any("stop_reason" in e for e in errs)


# ---------------------------------------------------------------------------
# Settings SHA stability
# ---------------------------------------------------------------------------

class TestSettingsSha:
    def test_sha_of_same_content_identical(self):
        content = "engines:\n  - name: arxiv\n"
        sha1 = _score_mod.settings_sha(content)
        sha2 = _score_mod.settings_sha(content)
        assert sha1 == sha2

    def test_sha_changes_on_diff(self):
        a = "engines:\n  - name: arxiv\n"
        b = "engines:\n  - name: pubmed\n"
        assert _score_mod.settings_sha(a) != _score_mod.settings_sha(b)

    def test_sha_is_hex_string(self):
        sha = _score_mod.settings_sha("test")
        int(sha, 16)  # raises ValueError if not hex
