# Plan: Allow Container to Call Host-Bound LLM Server

## Problem

The `start-agent.sh` container cannot reach an OpenAI/Anthropic-compatible LLM server running on the macOS host's loopback address (`127.0.0.1:PORT`). The Colima VM and Docker bridge network isolate the container from the host's loopback interface.

## Current Architecture

1. **tinyproxy** — Runs in the Colima VM, bound to bridge IP (`172.17.0.1:8888`), enforces allowlist-based filtering via `FilterDefaultDeny`
2. **iptables DOCKER-USER** — Allows container → tinyproxy, container → host inference port (11434/8000), and DNS; rejects everything else
3. **Proxy env vars** — `HTTP_PROXY`/`HTTPS_PROXY` point to tinyproxy; `NO_PROXY` includes `HOST_IP`

## Recommendation

### Option A: `--host-llm=HOST:PORT` Flag (Recommended)

Add a new CLI option that mirrors the existing Ollama/omlx backend pattern:

```bash
start-agent.sh --host-llm=localhost:4000
```

**Implementation:**

1. **Parse the flag** — Extract host/port; resolve `localhost`/`127.0.0.1` to `HOST_IP` (the macOS host IP reachable from the VM, already discovered in the script)
2. **Add iptables carve-out** — Insert a rule alongside the existing inference port rule:
   ```bash
   sudo iptables -I DOCKER-USER 3 -s "$BRIDGE_CIDR" -d "$HOST_IP" \
     -p tcp --dport "$HOST_LLM_PORT" \
     -m comment --comment claude-agent -j RETURN
   ```
3. **Set container env var** — Add `-e "HOST_LLM_URL=http://$HOST_IP:$HOST_LLM_PORT"` to `DOCKER_ENV_ARGS`
4. **Optional: OpenCode config injection** — If the user specifies `--host-llm`, inject an additional provider entry in `~/.claude-agent/opencode-config/opencode.json`

**Constraint:** The LLM server must be bound to `0.0.0.0` or the host's external interface IP. A server bound strictly to `127.0.0.1` will still be unreachable from the VM.

---

### Option B: SSH Port Forwarding (For True Loopback-Only Servers)

If the LLM server is bound strictly to loopback and cannot be reconfigured, use SSH tunneling:

**User runs on macOS host:**
```bash
# Forward container's localhost:4000 → host's localhost:4000 via Colima VM
ssh -L 4000:127.0.0.1:4000 -N colima-ssh-user@<colima-vm-gateway>
```

Then inside the container, call `http://localhost:4000`.

**Limitations:** Requires a persistent SSH tunnel; adds operational complexity.

---

### Option C: `host.docker.internal` DNS Name

Colima already provides `host.docker.internal` as a DNS name that resolves to the macOS host. The user could:

1. Bind their LLM server to `0.0.0.0` on macOS
2. Call it from the container as `http://host.docker.internal:PORT`

**Gap:** The script would still need to add an iptables carve-out for the port, and set `NO_PROXY` appropriately.

---

## Recommended Approach

**Option A** with the following refinements:

1. Accept `--host-llm=HOST:PORT` where `HOST` can be `localhost`, `127.0.0.1`, or omitted (defaults to host IP)
2. Auto-resolve `localhost`/`127.0.0.1` to `HOST_IP` (macOS host IP from VM perspective)
3. Warn if the server appears unreachable (similar to Ollama/omlx preflight)
4. Add iptables rule and env var automatically
5. Optionally inject OpenCode provider config

This reuses existing patterns, requires minimal changes, and works for the common case where the user can bind their server to `0.0.0.0`.
