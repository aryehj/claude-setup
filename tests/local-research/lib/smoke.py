"""
Smoke checks run via `bootstrap.sh --smoke`.
Exits non-zero if any check fails.
"""
import json
import sys

import requests

from lib.config import (
    EMBED_MODEL,
    OMLX_API_KEY,
    SEARXNG_URL,
)
from lib import omlx


def _ok(label: str, detail: str) -> None:
    print(f"  [ok] {label}: {detail}")


def _fail(label: str, detail: str) -> None:
    print(f"  [FAIL] {label}: {detail}", file=sys.stderr)
    sys.exit(1)


def check_omlx_api_key() -> None:
    if not OMLX_API_KEY:
        _fail("OMLX_API_KEY", "env var is unset — set it before running")


def check_searxng() -> None:
    label = "SearXNG"
    try:
        resp = requests.get(f"{SEARXNG_URL}/search?q=test&format=json", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        count = len(data.get("results", []))
        _ok(label, f"{count} results for 'test'")
    except Exception as exc:
        _fail(label, str(exc))


def check_omlx_models() -> None:
    label = "omlx /v1/models"
    try:
        models = omlx.list_models()
    except Exception as exc:
        _fail(label, str(exc))
        return

    chat_models = [m for m in models if m.get("object") == "model" and "embed" not in m["id"].lower()]
    embed_models = [m for m in models if "embed" in m["id"].lower()]

    if not chat_models:
        # Some omlx versions don't set object="model"; just check len
        if len(models) < 1:
            _fail(label, f"no models returned (full list: {[m['id'] for m in models]})")
            return
        chat_models = models

    _ok(label, f"{len(models)} model(s) — chat: {[m['id'] for m in chat_models[:3]]}, embed: {[m['id'] for m in embed_models[:3]]}")


def check_embedder() -> None:
    label = f"embedder ({EMBED_MODEL})"
    try:
        vecs = omlx.embed(EMBED_MODEL, ["test"])
        if not vecs or not vecs[0]:
            _fail(label, "empty vector returned")
            return
        dim = len(vecs[0])
        _ok(label, f"vector dim={dim}, first 3 values={vecs[0][:3]}")
    except Exception as exc:
        _fail(label, str(exc))


def main() -> None:
    print("==> Smoke checks")
    check_omlx_api_key()
    check_searxng()
    check_omlx_models()
    check_embedder()
    print("==> All checks passed")


if __name__ == "__main__":
    main()
