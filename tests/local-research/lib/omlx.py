"""
Shared OpenAI-compatible client for omlx.
All later phases import from here rather than calling requests directly.
"""
import json
from typing import Any

import requests

from lib.config import OMLX_API_KEY, OMLX_BASE_URL

_DEFAULT_TIMEOUT_S = 1200  # 20 min — matches vane-eval POST timeout


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if OMLX_API_KEY:
        h["Authorization"] = f"Bearer {OMLX_API_KEY}"
    return h


def _url(path: str) -> str:
    return OMLX_BASE_URL.rstrip("/") + path


def list_models() -> list[dict]:
    """Return the /v1/models list."""
    resp = requests.get(_url("/models"), headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def chat(model: str, messages: list[dict], **kwargs: Any) -> str:
    """Call /v1/chat/completions and return the assistant message content."""
    payload = {"model": model, "messages": messages, **kwargs}
    resp = requests.post(
        _url("/chat/completions"),
        headers=_headers(),
        data=json.dumps(payload),
        timeout=_DEFAULT_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def embed(model: str, inputs: list[str]) -> list[list[float]]:
    """Call /v1/embeddings and return a list of embedding vectors."""
    payload = {"model": model, "input": inputs}
    resp = requests.post(
        _url("/embeddings"),
        headers=_headers(),
        data=json.dumps(payload),
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [item["embedding"] for item in data]
