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
    def _fake_search(self, query, n=20, categories=None, engines=None, pages=1):
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

        def dupe_search(query, n=20, categories=None, engines=None, pages=1):
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


# ---------------------------------------------------------------------------
# Phase 3 — lib/fetch.py
# ---------------------------------------------------------------------------

import lib.fetch


class TestFetch(unittest.TestCase):
    def test_success_returns_body_and_meta(self):
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>hello</body></html>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.elapsed.total_seconds.return_value = 0.1
        mock_resp.content = b"<html><body>hello</body></html>"
        mock_resp.raise_for_status.return_value = None

        with patch("lib.fetch.requests.get", return_value=mock_resp):
            body, meta = lib.fetch.fetch("https://example.com")

        self.assertIsInstance(body, str)
        self.assertIn("hello", body)
        self.assertEqual(meta["status"], 200)
        self.assertIn("final_url", meta)
        self.assertIn("latency_s", meta)
        self.assertIn("bytes", meta)

    def test_raises_on_4xx(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = Exception("404 Not Found")

        with patch("lib.fetch.requests.get", return_value=mock_resp):
            with self.assertRaises(lib.fetch.FetchError):
                lib.fetch.fetch("https://example.com/missing")

    def test_raises_on_timeout(self):
        import requests as _req
        with patch("lib.fetch.requests.get", side_effect=_req.exceptions.Timeout("timed out")):
            with self.assertRaises(lib.fetch.FetchError):
                lib.fetch.fetch("https://slow.example.com")

    def test_meta_bytes_matches_content_length(self):
        content = b"hello world"
        mock_resp = MagicMock()
        mock_resp.text = "hello world"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.elapsed.total_seconds.return_value = 0.05
        mock_resp.content = content
        mock_resp.raise_for_status.return_value = None

        with patch("lib.fetch.requests.get", return_value=mock_resp):
            _, meta = lib.fetch.fetch("https://example.com")

        self.assertEqual(meta["bytes"], len(content))


# ---------------------------------------------------------------------------
# Phase 3 — lib/extract.py
# ---------------------------------------------------------------------------

import lib.extract


class TestExtract(unittest.TestCase):
    def test_returns_string(self):
        html = "<html><body><p>This is a test article with enough content to extract properly.</p></body></html>"
        result = lib.extract.extract(html, "https://example.com")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_fallback_fires_on_none_from_trafilatura(self):
        html = "<html><head><title>Page Title</title></head><body><script>var x = 1;</script></body></html>"
        with patch("lib.extract.trafilatura.extract", return_value=None):
            result = lib.extract.extract(html, "https://js-heavy.example.com")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_fallback_fires_when_short_result(self):
        html = "<html><head><title>Short</title></head><body><p>tiny</p></body></html>"
        with patch("lib.extract.trafilatura.extract", return_value="x" * 10):
            result = lib.extract.extract(html, "https://short.example.com")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_tags_stripped_in_fallback(self):
        html = "<html><head><title>Test</title></head><body><p>Clean text here.</p><script>bad()</script></body></html>"
        with patch("lib.extract.trafilatura.extract", return_value=None):
            result = lib.extract.extract(html, "https://example.com")
        self.assertNotIn("<p>", result)
        self.assertNotIn("<script>", result)


# ---------------------------------------------------------------------------
# Phase 3 — lib/notes.py
# ---------------------------------------------------------------------------

import lib.notes


class TestNotes(unittest.TestCase):
    def test_returns_string(self):
        with patch("lib.omlx.chat", return_value="- Bullet one\n- Bullet two"):
            result = lib.notes.note_for_source(
                query="test query",
                source_text="Some source text about the topic.",
                meta={"url": "https://example.com", "title": "Example"},
            )
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_query_in_prompt(self):
        captured = {}

        def capture_chat(model, messages, **kw):
            captured["messages"] = messages
            return "- Note"

        with patch("lib.omlx.chat", side_effect=capture_chat):
            lib.notes.note_for_source(
                query="medial knee pain cyclists",
                source_text="Article text",
                meta={"url": "https://x.com", "title": "X"},
            )

        full_prompt = " ".join(m["content"] for m in captured["messages"])
        self.assertIn("medial knee pain cyclists", full_prompt)

    def test_source_text_truncated_to_budget(self):
        long_text = "word " * 10000
        captured = {}

        def capture_chat(model, messages, **kw):
            captured["messages"] = messages
            return "- Note"

        with patch("lib.omlx.chat", side_effect=capture_chat):
            lib.notes.note_for_source(
                query="q",
                source_text=long_text,
                meta={"url": "https://x.com", "title": "X"},
            )

        full_prompt = " ".join(m["content"] for m in captured["messages"])
        self.assertLess(len(full_prompt), len(long_text), "prompt must be shorter than untruncated text")


# ---------------------------------------------------------------------------
# Phase 3 — lib/bundle.py
# ---------------------------------------------------------------------------

import tempfile
import pathlib
import lib.bundle


class TestBundle(unittest.TestCase):
    def test_write_source_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            lib.bundle.write_source(
                round_dir=round_dir,
                idx=1,
                url="https://example.com/article",
                title="Example Article",
                round_idx=1,
                branch_label="main",
                engine="google",
                rerank_score=0.85,
                note="- Key finding here",
                extracted_text="Full article text goes here.",
                timings={"fetch_s": 0.5, "extract_s": 0.1, "note_s": 2.3},
                model="gemma-4-26b-a4b-it-8bit",
            )
            sources_dir = round_dir / "sources"
            files = list(sources_dir.glob("*.md"))
            self.assertEqual(len(files), 1)

    def test_write_source_has_yaml_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            lib.bundle.write_source(
                round_dir=round_dir,
                idx=1,
                url="https://example.com/article",
                title="Example Article",
                round_idx=1,
                branch_label="main",
                engine="google",
                rerank_score=0.85,
                note="- Key finding here",
                extracted_text="Full article text.",
                timings={"fetch_s": 0.5},
                model="gemma-4-26b-a4b-it-8bit",
            )
            sources_dir = round_dir / "sources"
            content = list(sources_dir.glob("*.md"))[0].read_text()
            self.assertTrue(content.startswith("---"), "file must start with YAML frontmatter")
            self.assertIn("url:", content)

    def test_write_source_has_note_and_extracted_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            lib.bundle.write_source(
                round_dir=round_dir,
                idx=1,
                url="https://example.com/article",
                title="Example Article",
                round_idx=1,
                branch_label="main",
                engine="google",
                rerank_score=0.85,
                note="- Key finding here",
                extracted_text="Clean prose text here.",
                timings={"fetch_s": 0.5},
                model="gemma-4-26b-a4b-it-8bit",
            )
            sources_dir = round_dir / "sources"
            content = list(sources_dir.glob("*.md"))[0].read_text()
            self.assertIn("Key finding here", content)
            self.assertIn("Clean prose text here.", content)

    def test_write_source_slug_from_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            lib.bundle.write_source(
                round_dir=round_dir,
                idx=3,
                url="https://pubmed.ncbi.nlm.nih.gov/12345",
                title="Pubmed Article",
                round_idx=1,
                branch_label="main",
                engine="google",
                rerank_score=0.7,
                note="- note",
                extracted_text="text",
                timings={},
                model="m",
            )
            sources_dir = round_dir / "sources"
            files = list(sources_dir.glob("*.md"))
            self.assertEqual(len(files), 1)
            # idx=3 → filename starts with "03-"
            self.assertTrue(files[0].name.startswith("03-"))


# ---------------------------------------------------------------------------
# Phase 3 — pipeline.fetch_and_note
# ---------------------------------------------------------------------------


class TestFetchAndNote(unittest.TestCase):
    def _make_ranked(self, n=3):
        return [
            {
                "url": f"https://source{i}.example.com/",
                "title": f"Source {i}",
                "content": f"Content {i}",
                "engine": "google",
                "score": 1.0,
                "rerank_score": 0.8,
            }
            for i in range(n)
        ]

    def test_returns_source_metas_for_success(self):
        ranked = self._make_ranked(3)
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            with patch("lib.fetch.fetch", return_value=("<html>hello</html>", {"status": 200, "final_url": "https://x.com", "latency_s": 0.1, "bytes": 5})), \
                 patch("lib.extract.extract", return_value="Extracted text here"), \
                 patch("lib.notes.note_for_source", return_value="- Key fact"), \
                 patch("lib.bundle.write_source", return_value=pathlib.Path(tmpdir) / "file.md"):
                metas = lib.pipeline.fetch_and_note(ranked, round_dir, round_idx=1, branch_label="main")

        self.assertEqual(len(metas), 3)

    def test_failure_does_not_abort_batch(self):
        ranked = self._make_ranked(3)

        def fetch_side_effect(url, **kw):
            if "source1" in url:
                raise lib.fetch.FetchError("404 Not Found")
            return ("<html>ok</html>", {"status": 200, "final_url": url, "latency_s": 0.1, "bytes": 2})

        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            with patch("lib.fetch.fetch", side_effect=fetch_side_effect), \
                 patch("lib.extract.extract", return_value="text"), \
                 patch("lib.notes.note_for_source", return_value="- note"), \
                 patch("lib.bundle.write_source", return_value=pathlib.Path(tmpdir) / "file.md"):
                metas = lib.pipeline.fetch_and_note(ranked, round_dir, round_idx=1, branch_label="main")

        # 3 sources tried, 1 failed — should still return 3 entries (with error flag)
        self.assertEqual(len(metas), 3)

    def test_failure_recorded_in_meta(self):
        ranked = self._make_ranked(2)

        def fetch_side_effect(url, **kw):
            raise lib.fetch.FetchError("timeout")

        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            with patch("lib.fetch.fetch", side_effect=fetch_side_effect):
                metas = lib.pipeline.fetch_and_note(ranked, round_dir, round_idx=1, branch_label="main")

        for m in metas:
            self.assertIn("error", m)
            self.assertIsNotNone(m["error"])

    def test_success_meta_has_expected_keys(self):
        ranked = self._make_ranked(1)
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            with patch("lib.fetch.fetch", return_value=("<html>x</html>", {"status": 200, "final_url": "https://x.com", "latency_s": 0.1, "bytes": 3})), \
                 patch("lib.extract.extract", return_value="text"), \
                 patch("lib.notes.note_for_source", return_value="- note"), \
                 patch("lib.bundle.write_source", return_value=pathlib.Path(tmpdir) / "file.md"):
                metas = lib.pipeline.fetch_and_note(ranked, round_dir, round_idx=1, branch_label="main")

        m = metas[0]
        for key in ("url", "title", "round_idx", "branch_label", "error"):
            self.assertIn(key, m)


if __name__ == "__main__":
    unittest.main()
