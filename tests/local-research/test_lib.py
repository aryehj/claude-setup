"""
Unit tests for Phase 2 pipeline modules.
All tests use fixture data — no live services required.
"""
import math
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Ensure lib is importable from this directory
sys.path.insert(0, os.path.dirname(__file__))

# Import all lib modules up-front so patch() can resolve their attributes.
import lib.omlx  # noqa: F401 — side effect: makes lib.omlx patchable
import lib.search
import lib.rerank
import lib.expand
import lib.pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(url, title="t", content="c", engine="google", score=1.0):
    return {"url": url, "title": title, "content": content, "engine": engine, "score": score}


# ---------------------------------------------------------------------------
# lib/search.py — SearXNG JSON fixture parsing
# ---------------------------------------------------------------------------

class TestSearch(unittest.TestCase):
    def test_parse_response(self):
        fixture = {
            "results": [
                {"url": "https://a.com", "title": "A", "content": "aaa", "engine": "google", "score": 0.9},
                {"url": "https://b.com", "title": "B", "content": "bbb", "engine": "bing", "score": 0.7},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = fixture
        mock_resp.raise_for_status.return_value = None

        with patch("lib.search.requests.get", return_value=mock_resp) as mock_get:
            results = lib.search.search("test query", n=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://a.com")
        self.assertEqual(results[1]["engine"], "bing")
        self.assertTrue(mock_get.called)

    def test_n_limits_results(self):
        fixture = {"results": [{"url": f"https://{i}.com", "title": str(i), "content": "x", "engine": "g", "score": 1.0} for i in range(10)]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = fixture
        mock_resp.raise_for_status.return_value = None

        with patch("lib.search.requests.get", return_value=mock_resp):
            results = lib.search.search("q", n=3)

        self.assertLessEqual(len(results), 3)

    def test_empty_results(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.return_value = None

        with patch("lib.search.requests.get", return_value=mock_resp):
            results = lib.search.search("nothing", n=5)

        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# lib/rerank.py — ordering, dedupe, exclude_urls
# ---------------------------------------------------------------------------

class TestRerank(unittest.TestCase):
    """
    Embedding vectors chosen so cosine similarity to the query is predictable:
      query  = [1, 0, 0]
      hi_vec = [1, 0, 0]   → cosine 1.0  (perfect)
      md_vec = [1, 1, 0]   → cosine ~0.71
      lo_vec = [0, 1, 0]   → cosine 0.0  (orthogonal)
    Expected rank: hi > md > lo
    """

    QUERY_VEC = [1.0, 0.0, 0.0]
    HI_VEC = [1.0, 0.0, 0.0]
    MD_VEC = [1.0, 1.0, 0.0]
    LO_VEC = [0.0, 1.0, 0.0]

    def _embed_side_effect(self, model, texts):
        mapping = {
            "query": self.QUERY_VEC,
            "hi title\nhi content": self.HI_VEC,
            "md title\nmd content": self.MD_VEC,
            "lo title\nlo content": self.LO_VEC,
        }
        return [mapping.get(t, [0.0, 0.0, 1.0]) for t in texts]

    def test_ranking_order(self):
        results = [
            _make_result("https://lo.com", title="lo title", content="lo content"),
            _make_result("https://md.com", title="md title", content="md content"),
            _make_result("https://hi.com", title="hi title", content="hi content"),
        ]
        with patch("lib.omlx.embed", side_effect=self._embed_side_effect):
            ranked = lib.rerank.rerank("query", results, top_k=3)

        urls = [r["url"] for r in ranked]
        self.assertEqual(urls[0], "https://hi.com", "highest-similarity result should be first")
        self.assertEqual(urls[-1], "https://lo.com", "lowest-similarity result should be last")

    def test_rerank_score_present(self):
        results = [_make_result("https://x.com", title="hi title", content="hi content")]
        with patch("lib.omlx.embed", side_effect=self._embed_side_effect):
            ranked = lib.rerank.rerank("query", results, top_k=5)
        self.assertIn("rerank_score", ranked[0])
        self.assertIsInstance(ranked[0]["rerank_score"], float)

    def test_url_dedupe_within_results(self):
        """Duplicate URLs in the input list should appear only once in output."""
        results = [
            _make_result("https://dup.com", title="hi title", content="hi content"),
            _make_result("https://dup.com", title="hi title", content="hi content"),
            _make_result("https://other.com", title="lo title", content="lo content"),
        ]
        with patch("lib.omlx.embed", side_effect=self._embed_side_effect):
            ranked = lib.rerank.rerank("query", results, top_k=10)
        urls = [r["url"] for r in ranked]
        self.assertEqual(len(urls), len(set(urls)), "duplicate URLs must be removed")

    def test_exclude_urls_dropped(self):
        """URLs in exclude_urls must not appear in output even if they rank highly."""
        results = [
            _make_result("https://hi.com", title="hi title", content="hi content"),
            _make_result("https://lo.com", title="lo title", content="lo content"),
        ]
        with patch("lib.omlx.embed", side_effect=self._embed_side_effect):
            ranked = lib.rerank.rerank("query", results, top_k=10, exclude_urls={"https://hi.com"})
        urls = [r["url"] for r in ranked]
        self.assertNotIn("https://hi.com", urls)
        self.assertIn("https://lo.com", urls)

    def test_top_k_respected(self):
        results = [_make_result(f"https://{i}.com", title="lo title", content="lo content") for i in range(10)]
        with patch("lib.omlx.embed", side_effect=lambda m, texts: [[0.0, 1.0, 0.0]] * len(texts)):
            ranked = lib.rerank.rerank("query", results, top_k=3)
        self.assertLessEqual(len(ranked), 3)

    def test_original_fields_preserved(self):
        results = [_make_result("https://x.com", title="hi title", content="hi content", engine="brave", score=0.5)]
        with patch("lib.omlx.embed", side_effect=self._embed_side_effect):
            ranked = lib.rerank.rerank("query", results, top_k=5)
        self.assertEqual(ranked[0]["engine"], "brave")
        self.assertAlmostEqual(ranked[0]["score"], 0.5)


# ---------------------------------------------------------------------------
# lib/expand.py — original query preserved, parsing
# ---------------------------------------------------------------------------

class TestExpand(unittest.TestCase):
    def test_original_query_first(self):
        raw = "technical term\nbroader phrasing\nalternate wording\nlay description"
        with patch("lib.omlx.chat", return_value=raw):
            result = lib.expand.expand("original query", n=4)
        self.assertEqual(result[0], "original query")

    def test_expansion_count(self):
        raw = "a\nb\nc\nd"
        with patch("lib.omlx.chat", return_value=raw):
            result = lib.expand.expand("q", n=4)
        self.assertGreaterEqual(len(result), 2)

    def test_blank_lines_stripped(self):
        raw = "alpha\n\nbeta\n\n"
        with patch("lib.omlx.chat", return_value=raw):
            result = lib.expand.expand("seed", n=4)
        for item in result:
            self.assertGreater(len(item.strip()), 0, "blank entries must not appear")

    def test_returns_list_of_strings(self):
        with patch("lib.omlx.chat", return_value="one\ntwo"):
            result = lib.expand.expand("seed", n=2)
        self.assertIsInstance(result, list)
        for item in result:
            self.assertIsInstance(item, str)


# ---------------------------------------------------------------------------
# lib/pipeline.py — gather_sources integration (mocked)
# ---------------------------------------------------------------------------

class TestPipeline(unittest.TestCase):
    def _fake_search(self, query, n=20):
        return [
            {"url": f"https://{query[:3].replace(' ', '_')}.example.com/{i}", "title": f"T{i}", "content": f"C{i}", "engine": "google", "score": 1.0}
            for i in range(3)
        ]

    def _fake_rerank(self, query, results, top_k=15, exclude_urls=None):
        exclude_urls = exclude_urls or set()
        out = [r for r in results if r["url"] not in exclude_urls]
        for r in out:
            r["rerank_score"] = 0.9
        return out[:top_k]

    def test_returns_expected_keys(self):
        with patch("lib.expand.expand", return_value=["q", "q alt"]), \
             patch("lib.search.search", side_effect=self._fake_search), \
             patch("lib.rerank.rerank", side_effect=self._fake_rerank):
            result = lib.pipeline.gather_sources("test query")
        for key in ("query", "expansions", "raw_results", "ranked", "timings"):
            self.assertIn(key, result)

    def test_exclude_urls_propagated(self):
        excluded = {"https://tes.example.com/0"}
        captured = {}

        def fake_rerank(query, results, top_k=15, exclude_urls=None):
            captured["exclude_urls"] = exclude_urls
            return [r for r in results if r["url"] not in (exclude_urls or set())]

        with patch("lib.expand.expand", return_value=["q"]), \
             patch("lib.search.search", side_effect=self._fake_search), \
             patch("lib.rerank.rerank", side_effect=fake_rerank):
            lib.pipeline.gather_sources("q", exclude_urls=excluded)

        self.assertEqual(captured["exclude_urls"], excluded)

    def test_url_dedupe_across_expansions(self):
        """Two expansion queries returning the same URL should dedupe before reranking."""
        shared_url = "https://shared.example.com/"

        def dupe_search(query, n=20):
            return [{"url": shared_url, "title": "shared", "content": "x", "engine": "g", "score": 1.0}]

        seen_urls = []

        def capture_rerank(query, results, top_k=15, exclude_urls=None):
            seen_urls.extend(r["url"] for r in results)
            return results[:top_k]

        with patch("lib.expand.expand", return_value=["q1", "q2"]), \
             patch("lib.search.search", side_effect=dupe_search), \
             patch("lib.rerank.rerank", side_effect=capture_rerank):
            lib.pipeline.gather_sources("seed")

        self.assertEqual(seen_urls.count(shared_url), 1, "shared URL must appear only once before reranking")


if __name__ == "__main__":
    unittest.main()
