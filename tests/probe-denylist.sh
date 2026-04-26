#!/usr/bin/env bash
# Probe the research VM Squid denylist end-to-end.
# Runs from the macOS host. Two known-good and two known-blocked URLs.
set -u

read -r -d '' PY <<'PY'
import urllib.request
p = "http://172.17.0.1:8888"
o = urllib.request.build_opener(urllib.request.ProxyHandler({"https": p, "http": p}))
urls = [
    ("allow", "https://example.com/"),
    ("allow", "https://api.github.com/zen"),
    ("deny",  "https://googlesyndication.com/"),  # in hagezi pro
    ("deny",  "https://doubleclick.net/"),         # hagezi omits — needs additions
    ("deny",  "https://googleadservices.com/"),    # hagezi omits — needs additions
]
for kind, u in urls:
    try:
        r = o.open(u, timeout=8)
        print(f"{kind:5s} {u} -> {r.status}")
    except Exception as e:
        print(f"{kind:5s} {u} -> {type(e).__name__}: {e}")
PY

printf '%s' "$PY" | colima ssh -p research -- docker exec -i research-searxng python3
