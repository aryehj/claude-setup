#!/usr/bin/env python3
"""Walk-back verification for the research.py debug-saga compromises.

Run on the macOS host. Exercises four tests:
  T1  swap claim — does upstream hagezi domains/pro.txt actually omit the apex
      that wildcard/pro-onlydomains.txt covers?
  T2  6 explicit Google ad apex additions — actually absent from cached feeds,
      or redundant with what hagezi already ships?
  T3  TIF re-enable memory claim — Squid RSS at the current denylist size,
      observed directly inside the research VM.
  T4  HTTP_PROXY-on-Vane — the env var is set on the running test container,
      and Squid access.log shows scrape CONNECTs originating from Vane.

Stdlib only. No curl/wget. All shell-out goes through subprocess.run with
argv lists (no shell expansion, no quoting hazards). Output is one section
per test with an explicit pass/fail/inconclusive verdict line.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import List, Tuple

CACHE_DIR = Path.home() / ".research" / "denylist-cache"
HAGEZI_BASE = (
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/"
    "b3c6077683c41884beff8b1a7678d09a9c5e9e00"
)
SWAP_PROBES = ["googlesyndication.com", "googleadservices.com", "doubleclick.net"]
ADDITIONS = [
    "doubleclick.net",
    "googleadservices.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adservice.google.com",
]


def section(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{title}\n{bar}")


def verdict(label: str, msg: str) -> None:
    print(f"  -> {label}: {msg}")


def run(argv: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError as e:
        return 127, "", f"{e}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def vm_sh(snippet: str, timeout: int = 30) -> Tuple[int, str, str]:
    """Run a shell snippet inside the research Colima VM."""
    return run(["colima", "ssh", "-p", "research", "--", "sh", "-c", snippet], timeout)


def http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode("utf-8", errors="replace")


def t1_swap_claim() -> None:
    section("T1: did upstream domains/pro.txt actually omit the apex?")
    print(f"  fetching {HAGEZI_BASE}/domains/pro.txt …")
    try:
        body = http_get(f"{HAGEZI_BASE}/domains/pro.txt")
    except Exception as e:
        verdict("inconclusive", f"fetch failed: {e}")
        return
    lines = {ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")}
    print(f"  {len(lines)} entries in old domains/pro.txt")
    any_evidence = False
    for d in SWAP_PROBES:
        apex = d in lines
        subs = sum(1 for l in lines if l.endswith("." + d))
        marker = "APEX-PRESENT" if apex else "apex-absent"
        print(f"    {d}: {marker}, {subs} subdomain entries")
        if not apex and subs > 0:
            any_evidence = True
    if any_evidence:
        verdict("supports swap", "at least one probe has subdomains but no apex in domains/pro.txt — Squid would leak the apex")
    else:
        verdict("does not support swap", "every probe either had its apex listed or had no entries at all; the leak claim isn't reproduced")


def t2_additions() -> None:
    section("T2: are the 6 explicit Google apex additions absent from cached feeds?")
    if not CACHE_DIR.is_dir():
        verdict("inconclusive", f"cache dir missing: {CACHE_DIR}")
        return
    cached: dict[str, set[str]] = {}
    for f in sorted(CACHE_DIR.glob("*.txt")):
        cached[f.name] = {
            ln.strip() for ln in f.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        }
    print(f"  cached feeds: {', '.join(cached) or '(none)'}")
    real_gaps = 0
    redundant = 0
    for d in ADDITIONS:
        in_files = [name for name, lines in cached.items() if d in lines]
        if in_files:
            print(f"    {d}: PRESENT in {','.join(in_files)} -> addition is redundant")
            redundant += 1
        else:
            print(f"    {d}: absent from all cached feeds -> addition is real coverage")
            real_gaps += 1
    verdict("summary", f"{real_gaps} real, {redundant} redundant of {len(ADDITIONS)} additions")


def t3_squid_size() -> None:
    section("T3: Squid memory + denylist size in the research VM")
    rc, out, err = vm_sh(
        "wc -l /etc/squid/denylist.txt 2>&1; "
        "echo '---'; "
        "ps -o pid,rss,cmd -C squid 2>&1 || ps -ef | grep -i [s]quid"
    )
    print(out.rstrip() if out else "")
    if err.strip():
        print(f"  stderr: {err.rstrip()}")
    if rc != 0:
        verdict("inconclusive", f"colima ssh exit {rc}")
    else:
        verdict("captured", "compare RSS to VM memory; under ~200MB means TIF re-enable is justified at default size")


def t4_http_proxy() -> None:
    section("T4a: HTTP_PROXY env vars on Vane test container")
    found = False
    for name in ("research-vane-test", "research-vane"):
        rc, out, err = vm_sh(f"docker exec {name} env 2>/dev/null | grep -i proxy")
        if rc == 0 and out.strip():
            found = True
            print(f"  {name}:")
            for line in out.splitlines():
                print(f"    {line}")
            break
        else:
            print(f"  {name}: not running or no proxy vars")
    if not found:
        verdict("inconclusive", "no Vane container with proxy env found")

    section("T4b: recent Squid access.log entries (scrape CONNECTs from Vane?)")
    rc, out, err = vm_sh("sudo tail -n 80 /var/log/squid/access.log 2>&1")
    print(out.rstrip() if out else "")
    if err.strip():
        print(f"  stderr: {err.rstrip()}")
    if rc != 0:
        verdict("inconclusive", f"could not read access.log (exit {rc})")
        return
    lines = [l for l in out.splitlines() if l.strip()]
    connect_count = sum(1 for l in lines if " CONNECT " in l)
    denied_count = sum(1 for l in lines if "TCP_DENIED" in l)
    verdict(
        "summary",
        f"{len(lines)} log lines, {connect_count} CONNECTs (HTTPS scrapes), {denied_count} TCP_DENIED (denylist hits)",
    )
    if connect_count == 0:
        print("  note: zero CONNECTs means either no recent queries OR Vane bypassed Squid; run a few quality-mode queries and re-run T4")


def preflight() -> bool:
    if shutil.which("colima") is None:
        print("FATAL: colima not on PATH; run on macOS host", file=sys.stderr)
        return False
    return True


def main() -> int:
    if not preflight():
        return 2
    t1_swap_claim()
    t2_additions()
    t3_squid_size()
    t4_http_proxy()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
