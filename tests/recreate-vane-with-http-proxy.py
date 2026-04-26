#!/usr/bin/env python3
"""Recreate research-vane with HTTP_PROXY added, to test ADR-027's claim.

Stops + removes the existing research-vane container, then starts a new one
with all three proxy env vars (HTTP_PROXY, HTTPS_PROXY, NO_PROXY). Same image,
same network, same volume, same port — the only difference is HTTP_PROXY is
now set.

After this script exits successfully, drive a quality-mode query in the
browser at http://localhost:3000, then re-run tests/walkback-checks.py to
inspect Squid access.log for fresh CONNECTs from research-vane (172.18.0.3).

If queries hang at "searching" indefinitely, ADR-027's regression reproduces
and the omission stands. If queries complete and new CONNECTs appear in the
log, the omission was wrong-Vane — re-add HTTP_PROXY to ensure_vane_container.

Run on the macOS host. No shell escaping (everything goes through argv).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VANE_DATA = Path.home() / ".research" / "vane-data"
SQUID_PROXY = "http://172.17.0.1:8888"
NO_PROXY = "research-searxng,host.docker.internal,localhost,127.0.0.1"
SEARXNG_URL = "http://research-searxng:8080"
IMAGE = "docker.io/itzcrazykns1337/vane:slim-latest"


def colima(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["colima", "ssh", "-p", "research", "--", *argv],
        capture_output=True, text=True,
    )


def main() -> int:
    if not VANE_DATA.is_dir():
        print(f"FATAL: {VANE_DATA} does not exist", file=sys.stderr)
        return 2

    print("==> Stopping and removing existing research-vane (if any)")
    rm = colima("docker", "rm", "-f", "research-vane")
    print(f"    {rm.stdout.strip() or rm.stderr.strip() or '(no output)'}")

    print("==> Starting research-vane with HTTP_PROXY + HTTPS_PROXY + NO_PROXY")
    run = colima(
        "docker", "run", "-d",
        "--name", "research-vane",
        "--network", "research-net",
        "--add-host", "host.docker.internal:host-gateway",
        "-p", "3000:3000",
        "-e", f"SEARXNG_API_URL={SEARXNG_URL}",
        "-e", f"HTTP_PROXY={SQUID_PROXY}",
        "-e", f"HTTPS_PROXY={SQUID_PROXY}",
        "-e", f"NO_PROXY={NO_PROXY}",
        "-v", f"{VANE_DATA}:/home/vane/data",
        IMAGE,
    )
    if run.returncode != 0:
        print(f"FATAL: docker run failed (exit {run.returncode})", file=sys.stderr)
        print(run.stderr, file=sys.stderr)
        return run.returncode
    cid = run.stdout.strip()
    print(f"    container id: {cid[:12]}")

    print("==> Verifying proxy env vars are set on the new container")
    env = colima("docker", "exec", "research-vane", "sh", "-c", "env | grep -i proxy | sort")
    print(env.stdout.rstrip() or env.stderr.rstrip())

    print()
    print("Next steps:")
    print("  1. Open http://localhost:3000 and run a quality-mode query.")
    print("  2. Watch for the UI hang at 'searching N queries' (= ADR-027 reproduces)")
    print("     vs. successful answer with scrapes (= ADR-027 was wrong-Vane).")
    print("  3. Re-run ./tests/walkback-checks.py and check T4b CONNECT count.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
