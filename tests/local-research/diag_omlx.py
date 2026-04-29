"""
Diagnose why /v1/chat/completions returns 404 from inside the runner container
even though it works from the macOS host.

Run via:
    ./tests/local-research/diag_omlx.sh
"""
import os
import socket
import requests


def main() -> None:
    api_key = os.environ.get("OMLX_API_KEY", "")
    if not api_key:
        print("OMLX_API_KEY is not set inside the container.")
        return

    hdr = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    print("== DNS resolution ==")
    try:
        ip = socket.gethostbyname("host.docker.internal")
        print(f"host.docker.internal -> {ip}")
    except Exception as exc:
        print(f"DNS error: {exc}")

    print("\n== Proxy env vars ==")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        print(f"  {k}={os.environ.get(k, '')!r}")

    url_models = "http://host.docker.internal:8000/v1/models"
    url_chat = "http://host.docker.internal:8000/v1/chat/completions"
    payload = {
        "model": "gemma-4-E4B-it-MLX-8bit",
        "messages": [{"role": "user", "content": "hi"}],
    }

    for label, kwargs in [("no-proxy", {"proxies": {"http": "", "https": ""}}), ("default", {})]:
        print(f"\n== GET /v1/models ({label}) ==")
        try:
            r = requests.get(url_models, headers=hdr, timeout=10, **kwargs)
            print(f"status={r.status_code}")
            print(f"body={r.text[:300]}")
        except Exception as exc:
            print(f"error={type(exc).__name__}: {exc}")

        print(f"\n== POST /v1/chat/completions ({label}) ==")
        try:
            r = requests.post(url_chat, headers=hdr, json=payload, timeout=30, **kwargs)
            print(f"status={r.status_code}")
            print(f"body={r.text[:300]}")
        except Exception as exc:
            print(f"error={type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
