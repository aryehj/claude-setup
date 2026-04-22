# Global Container CLAUDE.md

This file is loaded into every coding-agent session (Claude Code via
`~/.claude/CLAUDE.md`; OpenCode via `opencode.json`'s `instructions`) running
inside a dev container. It describes the shared container environment.
Project-level `CLAUDE.md` / `AGENTS.md` overrides anything here. You are running inside one of two
sibling environments: `claude-agent` (Colima + docker, via `start-agent.sh`)
or `claude-dev` (Apple Containers microVM, via `start-claude.sh`). Assume
`claude-agent` unless the start-claude.sh exceptions at the end of this file
apply.

## Filesystem

`$HOME=/root`. The working directory (e.g. `/Users/<name>/Repos/...`) is a
bind mount from the macOS host — it is not a Mac filesystem. There is no
`/Users/<name>/.claude/`; all Claude Code config lives at `/root/.claude/`.
`/root/.claude/` is itself a bind mount from `~/.claude-containers/shared/`
on the host, shared across all containers.

## Python & uv

`uv` is at `/usr/local/bin/uv`. Use `uv run` and `uv pip`.
`UV_PROJECT_ENVIRONMENT` is set to `$TMPDIR/.venv`, so any `.venv/` in the
project root is **ignored** — it almost certainly contains macOS binaries
that won't run on Linux. Run `uv sync` once per fresh session to build a
usable venv; this is expected and normal.

## Network Egress (claude-agent)

All egress flows through an in-VM HTTP proxy with a hostname allowlist at
`~/.claude-agent/allowlist.txt` on the host. `HTTPS_PROXY` and `HTTP_PROXY`
are pre-set; Node honors them via `NODE_USE_ENV_PROXY=1`.

**`github.com` is not on the default allowlist.** The proxy filters by
hostname only (not method), so it cannot be made read-only. For code reads,
use `codeload.github.com` or `raw.githubusercontent.com`, which are allowed.

A `403` or connection-refused on a hostname means the allowlist is rejecting
it. Do not retry blindly — tell the user to add the hostname to
`~/.claude-agent/allowlist.txt` and run `start-agent.sh --reload-allowlist`.

## Local Inference (claude-agent)

A local model server runs on the macOS host at `$OLLAMA_HOST` (Ollama, port 11434) or `$OMLX_HOST` (omlx, port 8000). OpenCode is pre-wired to it — no configuration needed. Route local-model calls to `$OLLAMA_HOST` / `$OMLX_HOST`, not through the proxy.

## Differences in claude-dev (start-claude.sh)

- **No network proxy or allowlist** — full outbound egress is available.
- **No local inference server** — `$OLLAMA_HOST` and `$OMLX_HOST` are unset.
- **Bubblewrap sandbox is active** for bash commands. `/tmp` and
  `/root/.cache` are **read-only** at the sandbox mount layer. Use `$TMPDIR`
  for all scratch work — `uv` is already configured to use it. A
  `read-only file system` error on `/tmp` means you are inside the sandbox;
  retarget the operation to `$TMPDIR` and do not escalate.
