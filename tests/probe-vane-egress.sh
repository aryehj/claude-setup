#!/usr/bin/env bash
# Probe that the research-vane container is wired through Squid for full egress.
# Three checks:
#   1. HTTP_PROXY, HTTPS_PROXY, and NO_PROXY are all present on the running
#      container, well-formed, and cover the in-network hostnames we depend on.
#   2. A sidecar curl on research-net, configured with the same HTTPS_PROXY,
#      can reach an HTTPS host through Squid.
# Exit 0 if all pass, non-zero with diagnostics otherwise.
#
# We deliberately do NOT hardcode the bridge IP. Squid binds to the docker0
# bridge gateway in the VM (research.py's config.bridge_ip), which differs
# from research-net's gateway. The container's env vars are the source of
# truth — we just verify they're set and that they actually work.
set -u

ENV_DUMP=$(colima ssh -p research -- docker inspect research-vane \
  --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null) || {
  echo "FAIL: research-vane container not found (run ./research.py first)" >&2
  exit 2
}

env_get() {
  printf '%s\n' "$ENV_DUMP" | awk -F= -v k="$1" '$1==k {sub(/^[^=]+=/,""); print; exit}'
}

fail=0
HTTP_PROXY_VAL=$(env_get HTTP_PROXY)
HTTPS_PROXY_VAL=$(env_get HTTPS_PROXY)
NO_PROXY_VAL=$(env_get NO_PROXY)

if [[ -z "$HTTP_PROXY_VAL" ]]; then
  echo "FAIL: HTTP_PROXY is not set on research-vane"
  fail=1
elif [[ "$HTTP_PROXY_VAL" != http://*:* ]]; then
  echo "FAIL: HTTP_PROXY=${HTTP_PROXY_VAL} is not a well-formed http://host:port URL"
  fail=1
else
  echo "ok   HTTP_PROXY=${HTTP_PROXY_VAL}"
fi

if [[ -z "$HTTPS_PROXY_VAL" ]]; then
  echo "FAIL: HTTPS_PROXY is not set on research-vane"
  fail=1
elif [[ "$HTTPS_PROXY_VAL" != http://*:* ]]; then
  echo "FAIL: HTTPS_PROXY=${HTTPS_PROXY_VAL} is not a well-formed http://host:port URL"
  fail=1
else
  echo "ok   HTTPS_PROXY=${HTTPS_PROXY_VAL}"
fi

for needle in research-searxng host.docker.internal; do
  if [[ ",${NO_PROXY_VAL}," != *",${needle},"* ]]; then
    echo "FAIL: NO_PROXY is missing '${needle}' (got: ${NO_PROXY_VAL:-<unset>})"
    fail=1
  fi
done
[[ -n "$NO_PROXY_VAL" ]] && echo "ok   NO_PROXY=${NO_PROXY_VAL}"

# Sidecar HTTPS reachability: a research-net container with the same
# HTTPS_PROXY should reach an HTTPS host via Squid. Skip if HTTPS_PROXY was
# unusable.
if [[ -n "$HTTPS_PROXY_VAL" && "$HTTPS_PROXY_VAL" == http://*:* ]]; then
  status=$(colima ssh -p research -- docker run --rm --network research-net \
    -e HTTPS_PROXY="$HTTPS_PROXY_VAL" \
    curlimages/curl:latest -s -o /dev/null -w '%{http_code}' \
    https://example.com 2>/dev/null)
  if [[ "$status" == "200" ]]; then
    echo "ok   sidecar via ${HTTPS_PROXY_VAL} -> https://example.com -> 200"
  else
    echo "FAIL: sidecar via ${HTTPS_PROXY_VAL} -> https://example.com -> ${status:-no-response}"
    fail=1
  fi
fi

exit $fail
