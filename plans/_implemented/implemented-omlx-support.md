# omlx support (retain ollama)

Add omlx as an alternative local-inference backend alongside Ollama in
`start-agent.sh`. Selection via `--backend=omlx` CLI flag; default
remains `ollama`. omlx's API-key authentication eliminates the need for
a host-side firewall when using it, which is the primary motivation for
adding support.

## Status

- [x] Phase 1: CLI flag + backend dispatch plumbing
- [x] Phase 2: omlx-specific wiring (port, API key, iptables, preflight, OpenCode config)
- [x] Phase 3: Documentation (CLAUDE.md, README.md, ADR, update ollama-host-firewall.md)

## Context

`start-agent.sh` currently hard-codes Ollama as the local-inference
backend. Relevant touchpoints:

1. **Constant**: `OLLAMA_PORT=11434` (line 122).
2. **iptables carve-out**: the `firewall-apply.sh` heredoc at lines
   495–550 has a dedicated RETURN rule allowing container → `$HOST_IP`
   on `$OLLAMA_PORT` (line 532–534).
3. **Preflight probe**: lines 574–584 curl `http://$HOST_IP:$OLLAMA_PORT/api/tags`
   from inside the VM.
4. **Container env**: `OLLAMA_HOST=http://$HOST_IP:$OLLAMA_PORT` is
   injected via `DOCKER_ENV_ARGS` (line 761).
5. **OpenCode config injection**: lines 692–718 write an `ollama`
   provider entry into `opencode.json` with `baseURL` pointing at
   `http://$HOST_IP:$OLLAMA_PORT/v1`.
6. **Startup banner**: line 800 prints the Ollama endpoint.
7. **Preflight warning message**: lines 577–584 tell the user to set
   `OLLAMA_HOST=0.0.0.0:$OLLAMA_PORT` on the host.

omlx (https://github.com/jundot/omlx) is an MLX-based inference server
for Apple Silicon with an OpenAI-compatible API on port 8000 (default)
and `--api-key` support. Its API key authentication means the server can
safely bind to `0.0.0.0` without a host-side pf firewall — LAN peers
that probe the port get 401/403, not model responses.

## Goals

- `--backend=ollama` (default): behavior identical to today.
- `--backend=omlx`: uses port 8000, injects `OMLX_API_KEY` from host
  env var into the container, adjusts the iptables carve-out port,
  writes an omlx provider entry to `opencode.json`, and runs an
  omlx-specific preflight probe.
- The `$BACKEND` variable governs all backend-specific behavior via a
  single dispatch point; no scattered if/else throughout the script.
- Document in CLAUDE.md and README.md.
- Update `plans/ollama-host-firewall.md` to note it's unnecessary when
  using omlx.

## Unknowns / To Verify

1. **omlx health endpoint path.** The preflight probe for Ollama hits
   `/api/tags`. omlx likely uses `/v1/models` (OpenAI-compatible) or
   has a dedicated health endpoint. Verify before implementing Phase 2
   step 4 by checking the omlx README or running `omlx serve` and
   probing. The WebFetch from our conversation suggests `/v1/models`
   exists. Use that as the starting assumption but verify.

2. **omlx API key header format.** The `--api-key` flag enables auth,
   but the header format (likely `Authorization: Bearer <key>`) should
   be confirmed. The preflight curl needs to send the key. Verify by
   checking omlx docs or source. If it's standard OpenAI-style Bearer
   auth, `curl -H "Authorization: Bearer $key"` is correct.

3. **OpenCode provider config for omlx.** The current Ollama provider
   uses `@ai-sdk/openai-compatible` with `baseURL`. omlx is also
   OpenAI-compatible, so the same npm adapter should work with
   `baseURL: http://$HOST_IP:8000/v1`. Confirm that omlx's
   `/v1/chat/completions` response format is compatible with
   `@ai-sdk/openai-compatible` — if not, omlx may ship its own adapter.
   The WebFetch from our conversation lists one-click OpenCode setup, so
   there may be a documented config shape.

4. **omlx API key in OpenCode config.** OpenCode's `openai-compatible`
   provider likely accepts an `apiKey` field in `options` or reads an
   env var. Determine the correct config path so OpenCode authenticates
   to omlx. Fallback: inject the key as an env var that the
   `@ai-sdk/openai-compatible` adapter picks up automatically
   (typically `OPENAI_API_KEY` for OpenAI-compatible providers, but
   verify).

---

## Phase 1: CLI flag + backend dispatch plumbing

### Steps

1. **Add `--backend=` CLI flag.** In the arg-parsing `while` loop
   (lines 60–76), add:
   ```
   --backend=*)  CLI_BACKEND="${1#--backend=}" ;;
   --backend)    CLI_BACKEND="${2:?--backend requires a value}"; shift ;;
   ```
   After the loop, resolve:
   ```
   BACKEND="${CLI_BACKEND:-${CLAUDE_AGENT_BACKEND:-ollama}}"
   ```
   Validate that `$BACKEND` is one of `ollama` or `omlx`; error
   otherwise.

2. **Add `CLAUDE_AGENT_BACKEND` to the usage block** (lines 27–58).
   Add it to the ENVIRONMENT section and add `--backend=BACKEND` to
   the OPTIONS section.

3. **Replace the `OLLAMA_PORT` constant** (line 122) with a
   backend-dispatch block that sets three variables:
   ```bash
   case "$BACKEND" in
     ollama)
       INFERENCE_PORT=11434
       INFERENCE_LABEL="Ollama"
       ;;
     omlx)
       INFERENCE_PORT=8000
       INFERENCE_LABEL="omlx"
       ;;
   esac
   ```
   Remove the `OLLAMA_PORT=11434` line and replace all downstream
   references to `$OLLAMA_PORT` with `$INFERENCE_PORT`. Grep the
   script for `OLLAMA_PORT` — it appears at lines 122, 502, 534,
   576, 580, 581, 761, and 800.

4. **Rename `OLLAMA_PORT` references in the firewall heredoc.**
   The `firewall-apply.sh` heredoc (lines 495–550) uses
   `OLLAMA_PORT="$OLLAMA_PORT"` on line 502 and `$OLLAMA_PORT` in the
   iptables rule on line 534. Rename to `INFERENCE_PORT` in both the
   variable assignment inside the heredoc and the iptables rule.

### Files

- `start-agent.sh`

### Testing

- `./start-agent.sh --help` shows `--backend=BACKEND` in OPTIONS and
  `CLAUDE_AGENT_BACKEND` in ENVIRONMENT.
- `./start-agent.sh --backend=ollama` behaves identically to the
  current script (diff the startup banner output to confirm).
- `./start-agent.sh --backend=invalid` exits with an error.
- Grep the script for `OLLAMA_PORT` — should return zero matches
  (fully replaced by `INFERENCE_PORT`).

---

## Phase 2: omlx-specific wiring (Opus recommended)

This phase has the most judgment calls: API key threading, OpenCode
config shape, preflight probe format, and firewall rule adjustment all
depend on resolving the Unknowns above.

### Steps

1. **API key from host env var.** After the backend dispatch block from
   Phase 1, add:
   ```bash
   if [[ "$BACKEND" == "omlx" ]]; then
     OMLX_API_KEY="${OMLX_API_KEY:-}"
     if [[ -z "$OMLX_API_KEY" ]]; then
       echo "warning: OMLX_API_KEY not set. omlx requests from the container will fail if the server requires auth." >&2
     fi
   fi
   ```
   This is a warning, not a hard error — the user may be running omlx
   without `--api-key` during initial testing.

2. **Inject the API key into the container.** In the `DOCKER_ENV_ARGS`
   array (lines 751–765), make the inference env vars conditional on
   `$BACKEND`:

   For `ollama`:
   ```bash
   -e "OLLAMA_HOST=http://$HOST_IP:$INFERENCE_PORT"
   ```
   (same as today).

   For `omlx`:
   ```bash
   -e "OMLX_HOST=http://$HOST_IP:$INFERENCE_PORT"
   -e "OMLX_API_KEY=$OMLX_API_KEY"
   ```

   Keep the shared env vars (`TERM`, `COLORTERM`, proxy vars, git
   vars) unconditional. Only the inference-specific vars differ.

3. **Adjust the iptables carve-out comment.** The iptables rule at
   line 532–534 currently says nothing about which backend it's for.
   Update the firewall heredoc so the comment on the inference port
   RETURN rule says `claude-agent-inference` (or similar) instead of
   just `claude-agent`, to distinguish it from the other tagged rules
   when debugging. This is optional polish — the rule works either way
   since the port is parameterized from Phase 1.

4. **Backend-specific preflight probe.** Replace the current Ollama
   preflight block (lines 574–584) with a backend-dispatched version:

   For `ollama`: keep the existing `curl` to
   `http://$HOST_IP:$INFERENCE_PORT/api/tags` and the existing warning
   message about `launchctl setenv OLLAMA_HOST 0.0.0.0`.

   For `omlx`: probe `http://$HOST_IP:$INFERENCE_PORT/v1/models`
   (verify per Unknown #1). Include the API key header if
   `$OMLX_API_KEY` is set:
   `curl -sf --max-time 3 -H "Authorization: Bearer $OMLX_API_KEY" "http://$HOST_IP:$INFERENCE_PORT/v1/models"`.
   On failure, warn:
   ```
   warning: omlx not reachable at http://$HOST_IP:$INFERENCE_PORT from
   inside the Colima VM. Ensure omlx is running on the host with:
       omlx serve --model-dir ~/models
   or via: brew services start omlx
   Continuing without local inference.
   ```

5. **Backend-specific OpenCode config injection.** The existing Python
   block at lines 692–718 writes an `ollama` provider entry. Make this
   conditional on `$BACKEND`:

   For `ollama`: keep the current behavior unchanged.

   For `omlx`: write an `omlx` provider entry with:
   - `"npm": "@ai-sdk/openai-compatible"` (same adapter — verify per
     Unknown #3)
   - `"name": "omlx (host)"`
   - `"options": {"baseURL": "http://$HOST_IP:$INFERENCE_PORT/v1"}`
   - If `$OMLX_API_KEY` is set, include it in `"options"` as
     `"apiKey": "$OMLX_API_KEY"` (verify field name per Unknown #4).

   Pass `$BACKEND` and `$OMLX_API_KEY` as additional `sys.argv`
   arguments to the Python script. The script should write the
   provider entry keyed as `"omlx"` (not `"ollama"`) so both can
   coexist in the config if the user switches backends between runs.

6. **Startup banner.** Update the banner at line 798–800 to print the
   backend name and the inference endpoint:
   ```
   echo "    inference: $INFERENCE_LABEL at http://$HOST_IP:$INFERENCE_PORT"
   ```
   instead of the current `echo "    ollama  : ..."`.

### Files

- `start-agent.sh`

### Testing

- `./start-agent.sh --backend=omlx` with `OMLX_API_KEY=test-key`
  exported:
  - Banner prints `inference: omlx at http://<HOST_IP>:8000`.
  - `docker inspect claude-agent` shows `OMLX_HOST` and `OMLX_API_KEY`
    env vars.
  - `OLLAMA_HOST` is NOT in the container env.
  - Inside the container: `echo $OMLX_HOST` prints the expected URL;
    `echo $OMLX_API_KEY` prints the key.
- `./start-agent.sh --backend=ollama` (default):
  - Behavior identical to before Phase 1.
  - `OLLAMA_HOST` is in the container env; `OMLX_HOST` is NOT.
- `./start-agent.sh --backend=omlx` without `OMLX_API_KEY`:
  - Prints the warning about missing API key.
  - Container still starts (non-fatal).
- Firewall rules in the VM (`colima ssh -- sudo iptables -L DOCKER-USER -v -n`):
  - With `--backend=omlx`: the inference port RETURN rule shows
    `dpt:8000`, not `dpt:11434`.
  - With `--backend=ollama`: the inference port RETURN rule shows
    `dpt:11434`.
- `opencode.json` at `~/.claude-agent/opencode-config/opencode.json`:
  - With `--backend=omlx`: contains an `"omlx"` provider entry with
    `baseURL` pointing at port 8000.
  - With `--backend=ollama`: contains an `"ollama"` provider entry
    (same as today).
  - Switching backends between runs: both provider entries coexist in
    the config file (they don't stomp each other).

---

## Phase 3: Documentation

### Steps

1. **CLAUDE.md** — add an `omlx` subsection under the existing
   "start-agent.sh key decisions" section. Key points to document:
   - `--backend=omlx` selects omlx; default is `ollama`.
   - `OMLX_API_KEY` env var on the host is passed into the container.
   - omlx's API-key auth means no host-side firewall is needed (unlike
     Ollama, which requires either `localhost`-only binding or a pf
     firewall when bound to `0.0.0.0`).
   - Port 8000 is the hardcoded default; change `INFERENCE_PORT` in
     the `omlx` case branch to use a different port.
   - OpenCode config writes an `"omlx"` provider entry alongside (not
     replacing) the `"ollama"` entry.

2. **README.md** — add usage examples:
   ```bash
   # With Ollama (default):
   ./start-agent.sh

   # With omlx:
   export OMLX_API_KEY=your-secret-key
   ./start-agent.sh --backend=omlx
   ```
   Brief note on first-time omlx setup: install via `brew install omlx`
   or download from releases, start with
   `omlx serve --model-dir ~/models --api-key $OMLX_API_KEY`, and
   ensure it's reachable on `0.0.0.0:8000`.

3. **ADR.md** — new ADR documenting why omlx was added as an
   alternative backend: API-key auth eliminates the host-firewall
   requirement, reducing operational complexity for the common case
   where the user is on a shared network.

4. **`plans/ollama-host-firewall.md`** — add a note at the top, after
   the title, stating:
   ```
   > **Note:** This plan is unnecessary if using `--backend=omlx`.
   > omlx's `--api-key` flag provides application-layer auth that
   > prevents unauthorized LAN access without a host-side firewall.
   > See ADR-NNN.
   ```

### Files

- `CLAUDE.md`
- `README.md`
- `ADR.md`
- `plans/ollama-host-firewall.md`

### Testing

- Read through each doc and verify cross-references are consistent.
- `./start-agent.sh --help` output matches the README examples.

---

## Notes

- **Why `INFERENCE_PORT` and not `BACKEND_PORT`.** The port is
  specifically for the inference server, not a generic "backend" port.
  If a future backend uses a different port range, the name remains
  descriptive. `BACKEND_PORT` could be confused with the tinyproxy port
  or some other backend service.

- **Both provider entries coexist in `opencode.json`.** Switching
  between `--backend=ollama` and `--backend=omlx` across runs writes
  separate provider keys (`"ollama"` vs `"omlx"`). Neither stomps the
  other. This means OpenCode's UI shows both providers after the user
  has used both backends at least once, which may be confusing but is
  more correct than silently deleting the other provider's config.

- **`OMLX_API_KEY` is a warning, not a hard error.** The user may run
  omlx without `--api-key` during local testing. Forcing the env var
  would break that workflow. The warning is sufficient.

- **No changes to the Dockerfile.** omlx runs on the macOS host, not
  inside the container. The container just needs `curl` (already
  present) to reach it via the OpenAI-compatible API. No new packages
  or binaries needed inside the image.

- **`OLLAMA_HOST` env var semantics.** When `--backend=omlx` is used,
  `OLLAMA_HOST` is intentionally NOT set in the container. Tools that
  auto-discover Ollama via this env var will correctly see no Ollama
  endpoint, avoiding confusion. omlx gets its own `OMLX_HOST` env var.

- **Future backends.** The `case "$BACKEND"` dispatch block is easy to
  extend. Adding a third backend (e.g., `llama-cpp`, `vllm`) means
  adding one more case branch in Phase 1's dispatch, one more
  conditional in Phase 2's env/preflight/config blocks, and one more
  doc section. The pattern scales linearly.
