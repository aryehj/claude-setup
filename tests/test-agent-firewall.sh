#!/usr/bin/env bash
# NOTE: this branch (loose-agent-firewall) loosens the firewall to a denylist; this script tests the legacy allowlist behavior and will fail.
# test-agent-firewall.sh — validate the start-agent.sh egress allowlist from
# inside the running claude-agent container.
#
# Covers 5 of the 6 smoke tests documented in README.md. The 6th (allowlist
# hot-reload) requires host-side actions and is not included here.
#
# Exit 0 if every test either passes or is legitimately skipped (Ollama not
# running). Exit 1 on any failure.

set -u

PASS=0
FAIL=0
SKIP=0

if [[ -t 1 ]]; then
  C_OK=$'\e[32m'
  C_ERR=$'\e[31m'
  C_WARN=$'\e[33m'
  C_RST=$'\e[0m'
else
  C_OK=""; C_ERR=""; C_WARN=""; C_RST=""
fi

pass() { printf '%sPASS%s  %s\n' "$C_OK" "$C_RST" "$1"; PASS=$((PASS+1)); }
fail() { printf '%sFAIL%s  %s%s\n' "$C_ERR" "$C_RST" "$1" "${2:+ — $2}"; FAIL=$((FAIL+1)); }
skip() { printf '%sSKIP%s  %s%s\n' "$C_WARN" "$C_RST" "$1" "${2:+ — $2}"; SKIP=$((SKIP+1)); }

echo "==> start-agent firewall smoke tests"
echo

if [[ -z "${HTTPS_PROXY:-}" ]]; then
  printf '%swarning%s: HTTPS_PROXY is not set — are you running this inside the claude-agent container?\n\n' \
    "$C_WARN" "$C_RST"
fi

# ── 1. Bridge default-deny ───────────────────────────────────────────────────
# Direct egress (bypassing the proxy) must be rejected by the DOCKER-USER
# REJECT rule. curl --noproxy '*' ignores HTTPS_PROXY for all hosts.
test_default_deny() {
  local out rc
  out=$(curl --noproxy '*' -sS --max-time 5 https://example.com 2>&1)
  rc=$?
  if (( rc != 0 )); then
    pass "default-deny: direct egress blocked (curl exit $rc)"
  else
    fail "default-deny" "direct request to example.com succeeded without the proxy"
  fi
}

# ── 2. Allowlisted host via proxy ────────────────────────────────────────────
# A host that should be in the default allowlist must reach the internet
# through tinyproxy.
test_allowed_via_proxy() {
  local out rc
  out=$(curl -sS --max-time 10 https://api.github.com/zen 2>&1)
  rc=$?
  if (( rc == 0 )) && [[ -n "$out" ]]; then
    pass "allowed-via-proxy: api.github.com/zen → \"${out}\""
  else
    fail "allowed-via-proxy" "curl exit $rc, response=${out:0:120}"
  fi
}

# ── 3. Denied host via proxy ─────────────────────────────────────────────────
# A host that is NOT in the allowlist must be rejected by tinyproxy's filter.
# curl exit 56 = CONNECT tunnel failed (the canonical "proxy said no" error
# for HTTPS); any other nonzero is still treated as denied.
test_denied_via_proxy() {
  local out rc
  out=$(curl -sS --max-time 10 https://example.com 2>&1)
  rc=$?
  if (( rc == 56 )); then
    pass "denied-via-proxy: tinyproxy rejected CONNECT (curl exit 56)"
  elif (( rc != 0 )); then
    pass "denied-via-proxy: blocked (curl exit $rc)"
  else
    fail "denied-via-proxy" "example.com reached the internet — the proxy filter is not enforcing"
  fi
}

# ── 4. Ollama carve-out ──────────────────────────────────────────────────────
# The host's Ollama endpoint must be reachable via the iptables carve-out,
# bypassing the proxy. If Ollama isn't actually running on the host, the TCP
# handshake reaches the host and fails fast with ECONNREFUSED (curl exit 7) —
# that still proves the carve-out is working, so we report it as SKIP rather
# than FAIL.
test_ollama_carveout() {
  if [[ -z "${OLLAMA_HOST:-}" ]]; then
    skip "ollama-carveout" "OLLAMA_HOST not set"
    return
  fi
  local out rc
  out=$(curl -sS --max-time 5 "$OLLAMA_HOST/api/tags" 2>&1)
  rc=$?
  if (( rc == 0 )); then
    pass "ollama-carveout: $OLLAMA_HOST reachable"
  elif (( rc == 7 )) && [[ "$out" == *"Couldn't connect"* ]]; then
    skip "ollama-carveout" "Ollama not running on host (carve-out itself is fine: ECONNREFUSED from the host, not an iptables reject)"
  else
    fail "ollama-carveout" "curl exit $rc, out=${out:0:120}"
  fi
}

# ── 5. Runtime env wiring ────────────────────────────────────────────────────
# All four proxy/Ollama vars must be present from process birth.
test_env_wiring() {
  local missing=()
  for var in HTTP_PROXY HTTPS_PROXY NO_PROXY OLLAMA_HOST; do
    if [[ -z "${!var:-}" ]]; then
      missing+=("$var")
    fi
  done
  if (( ${#missing[@]} == 0 )); then
    pass "env-wiring: HTTP_PROXY, HTTPS_PROXY, NO_PROXY, OLLAMA_HOST all set"
  else
    fail "env-wiring" "missing: ${missing[*]}"
  fi
}

# ── 6. Inter-container port isolation ────────────────────────────────────────
# The iptables CLAUDE_AGENT chain allows inter-container traffic within
# claude-agent-net only on port 8080 (SearXNG MCP path). All other ports must
# be rejected. Tests both sides: 8080 reachable, 9000 blocked.
# Skip if searxng is not running (--disable-search).
test_searxng_port_isolation() {
  if ! getent hosts searxng >/dev/null 2>&1; then
    skip "inter-container-port-isolation" "searxng not in DNS (--disable-search?)"
    return
  fi

  # Port 8080: must be reachable. curl 0 (ok), 22 (HTTP error ≥400), or 52
  # (empty reply) all confirm the TCP connection was allowed through.
  local rc
  curl --noproxy searxng -sS --max-time 5 http://searxng:8080/ >/dev/null 2>&1
  rc=$?
  if (( rc == 0 || rc == 22 || rc == 52 )); then
    pass "inter-container-port-isolation: searxng:8080 reachable (curl exit $rc)"
  else
    fail "inter-container-port-isolation" "searxng:8080 blocked (curl exit $rc); iptables AGENT_NET:8080 RETURN rule may be missing"
  fi

  # Port 9000: must be blocked. With --reject-with icmp-admin-prohibited, curl
  # sees the ICMP response immediately and exits non-zero (typically exit 7).
  curl --noproxy searxng -sS --max-time 3 http://searxng:9000/ >/dev/null 2>&1
  rc=$?
  if (( rc != 0 )); then
    pass "inter-container-port-isolation: searxng:9000 blocked (curl exit $rc)"
  else
    fail "inter-container-port-isolation" "searxng:9000 reachable — iptables CLAUDE_AGENT REJECT not firing on inter-container traffic"
  fi
}

test_default_deny
test_allowed_via_proxy
test_denied_via_proxy
test_ollama_carveout
test_env_wiring
test_searxng_port_isolation

echo
printf '==> %s%d passed%s, %s%d failed%s, %s%d skipped%s\n' \
  "$C_OK" "$PASS" "$C_RST" \
  "$C_ERR" "$FAIL" "$C_RST" \
  "$C_WARN" "$SKIP" "$C_RST"

(( FAIL == 0 ))
