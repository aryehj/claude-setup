"""
Minimal single-round pipeline driver.
Phase 6 replaces this with the full interactive multi-round CLI.
"""
import sys
import json

from lib.pipeline import gather_sources


def main() -> None:
    if not sys.argv[1:]:
        print("usage: python -m lib.cli <query>", file=sys.stderr)
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"==> gather_sources: {query!r}", flush=True)

    result = gather_sources(query)

    print(f"\n==> Expansions ({len(result['expansions'])}):")
    for i, q in enumerate(result["expansions"]):
        print(f"  [{i}] {q}")

    print(f"\n==> Raw results before rerank: {len(result['raw_results'])}")

    print(f"\n==> Ranked top-{len(result['ranked'])}:")
    for i, r in enumerate(result["ranked"], 1):
        score = r.get("rerank_score", 0.0)
        print(f"  {i:2d}. [{score:.3f}] {r.get('url', '')}")
        print(f"        {r.get('title', '')}")

    print(f"\n==> Timings: {json.dumps(result['timings'])}")


if __name__ == "__main__":
    main()
