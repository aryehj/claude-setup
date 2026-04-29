# Plan: Enable Container Access to Host-bound LLM Server

## Analysis
`start-agent.sh` already handles:
- Host IP discovery (`HOST_IP`).
- VM-level firewall rules (`iptables` allowing traffic to `HOST_IP:INFERENCE_PORT`).
- Provider configuration injection into `opencode.json`.

The issue is the host server is bound to `127.0.0.1`, and the Colima VM network interface will be rejected.

## Recommendations

### 1. Server Configuration (Preferred)
The LLM server should be bound to `0.0.0.0` (all interfaces) instead of `127.0.0.1`.
- **Ollama**: `launchctl setenv OLLAMA_HOST 0.0.0.0:11434`
- **Others**: Use the `--host 0.0.0.0` or similar flag provided by the server.

### 2. Host-side Proxy (Fallback)
If binding to `0.0.0.0` is not possible, the user can use `socat` on the macOS host to bridge the traffic:
```bash
socat TCP-LISTEN:<PORT>,fork,reuseaddr TCP:127.0.0.1:<PORT>
```

## Implementation steps for new servers in `start-agent.sh`
To add support for another OpenAI/Anthropic compatible server:
1. Update the `BACKEND` case statement (lines 117-130) in `start-agent.sh` with the new backend name and its `INFERENCE_PORT`.
2. Ensure the server on the host is host-reachable (bound to `0.0.0.0` or proxied).
3. Run `start-agent.sh --backend=<new-backend>`.
