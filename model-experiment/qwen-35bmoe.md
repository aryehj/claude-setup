# Plan: Allow Container to Call Host Local LLM Server

## Objective

Enable the `claude-agent` container created by `start-agent.sh` to call an openai- or anthropic-compatible LLM server running on the macOS host loopback interface (`127.0.0.1`).

## Problem

The container's egress is firewalled at the VM level (ADR-010) with a default-deny policy. The DOCKER-USER chain only allows:
- Established/related connections
- Traffic to tinyproxy for allowlisted domains
- Traffic to host inference server ports (Ollama: 11434, omlx: 8000)
- DNS to bridge gateway (127.0.0.11)

A host-local API server bound to `127.0.0.1` is not reachable from inside the container.

## Proposed Solution

Add a dedicated iptables carve-out for the API server port, similar to the existing Ollama/omlx rules.

### Implementation Steps

1. **Add CLI flag for API port** (in `start-agent.sh`):
   ```bash
   CLI_API_PORT=""
   # ... in args parsing:
   --api-port=*) CLI_API_PORT="${1#--api-port=}" ;;
   ```

2. **Add API port to environment**:
   ```bash
   API_PORT="8080"  # default, or from CLI/env
   ```

3. **Add iptables rule** (after line 564, where Ollama rule is):
   ```bash
   # Container -> macOS host API server
   sudo iptables -I DOCKER-USER 4 -s "\$BRIDGE_CIDR" -d "\$HOST_IP" \
     -p tcp --dport "\$API_PORT" \
     -m comment --comment claude-agent -j RETURN
   ```

4. **Pass API host URL to container** (in `DOCKER_ENV_ARGS`, after line 829):
   ```bash
   -e "API_BASE_URL=http://$HOST_IP:$API_PORT/v1"
   ```

5. **Update OpenCode config** (after line 780, add omlx-compatible entry):
   ```python
   elif backend == "api":
       entry = providers.setdefault('api', {
           "npm": "@ai-sdk/openai-compatible",
           "name": "API (host)",
           "options": {},
           "models": {},
       })
       entry.setdefault('options', {})['baseURL'] = base_url
   ```

6. **Add preflight probe** (after line 633):
   ```bash
   API_CURL_ARGS=(-sf --max-time 3)
   if [[ -n "${API_KEY:-}" ]]; then
     API_CURL_ARGS+=(-H "Authorization: Bearer $API_KEY")
   fi
   if ! vm_ssh curl "${API_CURL_ARGS[@]}" "http://$HOST_IP:$API_PORT/v1/models" >/dev/null 2>&1; then
     echo "warning: API server not reachable at http://$HOST_IP:$API_PORT from inside VM" >&2
   fi
   ```

### Environment Variables

- `API_PORT` (default: 8080)
- `API_BASE_URL` (container-internal: `http://$HOST_IP:$API_PORT/v1`)
- `API_KEY` (optional, for API key auth)

### Usage

```bash
# Default port 8080
start-agent.sh --api-port 8080

# With API key
API_KEY=abc123 start-agent.sh --api-port 8000
```

## Alternative Approaches Considered

### Option A: Tinyproxy Allowlist

Add the API server domain to `allowlist.txt` and route through tinyproxy.
- **Pros**: Uses existing allowlist mechanism
- **Cons**: Requires domain resolution, slower, doesn't work for `localhost` IPs

### Option B: Host Network Mode

Run container with `--network=host` to access host loopback directly.
- **Pros**: Simple, no firewall changes
- **Cons**: Breaks container isolation, defeats purpose of egress allowlist

### Option C: SSH Tunnel

Create SSH tunnel from container to host.
- **Pros**: Encrypted, no firewall changes
- **Cons**: Adds complexity, requires SSH setup, slower

## Why Port Carve-out is Best

1. **Simplicity**: Single iptables line, mirrors existing Ollama pattern
2. **Performance**: Direct routing, no proxy overhead
3. **Flexibility**: Works for any port, any binding
4. **Security**: Explicit allowlist, no trust boundary changes
5. **Maintainability**: Easy to add/remove, follows existing patterns

## Testing

1. Run API server on host: `ollama serve --host 127.0.0.1` (or your API server)
2. Start container: `./start-agent.sh --api-port 11434`
3. Inside container: `curl http://<HOST_IP>:11434/api/tags`
4. Verify OpenCode config: `cat ~/.config/opencode/opencode.json`
5. Test LLM call via Claude Code

## Files to Modify

- `start-agent.sh`: Add API port support, firewall rule, env vars
- `dockerfiles/claude-agent.Dockerfile`: No changes needed (API runs on host)
- `plans/`: This file

## Notes

- HOST_IP is discovered at runtime (line 451 in start-agent.sh), so the rule automatically works regardless of network configuration
- The rule is added to DOCKER-USER chain at position 4, after Ollama/omlx carve-out
- Uses same `-m comment --comment claude-agent` pattern for consistent rule management
- API_KEY support mirrors omlx implementation (ADR-012)
