"""
Unit + integration tests for Phase 4 orchestration modules.

All tests use fixture data and mocks — no live services required.
The forced 2-round driver at the bottom validates the full orchestration
path with a continuation callable that stops after exactly 2 rounds.
"""
import pathlib
import pickle
import sys
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(__file__))


# ---------------------------------------------------------------------------
# lib/round_state.py — URL canonicalization and accumulation
# ---------------------------------------------------------------------------

import lib.round_state as rs


class TestURLCanonicalization(unittest.TestCase):
    def canon(self, url):
        return rs.canonicalize_url(url)

    def test_strips_utm_params(self):
        url = "https://example.com/page?utm_source=google&utm_medium=cpc&foo=bar"
        c = self.canon(url)
        self.assertNotIn("utm_source", c)
        self.assertNotIn("utm_medium", c)
        self.assertIn("foo=bar", c)

    def test_strips_srsltid(self):
        url = "https://incrediwear.com/product?srsltid=abc123&color=red"
        c = self.canon(url)
        self.assertNotIn("srsltid", c)
        self.assertIn("color=red", c)

    def test_strips_fbclid_gclid_ref(self):
        for param in ("fbclid", "gclid", "ref"):
            url = f"https://example.com/page?{param}=xyz"
            c = self.canon(url)
            self.assertNotIn(param, c, f"{param} should be stripped")

    def test_drops_fragment(self):
        url = "https://example.com/article#section-3"
        c = self.canon(url)
        self.assertNotIn("#", c)

    def test_lowercase_scheme_and_host(self):
        url = "HTTPS://Example.COM/Path"
        c = self.canon(url)
        self.assertTrue(c.startswith("https://example.com/"))

    def test_normalizes_percent_encoding(self):
        url = "https://physiopedia.com/Knee%27s_Health"
        c = self.canon(url)
        # %27 → apostrophe; result must not have %27
        self.assertNotIn("%27", c)

    def test_same_url_different_srsltid_matches(self):
        u1 = "https://incrediwear.com/product?srsltid=aaa"
        u2 = "https://incrediwear.com/product?srsltid=bbb"
        self.assertEqual(self.canon(u1), self.canon(u2))

    def test_physiopedia_percent_vs_literal(self):
        u1 = "https://physiopedia.com/Knee%27s_Health"
        u2 = "https://physiopedia.com/Knee's_Health"
        self.assertEqual(self.canon(u1), self.canon(u2))

    def test_preserves_path(self):
        url = "https://example.com/deep/path/article"
        c = self.canon(url)
        self.assertIn("/deep/path/article", c)


class TestRoundState(unittest.TestCase):
    def test_initial_state(self):
        state = rs.RoundState()
        self.assertEqual(state.round_count, 0)
        self.assertEqual(state.accumulated_sources, [])
        self.assertEqual(len(state.seen_urls), 0)

    def test_add_source_registers_canonical_url(self):
        state = rs.RoundState()
        meta = {"url": "https://example.com/page?utm_source=google", "round_idx": 1, "branch_label": "main"}
        state.add_source(meta)
        self.assertEqual(len(state.accumulated_sources), 1)
        # canonicalized form (no utm_source) must be in seen_urls
        self.assertTrue(any("utm_source" not in u for u in state.seen_urls))

    def test_seen_url_excludes_duplicates(self):
        state = rs.RoundState()
        state.add_source({"url": "https://example.com/?srsltid=aaa", "round_idx": 1, "branch_label": "x"})
        state.add_source({"url": "https://example.com/?srsltid=bbb", "round_idx": 1, "branch_label": "x"})
        # Both canonicalize to the same URL, so only one should appear
        self.assertEqual(len(state.accumulated_sources), 1)

    def test_seen_urls_property_contains_canonical_form(self):
        state = rs.RoundState()
        state.add_source({"url": "https://example.com/page?fbclid=123", "round_idx": 1, "branch_label": "x"})
        # seen_urls should not contain the raw URL's tracking param
        for u in state.seen_urls:
            self.assertNotIn("fbclid", u)

    def test_increment_round(self):
        state = rs.RoundState()
        state.increment_round()
        self.assertEqual(state.round_count, 1)
        state.increment_round()
        self.assertEqual(state.round_count, 2)

    def test_add_digest_path(self):
        state = rs.RoundState()
        p = pathlib.Path("/sessions/test/rounds/01/digest.md")
        state.add_digest_path(1, p)
        self.assertIn(p, state.digest_paths)

    def test_add_branch(self):
        state = rs.RoundState()
        state.add_branch(1, "saphenous nerve compression cycling")
        self.assertEqual(len(state.branch_history), 1)
        self.assertEqual(state.branch_history[0]["query"], "saphenous nerve compression cycling")

    def test_pickle_roundtrip(self):
        state = rs.RoundState()
        state.add_source({"url": "https://example.com/a", "round_idx": 1, "branch_label": "main"})
        state.increment_round()
        data = pickle.dumps(state)
        restored = pickle.loads(data)
        self.assertEqual(restored.round_count, 1)
        self.assertEqual(len(restored.accumulated_sources), 1)
        self.assertEqual(len(restored.seen_urls), len(state.seen_urls))

    def test_pickle_to_file(self):
        state = rs.RoundState()
        state.increment_round()
        with tempfile.TemporaryDirectory() as tmpdir:
            pkl_path = pathlib.Path(tmpdir) / "state.pkl"
            state.save(pkl_path)
            self.assertTrue(pkl_path.exists())
            loaded = rs.RoundState.load(pkl_path)
            self.assertEqual(loaded.round_count, 1)


# ---------------------------------------------------------------------------
# lib/branch.py — propose_branches
# ---------------------------------------------------------------------------

import lib.branch as branch_mod


class TestProposeBranches(unittest.TestCase):
    # Non-empty digests are required to reach the LLM path (round 1 with no digests
    # returns the seed query directly without calling the model — see plan Phase 4 step 2).
    _DUMMY_DIGESTS = ["Round 1 digest: some prior findings about knee pain."]

    def test_returns_list_of_dicts_with_query_and_rationale(self):
        raw = (
            "saphenous nerve compression cycling | medial knee pain often caused by nerve\n"
            "training-load progression knee pain | overuse injury from mileage spikes\n"
            "iliotibial vs medial meniscus diagnosis | differential important for treatment\n"
        )
        with patch("lib.omlx.chat", return_value=raw):
            results = branch_mod.propose_branches("medial knee pain cyclists", self._DUMMY_DIGESTS, k=3)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertIn("query", r)
            self.assertIn("rationale", r)

    def test_query_field_populated(self):
        raw = "nerve entrapment cyclists | saphenous nerve territory\n"
        with patch("lib.omlx.chat", return_value=raw):
            results = branch_mod.propose_branches("knee pain", self._DUMMY_DIGESTS, k=1)
        self.assertEqual(results[0]["query"], "nerve entrapment cyclists")

    def test_tolerant_parser_strips_bullets(self):
        # Parser should handle "- query | rationale" and "1. query | rationale"
        raw = (
            "- saphenous nerve | nerve reason\n"
            "1. training load | overuse reason\n"
            "* meniscus | structural reason\n"
        )
        with patch("lib.omlx.chat", return_value=raw):
            results = branch_mod.propose_branches("knee pain", self._DUMMY_DIGESTS, k=3)
        queries = [r["query"] for r in results]
        self.assertIn("saphenous nerve", queries)
        self.assertIn("training load", queries)
        self.assertIn("meniscus", queries)

    def test_blank_lines_skipped(self):
        raw = "\nsaphenous nerve | reason\n\n\ntraining load | overuse\n"
        with patch("lib.omlx.chat", return_value=raw):
            results = branch_mod.propose_branches("knee pain", self._DUMMY_DIGESTS, k=4)
        self.assertEqual(len(results), 2)

    def test_lines_without_pipe_skipped(self):
        raw = "good line | good rationale\nbad line no pipe\nanother good | rationale"
        with patch("lib.omlx.chat", return_value=raw):
            results = branch_mod.propose_branches("knee pain", self._DUMMY_DIGESTS, k=4)
        queries = [r["query"] for r in results]
        self.assertIn("good line", queries)
        self.assertNotIn("bad line no pipe", queries)
        self.assertIn("another good", queries)

    def test_accumulated_digests_passed_in_prompt(self):
        captured = {}

        def capture_chat(model, messages, **kw):
            captured["messages"] = messages
            return "q1 | r1\nq2 | r2"

        with patch("lib.omlx.chat", side_effect=capture_chat):
            branch_mod.propose_branches(
                "seed query",
                ["Round 1 digest text here.", "Round 2 digest text here."],
                k=2,
            )

        full_prompt = " ".join(m["content"] for m in captured["messages"])
        self.assertIn("Round 1 digest", full_prompt)
        self.assertIn("seed query", full_prompt)

    def test_seed_query_in_prompt(self):
        captured = {}

        def capture_chat(model, messages, **kw):
            captured["messages"] = messages
            return "a | b"

        with patch("lib.omlx.chat", side_effect=capture_chat):
            branch_mod.propose_branches("very specific seed query", self._DUMMY_DIGESTS, k=1)

        full_prompt = " ".join(m["content"] for m in captured["messages"])
        self.assertIn("very specific seed query", full_prompt)


# ---------------------------------------------------------------------------
# lib/digest.py — digest_round
# ---------------------------------------------------------------------------

import lib.digest as digest_mod


class TestDigestRound(unittest.TestCase):
    def test_creates_digest_file(self):
        source_metas = [
            {"url": "https://a.com", "title": "A", "round_idx": 1, "branch_label": "main",
             "file_path": "/rounds/01/sources/01-a.md", "error": None},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            round_dir.mkdir(parents=True)
            with patch("lib.omlx.chat", return_value="Digest text here."):
                path = digest_mod.digest_round(
                    round_idx=1,
                    source_metas=source_metas,
                    round_dir=round_dir,
                )
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "digest.md")

    def test_digest_content_written(self):
        source_metas = [
            {"url": "https://a.com", "title": "A", "round_idx": 1, "branch_label": "main",
             "file_path": "/rounds/01/sources/01-a.md", "error": None},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            round_dir.mkdir(parents=True)
            digest_text = "This is the digest summary with [r.1] citations."
            with patch("lib.omlx.chat", return_value=digest_text):
                path = digest_mod.digest_round(1, source_metas, round_dir)
            content = path.read_text()
            self.assertIn(digest_text, content)

    def test_source_titles_in_prompt(self):
        captured = {}

        def capture_chat(model, messages, **kw):
            captured["messages"] = messages
            return "Summary text"

        source_metas = [
            {"url": "https://a.com", "title": "Title Alpha", "round_idx": 1, "branch_label": "main",
             "file_path": "/rounds/01/sources/01-a.md", "error": None},
            {"url": "https://b.com", "title": "Title Beta", "round_idx": 1, "branch_label": "main",
             "file_path": "/rounds/01/sources/02-b.md", "error": None},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            round_dir.mkdir(parents=True)
            with patch("lib.omlx.chat", side_effect=capture_chat):
                digest_mod.digest_round(1, source_metas, round_dir)

        full_prompt = " ".join(m["content"] for m in captured["messages"])
        self.assertIn("Title Alpha", full_prompt)
        self.assertIn("Title Beta", full_prompt)

    def test_failed_sources_excluded_from_prompt(self):
        captured = {}

        def capture_chat(model, messages, **kw):
            captured["messages"] = messages
            return "Summary"

        source_metas = [
            {"url": "https://ok.com", "title": "OK Source", "round_idx": 1, "branch_label": "main",
             "file_path": "/rounds/01/sources/01-ok.md", "error": None},
            {"url": "https://fail.com", "title": "Failed Source", "round_idx": 1, "branch_label": "main",
             "file_path": None, "error": "timeout"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            round_dir.mkdir(parents=True)
            with patch("lib.omlx.chat", side_effect=capture_chat):
                digest_mod.digest_round(1, source_metas, round_dir)

        full_prompt = " ".join(m["content"] for m in captured["messages"])
        self.assertIn("OK Source", full_prompt)
        self.assertNotIn("Failed Source", full_prompt)

    def test_returns_path_object(self):
        source_metas = [
            {"url": "https://a.com", "title": "A", "round_idx": 1, "branch_label": "main",
             "file_path": "/rounds/01/sources/01-a.md", "error": None},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = pathlib.Path(tmpdir) / "rounds" / "01"
            round_dir.mkdir(parents=True)
            with patch("lib.omlx.chat", return_value="text"):
                result = digest_mod.digest_round(1, source_metas, round_dir)
        self.assertIsInstance(result, pathlib.Path)


# ---------------------------------------------------------------------------
# lib/orchestrate.py — forced 2-round driver
# ---------------------------------------------------------------------------

import lib.orchestrate as orch


def _make_source_metas(round_idx, branch_label, urls):
    return [
        {
            "url": u,
            "title": f"Title for {u}",
            "round_idx": round_idx,
            "branch_label": branch_label,
            "engine": "google",
            "rerank_score": 0.9,
            "error": None,
            "file_path": f"/sessions/test/rounds/{round_idx:02d}/sources/01-slug.md",
        }
        for u in urls
    ]


class TestOrchestrateForced2Rounds(unittest.TestCase):
    """
    Full orchestration integration test using mocked inner modules.
    Continuation callable stops after exactly 2 rounds.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session_dir = pathlib.Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_forced_2_rounds(self, seed_query="medial knee pain cyclists"):
        round_call_count = {"n": 0}

        def continuation(state, round_dir):
            """Stop after 2 rounds."""
            return state.round_count < 2

        branches_round1 = [
            {"query": "saphenous nerve compression cycling", "rationale": "nerve territory"},
            {"query": "training load progression knee pain", "rationale": "overuse injury"},
        ]
        branches_round2 = [
            {"query": "iliotibial band friction syndrome", "rationale": "differential"},
        ]

        round1_urls = ["https://r1a.com/", "https://r1b.com/"]
        round2_urls = ["https://r2a.com/", "https://r2b.com/"]

        r1_metas = _make_source_metas(1, "saphenous nerve compression cycling", round1_urls)
        r2_metas = _make_source_metas(2, "iliotibial band friction syndrome", round2_urls)

        ranked_round1 = [
            {"url": u, "title": f"T{i}", "content": "c", "engine": "google",
             "score": 1.0, "rerank_score": 0.9}
            for i, u in enumerate(round1_urls)
        ]
        ranked_round2 = [
            {"url": u, "title": f"T{i}", "content": "c", "engine": "google",
             "score": 1.0, "rerank_score": 0.85}
            for i, u in enumerate(round2_urls)
        ]

        branch_call = {"n": 0}

        def fake_propose_branches(seed_query, accumulated_digests, k=4):
            branch_call["n"] += 1
            if branch_call["n"] == 1:
                return branches_round1
            return branches_round2

        def fake_gather_sources(query, exclude_urls=None, **kw):
            # Return different ranked lists based on round
            if branch_call["n"] == 1:
                ranked = [r for r in ranked_round1 if r["url"] not in (exclude_urls or set())]
            else:
                ranked = [r for r in ranked_round2 if r["url"] not in (exclude_urls or set())]
            return {"query": query, "expansions": [query], "raw_results": ranked, "ranked": ranked, "timings": {}}

        def fake_fetch_and_note(ranked, round_dir, round_idx, branch_label, query=""):
            urls = [r["url"] for r in ranked]
            return _make_source_metas(round_idx, branch_label, urls)

        def fake_digest_round(round_idx, source_metas, round_dir, **kwargs):
            digest_path = round_dir / "digest.md"
            round_dir.mkdir(parents=True, exist_ok=True)
            digest_path.write_text(f"Digest for round {round_idx}")
            return digest_path

        with patch("lib.branch.propose_branches", side_effect=fake_propose_branches), \
             patch("lib.pipeline.gather_sources", side_effect=fake_gather_sources), \
             patch("lib.pipeline.fetch_and_note", side_effect=fake_fetch_and_note), \
             patch("lib.digest.digest_round", side_effect=fake_digest_round):
            state = orch.research(seed_query, self.session_dir, continuation)

        return state

    def test_two_rounds_completed(self):
        state = self._run_forced_2_rounds()
        self.assertEqual(state.round_count, 2)

    def test_round_dirs_created(self):
        self._run_forced_2_rounds()
        r1 = self.session_dir / "rounds" / "01"
        r2 = self.session_dir / "rounds" / "02"
        self.assertTrue(r1.exists(), "rounds/01 must exist")
        self.assertTrue(r2.exists(), "rounds/02 must exist")

    def test_state_pkl_written(self):
        self._run_forced_2_rounds()
        # state.pkl should exist in the session dir or round dirs
        pkl_files = list(self.session_dir.rglob("state.pkl"))
        self.assertGreater(len(pkl_files), 0, "state.pkl must be written")

    def test_cross_round_url_dedupe(self):
        state = self._run_forced_2_rounds()
        # URLs from round 1 must not appear in round 2's sources
        r1_urls = {m["url"] for m in state.accumulated_sources if m["round_idx"] == 1}
        r2_urls = {m["url"] for m in state.accumulated_sources if m["round_idx"] == 2}
        overlap = r1_urls & r2_urls
        self.assertEqual(overlap, set(), f"Cross-round URL overlap: {overlap}")

    def test_accumulated_sources_have_round_and_branch_labels(self):
        state = self._run_forced_2_rounds()
        for m in state.accumulated_sources:
            self.assertIn("round_idx", m)
            self.assertIn("branch_label", m)
            self.assertIsNotNone(m["round_idx"])
            self.assertIsNotNone(m["branch_label"])

    def test_digest_paths_recorded_in_state(self):
        state = self._run_forced_2_rounds()
        self.assertEqual(len(state.digest_paths), 2, "Both round digests must be recorded")

    def test_query_md_written(self):
        self._run_forced_2_rounds("medial knee pain cyclists")
        query_file = self.session_dir / "query.md"
        self.assertTrue(query_file.exists(), "query.md must be written to session dir")
        content = query_file.read_text()
        self.assertIn("medial knee pain cyclists", content)


class TestOrchestrateStatePkl(unittest.TestCase):
    """Verify that state.pkl actually contains accumulated sources with labels."""

    def test_pkl_contains_accumulated_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = pathlib.Path(tmpdir)
            round1_urls = ["https://source1.com/", "https://source2.com/"]
            metas = _make_source_metas(1, "main branch", round1_urls)

            def continuation(state, round_dir):
                return False  # stop after round 1

            with patch("lib.branch.propose_branches", return_value=[{"query": "seed", "rationale": "initial"}]), \
                 patch("lib.pipeline.gather_sources", return_value={
                     "query": "seed", "expansions": ["seed"], "raw_results": [
                         {"url": u, "title": "T", "content": "c", "engine": "g", "score": 1.0, "rerank_score": 0.9}
                         for u in round1_urls
                     ],
                     "ranked": [
                         {"url": u, "title": "T", "content": "c", "engine": "g", "score": 1.0, "rerank_score": 0.9}
                         for u in round1_urls
                     ],
                     "timings": {},
                 }), \
                 patch("lib.pipeline.fetch_and_note", return_value=metas), \
                 patch("lib.digest.digest_round", side_effect=lambda **kw: (kw["round_dir"].mkdir(parents=True, exist_ok=True), kw["round_dir"] / "digest.md")[1]):
                state = orch.research("seed query", session_dir, continuation)

            pkl_files = list(session_dir.rglob("state.pkl"))
            self.assertGreater(len(pkl_files), 0)
            loaded = rs.RoundState.load(pkl_files[-1])
            self.assertGreater(len(loaded.accumulated_sources), 0)
            for m in loaded.accumulated_sources:
                self.assertIn("round_idx", m)
                self.assertIn("branch_label", m)


if __name__ == "__main__":
    unittest.main()
