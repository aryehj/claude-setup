"""
HTTP fetch with structured error handling.
Proxy env vars (HTTP_PROXY / HTTPS_PROXY) are honoured automatically by requests.
"""
import time

import requests


class FetchError(Exception):
    """Raised on 4xx/5xx, timeout, connection error, or proxy denial."""


def fetch(url: str, timeout_s: int = 30) -> tuple[str, dict]:
    """
    GET url and return (body, meta).

    meta keys: status, final_url, latency_s, bytes
    Raises FetchError on HTTP errors, timeouts, or connection failures.
    """
    t0 = time.monotonic()
    try:
        resp = requests.get(url, timeout=timeout_s)
        resp.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise FetchError(f"timeout after {timeout_s}s: {url}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise FetchError(f"connection error: {url}") from exc
    except requests.exceptions.HTTPError as exc:
        raise FetchError(f"HTTP {exc.response.status_code if exc.response is not None else '?'}: {url}") from exc
    except Exception as exc:
        raise FetchError(f"fetch failed: {url}: {exc}") from exc

    latency = time.monotonic() - t0
    meta = {
        "status": resp.status_code,
        "final_url": resp.url,
        "latency_s": round(latency, 3),
        "bytes": len(resp.content),
    }
    return resp.text, meta
