"""
Shared helpers for the Vane research-quality eval.

## Resolved unknowns (probed 2026-04-26)

Unknown #4 — omlx /v1/models response shape:
  GET /v1/models → {"object": "list", "data": [{"id": str, "object": "model",
  "created": int, "owned_by": str}, ...]}
  Requires Authorization: Bearer $OMLX_API_KEY.

Unknown #3 — omlx thinking parameter:
  Thinking is server-side config per loaded model, not a per-request toggle.
  The response uses reasoning_content (OpenAI-o1 style), not inline <think> tags.
  Currently loaded models:
    gemma-4-26b-a4b-it-8bit  → reasoning_content populated (thinking ON)
    gemma-4-31b-it-6bit      → reasoning_content null      (thinking OFF)
    gemma-4-E4B-it-MLX-8bit  → reasoning_content null      (thinking OFF)
    nomicai-modernbert-embed-base-bf16 → embedding model, not for chat
  chat_template_kwargs: {"enable_thinking": true} is silently accepted but does
  NOT toggle the server config at request time on Gemma models.
  For the OFAT thinking axis: cell.thinking=True means the caller should route
  to a model with thinking enabled; cell.thinking=False means a model without.
  Per-cell absence of reasoning_content on a thinking=True cell is a data point,
  not a failure — the phase-level human prompt asserts the model configuration.

## Status taxonomy (worst-wins precedence, top to bottom)

  error              — HTTP/transport failure
  error:no-content   — request succeeded but content is empty/whitespace
  warn:truncated     — finish_reason="length" and content non-empty
  warn:reasoning-leaked — thinking=False cell but reasoning_content populated
  ok                 — none of the above
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional


@dataclass
class Cell:
    query_id: str
    model: str
    prompt_style: str
    temperature: float
    thinking: bool
    label: str


def classify_status(cell: "Cell", result: "dict[str, Any]") -> str:
    """Return the status string for a completed cell using worst-wins precedence.

    Ladder (highest severity first):
      error              — HTTP/transport failure
      error:no-content   — request succeeded but content is empty/whitespace
      warn:truncated     — finish_reason="length" and content non-empty
      warn:reasoning-leaked — thinking=False cell but reasoning_content populated
      ok                 — none of the above
    """
    if result["error"]:
        return "error"
    if not result["text"].strip():
        return "error:no-content"
    finish_reason = (
        result.get("raw", {})
        .get("choices", [{}])[0]
        .get("finish_reason", "stop")
    )
    if finish_reason == "length":
        return "warn:truncated"
    if (not cell.thinking) and result["reasoning"] is not None:
        return "warn:reasoning-leaked"
    return "ok"


def discover_omlx_models(base_url: str) -> list[str]:
    """Return model IDs from GET {base_url}/models.

    base_url should end in /v1 (e.g. 'http://host.docker.internal:8000/v1').
    Raises ValueError if the response is not the expected OpenAI-style list.
    Uses OMLX_API_KEY from the environment.
    """
    url = base_url.rstrip("/") + "/models"
    api_key = os.environ.get("OMLX_API_KEY", "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach omlx at {url}: {exc}") from exc

    if not isinstance(data, dict) or "data" not in data:
        raise ValueError(f"Unexpected /v1/models response shape: {data!r}")
    return [m["id"] for m in data["data"] if isinstance(m.get("id"), str)]


_STRUCTURED_HINT = (
    "Answer concisely. Cite key facts inline. "
    "Use bullet points for lists; avoid padding."
)

_RESEARCH_SYSTEM = (
    "You are a meticulous research analyst. Your answers are accurate, well-sourced "
    "in reasoning, and appropriately hedged where facts are uncertain. "
    "Prioritise precision over length. Do not speculate beyond the evidence."
)


def build_prompt(
    query: str,
    style: Literal["bare", "structured", "research_system"],
) -> tuple[Optional[str], str]:
    """Return (system_prompt, user_message) for a given prompt style.

    bare            — no system prompt; user message is the raw query.
    structured      — no system prompt; user message includes a concise format hint.
    research_system — research-analyst system prompt; user message is the raw query.
    """
    if style == "bare":
        return None, query
    if style == "structured":
        return None, f"{_STRUCTURED_HINT}\n\n{query}"
    if style == "research_system":
        return _RESEARCH_SYSTEM, query
    raise ValueError(f"Unknown prompt style: {style!r}")


def call_omlx(
    base_url: str,
    cell: Cell,
    query: str,
    timeout_s: int = 1200,
) -> dict[str, Any]:
    """POST to {base_url}/chat/completions and return a normalised result dict.

    max_tokens=8192: allows up to 4000 reasoning tokens + ≥4192 content tokens
    at a uniform 4000-token thinking budget across all three Gemma 4 models,
    making the thinking axis a fair comparison. Empty-content pathology is
    impossible because max_tokens strictly exceeds the reasoning budget.

    timeout_s=1200: worst-case cell is 31b·think=on running to cap (~14 min).

    Returns:
        {
            "text":       str,   # assistant message content
            "reasoning":  str|None,  # reasoning_content if present
            "raw":        dict,  # full parsed response body
            "latency_s":  float,
            "error":      str|None,
        }
    """
    system, user = build_prompt(query, cell.prompt_style)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    body: dict[str, Any] = {
        "model": cell.model,
        "messages": messages,
        "temperature": cell.temperature,
        "max_tokens": 8192,
    }
    # cell.thinking is metadata — thinking is server-side config per model on omlx;
    # no per-request toggle is available for current Gemma 4 models.

    api_key = os.environ.get("OMLX_API_KEY", "")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = json.loads(resp.read())
        latency_s = time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001
        return {
            "text": "",
            "reasoning": None,
            "raw": {},
            "latency_s": time.monotonic() - t0,
            "error": str(exc),
        }

    choice = raw.get("choices", [{}])[0]
    message = choice.get("message", {})
    text = message.get("content") or ""
    reasoning = message.get("reasoning_content")

    return {
        "text": text,
        "reasoning": reasoning,
        "raw": raw,
        "latency_s": latency_s,
        "error": None,
    }


def write_cell_output(
    run_dir: Path,
    cell: Cell,
    query_id: str,
    query_text: str,
    reference_text: str,
    result: dict[str, Any],
) -> Path:
    """Write a single .md file for a cell result. Returns the file path.

    Filename: {query_id}_{safe_label}.md where safe_label strips unsafe chars.
    """
    safe_label = (
        cell.label.replace(" ", "_")
        .replace("·", "-")
        .replace("=", "")
        .replace("/", "-")
        .replace(":", "")
    )
    safe_label = "".join(c if c.isalnum() or c in "-_." else "" for c in safe_label)
    filename = f"{query_id}_{safe_label}.md"
    out_path = run_dir / filename
    run_dir.mkdir(parents=True, exist_ok=True)

    status = classify_status(cell, result)

    finish_reason = (
        result.get("raw", {})
        .get("choices", [{}])[0]
        .get("finish_reason", "unknown")
        or "unknown"
    )
    output_tokens = (
        result.get("raw", {})
        .get("usage", {})
        .get("completion_tokens", 0)
        or 0
    )

    # YAML frontmatter
    frontmatter_lines = [
        "---",
        f"query_id: {query_id}",
        f"model: {cell.model!r}",
        f"prompt_style: {cell.prompt_style!r}",
        f"temperature: {cell.temperature}",
        f"thinking: {str(cell.thinking).lower()}",
        f"label: {cell.label!r}",
        f"latency_s: {result['latency_s']:.2f}",
        f"status: {status}",
        f"finish_reason: {finish_reason}",
        f"output_tokens: {output_tokens}",
        f"run_dir: {run_dir.name}",
        "---",
    ]

    body_parts = [
        "\n".join(frontmatter_lines),
        "",
        f"## Query\n\n{query_text}",
        "",
        f"## Reference\n\n{reference_text}",
        "",
        f"## Response\n\n{result['text'] or '*(empty)*'}",
    ]

    if result.get("reasoning"):
        body_parts += ["", f"## Reasoning\n\n{result['reasoning']}"]

    if result["error"]:
        body_parts += ["", f"## Error\n\n```\n{result['error']}\n```"]

    raw_json = json.dumps(result["raw"], indent=2) if result["raw"] else "{}"
    body_parts += [
        "",
        "## Raw response (JSON)",
        "",
        f"```json\n{raw_json}\n```",
    ]

    out_path.write_text("\n".join(body_parts) + "\n")
    return out_path
