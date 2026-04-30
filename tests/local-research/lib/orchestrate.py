"""
Top-level round orchestrator for the research pipeline.

research(seed_query, session_dir, continuation) drives the loop:
  branch proposal → gather_sources (exclude seen URLs) → fetch+notes → digest
  → update state → call continuation(state, round_dir) → repeat if True.

The continuation callable is injected by the CLI (interactive gate) or by
batch.should_stop (Phase 7).  The forced-2-round test driver passes a callable
that returns False after 2 rounds.
"""
import pathlib
import sys
import time

from lib import branch as _branch_mod
from lib import digest as _digest_mod
from lib import pipeline as _pipeline_mod
from lib.round_state import RoundState


def research(
    seed_query: str,
    session_dir: pathlib.Path,
    continuation,
) -> RoundState:
    """
    Drive the iterative research loop until continuation returns False.

    continuation(state: RoundState, round_dir: Path) -> bool
      Return True to run another round, False to stop.

    Returns the final RoundState (also pickled after each round).
    """
    session_dir = pathlib.Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    # Write query.md at session root.
    (session_dir / "query.md").write_text(
        f"# Research query\n\n{seed_query}\n", encoding="utf-8"
    )

    state = RoundState()

    while True:
        # ── Branch proposal ──────────────────────────────────────────────────
        digest_texts = [p.read_text(encoding="utf-8") for p in state.digest_paths]
        branches = _branch_mod.propose_branches(seed_query, digest_texts)

        for b in branches:
            state.add_branch(state.round_count + 1, b["query"], b.get("rationale", ""))

        # ── Gather sources across branches (excluding seen URLs) ─────────────
        all_ranked: list[dict] = []
        for b in branches:
            result = _pipeline_mod.gather_sources(
                b["query"],
                exclude_urls=state.seen_urls,
            )
            all_ranked.extend(result["ranked"])
            print(
                f"  branch {b['query']!r}: {len(result['ranked'])} ranked",
                file=sys.stderr,
            )

        # Dedupe across branches by URL (preserve first occurrence).
        seen_this_round: set[str] = set()
        deduped_ranked: list[dict] = []
        for r in all_ranked:
            url = r.get("url", "")
            if url and url not in seen_this_round and url not in state.seen_urls:
                seen_this_round.add(url)
                deduped_ranked.append(r)

        # ── Increment round counter and set up directory ─────────────────────
        state.increment_round()
        round_dir = session_dir / "rounds" / f"{state.round_count:02d}"
        round_dir.mkdir(parents=True, exist_ok=True)

        print(
            f"\n==> Round {state.round_count}: {len(deduped_ranked)} unique sources to fetch",
            file=sys.stderr,
        )

        # ── Fetch + extract + notes ──────────────────────────────────────────
        branch_label = branches[0]["query"] if branches else seed_query
        source_metas = _pipeline_mod.fetch_and_note(
            deduped_ranked,
            round_dir=round_dir,
            round_idx=state.round_count,
            branch_label=branch_label,
            query=seed_query,
        )

        # Register all successfully fetched sources in state.
        for meta in source_metas:
            if not meta.get("error"):
                state.add_source(meta)

        # ── Per-round digest ─────────────────────────────────────────────────
        digest_path = _digest_mod.digest_round(
            round_idx=state.round_count,
            source_metas=source_metas,
            round_dir=round_dir,
            seed_query=seed_query,
        )
        state.add_digest_path(state.round_count, digest_path)

        # ── Persist state ────────────────────────────────────────────────────
        pkl_path = session_dir / "state.pkl"
        state.save(pkl_path)

        print(
            f"==> Round {state.round_count} done. "
            f"Accumulated sources: {len(state.accumulated_sources)}",
            file=sys.stderr,
        )

        # ── Continuation check ───────────────────────────────────────────────
        if not continuation(state, round_dir):
            break

    return state
