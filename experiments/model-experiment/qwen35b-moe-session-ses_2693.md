# start-agent.sh container host loopback LLM access

**Session ID:** ses_2693e3576ffesQbR0hfWVHH0HV
**Created:** 4/16/2026, 9:46:15 AM
**Updated:** 4/16/2026, 9:53:14 AM

---

## User

Evaluate start-agent.sh and recommend an approach to allow the container it creates to call a openai- or anthropic- compatible LLM server running on the host machine, currently bound to the host manchine's looopback address. 

---

## Assistant (Build · Qwen3.5-35B-A3B-6bit · 51.0s)

_Thinking:_


I need to read the start-agent.sh script to understand the container networking setup and firewall rules before I can recommend a solution for hosting access.



I'll analyze the start-agent.sh script to understand the current container networking setup and recommend an approach for hosting access.

**Tool: read**

**Input:**
```json
{
  "filePath": "/Users/aryehj/Repos/start-claude/start-agent.sh"
}
```

**Output:**
```
<path>/Users/aryehj/Repos/start-claude/start-agent.sh</path>
<type>file</type>
<content>
1: #!/usr/bin/env bash
2: # start-agent.sh — spin up a Colima-backed agent container for a project.
3: #
4: # Sibling to start-claude.sh. Uses one shared Colima VM (profile:
5: # claude-agent), one shared docker container (claude-agent), with both the
6: # Claude Code and OpenCode CLIs installed. Enforces a VM-level egress
7: # allowlist via tinyproxy + iptables DOCKER-USER rules, and routes local
8: # inference to Ollama or omlx running on the macOS host.
9: #
10: # Usage:
11: #   start-agent.sh [--rebuild] [--reload-allowlist]
12: #                  [--backend=ollama|omlx]
13: #                  [--memory=VALUE] [--cpus=N]
14: #                  [--git-name=NAME] [--git-email=EMAIL]
15: #                  [project-dir]
16: 
17: set -euo pipefail
18: 
19: # ── args ──────────────────────────────────────────────────────────────────────
20: REBUILD=false
21: RELOAD_ALLOWLIST=false
22: CLI_MEMORY=""
23: CLI_CPUS=""
24: CLI_GIT_NAME=""
25: CLI_GIT_EMAIL=""
26: CLI_BACKEND=""
27: POSITIONAL=()
28: 
29: usage() {
30:   cat <<'USAGE'
31: start-agent.sh — Colima-backed Claude Code + OpenCode dev container
32: 
33: USAGE:
34:   start-agent.sh [options] [project-dir]
35: 
36: OPTIONS:
37:   --rebuild              Remove image + container and recreate. With
38:                          confirmation, also delete and recreate the Colima VM.
39:   --reload-allowlist     Regenerate tinyproxy's filter file from
40:                          ~/.claude-agent/allowlist.txt and reload tinyproxy.
41:                          Fast path; does not restart the container.
42:   --memory=VALUE         VM memory (e.g. 8, 8G, 8GB). Default: 8 GiB.
43:   --cpus=N               VM CPU count. Default: 6.
44:   --backend=BACKEND      Local inference backend: ollama (default) or omlx.
45:   --git-name=NAME        Git author/committer name inside the container.
46:   --git-email=EMAIL      Git author/committer email inside the container.
47:   -h, --help             Show this help.
48: 
49: ALLOWLIST:
50:   Edit  ~/.claude-agent/allowlist.txt  on the macOS host to change which
51:   domains the container can reach. One domain per line; '#' for comments;
52:   suffix match (github.com covers api.github.com). Apply changes with:
53:       start-agent.sh --reload-allowlist
54: 
55: ENVIRONMENT:
56:   CLAUDE_AGENT_MEMORY    Default VM memory (overridden by --memory).
57:   CLAUDE_AGENT_CPUS      Default VM CPU count (overridden by --cpus).
58:   CLAUDE_AGENT_BACKEND   Default backend (overridden by --backend).
59:   GIT_USER_NAME          Default git name (overridden by --git-name).
60:   GIT_USER_EMAIL         Default git email (overridden by --git-email).
61:   OMLX_API_KEY           API key for omlx (passed into the container when
62:                          --backend=omlx).
63: USAGE
64: }
65: 
66: while [[ $# -gt 0 ]]; do
67:   case "$1" in
68:     --rebuild)           REBUILD=true ;;
69:     --reload-allowlist)  RELOAD_ALLOWLIST=true ;;
70:     --memory=*)          CLI_MEMORY="${1#--memory=}" ;;
71:     --memory)            CLI_MEMORY="${2:?--memory requires a value}"; shift ;;
72:     --cpus=*)            CLI_CPUS="${1#--cpus=}" ;;
73:     --cpus)              CLI_CPUS="${2:?--cpus requires a value}"; shift ;;
74:     --git-name=*)        CLI_GIT_NAME="${1#--git-name=}" ;;
75:     --git-name)          CLI_GIT_NAME="${2:?--git-name requires a value}"; shift ;;
76:     --git-email=*)       CLI_GIT_EMAIL="${1#--git-email=}" ;;
77:     --git-email)         CLI_GIT_EMAIL="${2:?--git-email requires a value}"; shift ;;
78:     --backend=*)         CLI_BACKEND="${1#--backend=}" ;;
79:     --backend)           CLI_BACKEND="${2:?--backend requires a value}"; shift ;;
80:     -h|--help)           usage; exit 0 ;;
81:     *)                   POSITIONAL+=("$1") ;;
82:   esac
83:   shift
84: done
85: 
86: PROJECT_DIR="${POSITIONAL[0]:-$(pwd)}"
87: PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
88: 
89: # Accept "8", "8G", "8GB" (any case); normalize to an integer GiB that
90: # `colima start --memory` expects.
91: normalize_gib() {
92:   local raw
93:   raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
94:   raw="${raw// /}"
95:   raw="${raw%gib}"
96:   raw="${raw%gb}"
97:   raw="${raw%g}"
98:   if [[ ! "$raw" =~ ^[0-9]+$ ]]; then
99:     echo "error: invalid memory value '$1' — use integer GiB (e.g. 8, 8G, 8GB)" >&2
100:     exit 1
101:   fi
102:   echo "$raw"
103: }
104: 
105: MEMORY_RAW="${CLI_MEMORY:-${CLAUDE_AGENT_MEMORY:-8}}"
106: CLAUDE_AGENT_MEMORY_GB="$(normalize_gib "$MEMORY_RAW")"
107: CLAUDE_AGENT_CPUS="${CLI_CPUS:-${CLAUDE_AGENT_CPUS:-6}}"
108: if [[ ! "$CLAUDE_AGENT_CPUS" =~ ^[0-9]+$ ]]; then
109:   echo "error: invalid --cpus value '$CLAUDE_AGENT_CPUS'" >&2
110:   exit 1
111: fi
112: 
113: GIT_USER_NAME="${CLI_GIT_NAME:-${GIT_USER_NAME:-Dev}}"
114: GIT_USER_EMAIL="${CLI_GIT_EMAIL:-${GIT_USER_EMAIL:-dev@localhost}}"
115: 
116: BACKEND="${CLI_BACKEND:-${CLAUDE_AGENT_BACKEND:-ollama}}"
117: case "$BACKEND" in
118:   ollama)
119:     INFERENCE_PORT=11434
120:     INFERENCE_LABEL="Ollama"
121:     ;;
122:   omlx)
123:     INFERENCE_PORT=8000
124:     INFERENCE_LABEL="omlx"
125:     ;;
126:   *)
127:     echo "error: unknown --backend value '$BACKEND'. Must be 'ollama' or 'omlx'." >&2
128:     exit 1
129:     ;;
130: esac
131: 
132: if [[ "$BACKEND" == "omlx" ]]; then
133:   OMLX_API_KEY="${OMLX_API_KEY:-}"
134:   if [[ -z "$OMLX_API_KEY" ]]; then
135:     echo "warning: OMLX_API_KEY not set. omlx requests from the container will fail if the server requires auth." >&2
136:   fi
137: fi
138: 
139: # ── constants ────────────────────────────────────────────────────────────────
140: COLIMA_PROFILE="claude-agent"
141: CONTAINER_NAME="claude-agent"
142: IMAGE_TAG="claude-agent:latest"
143: IMAGE_STAMP="$HOME/.claude-agent-image-built"
144: DOCKERFILE_PATH="$(cd "$(dirname "$0")" && pwd)/dockerfiles/claude-agent.Dockerfile"
145: DOCKERFILE_DIR="$(dirname "$DOCKERFILE_PATH")"
146: CLAUDE_CONFIG_DIR="$HOME/.claude-containers/shared"
147: CLAUDE_JSON_FILE="$HOME/.claude-containers/claude.json"
148: OPENCODE_CONFIG_DIR="$HOME/.claude-agent/opencode-config"
149: OPENCODE_DATA_DIR="$HOME/.claude-agent/opencode-data"
150: ALLOWLIST_DIR="$HOME/.claude-agent"
151: ALLOWLIST_FILE="$ALLOWLIST_DIR/allowlist.txt"
152: TINYPROXY_PORT=8888
153: 
154: # ── preflight ────────────────────────────────────────────────────────────────
155: if ! command -v colima &>/dev/null; then
156:   echo "error: 'colima' not found. Install with: brew install colima docker" >&2
157:   exit 1
158: fi
159: if ! command -v docker &>/dev/null; then
160:   echo "error: 'docker' not found. Install with: brew install colima docker" >&2
161:   exit 1
162: fi
163: if [[ ! -d "$PROJECT_DIR" ]]; then
164:   echo "error: project dir '$PROJECT_DIR' does not exist." >&2
165:   exit 1
166: fi
167: if [[ ! -f "$DOCKERFILE_PATH" ]]; then
168:   echo "error: Dockerfile not found at $DOCKERFILE_PATH" >&2
169:   exit 1
170: fi
171: 
172: # ── seed allowlist (first run only) ──────────────────────────────────────────
173: mkdir -p "$ALLOWLIST_DIR"
174: if [[ ! -f "$ALLOWLIST_FILE" ]]; then
175:   cat > "$ALLOWLIST_FILE" <<'ALLOWLIST'
176: # start-agent allowlist — edit on the macOS host.
177: # Apply changes:  start-agent.sh --reload-allowlist
178: # Suffix match:   'github.com' also matches 'api.github.com'.
179: # Comments start with '#'.
180: 
181: # === Anthropic / AI coding agents ===
182: anthropic.com
183: claude.ai
184: opencode.ai
185: 
186: # === Source control & code hosting ===
187: github.com
188: githubusercontent.com
189: githubassets.com
190: codeload.github.com
191: gitlab.com
192: bitbucket.org
193: git-scm.com
194: 
195: # === Package registries ===
196: npmjs.org
197: npmjs.com
198: yarnpkg.com
199: pypi.org
200: pythonhosted.org
201: crates.io
202: rust-lang.org
203: rubygems.org
204: packagist.org
205: pkg.go.dev
206: proxy.golang.org
207: hex.pm
208: 
209: # === Container / image registries ===
210: docker.io
211: docker.com
212: quay.io
213: ghcr.io
214: 
215: # === OS package repos ===
216: debian.org
217: ubuntu.com
218: deb.nodesource.com
219: astral.sh
220: 
221: # === Model & dataset hubs ===
222: huggingface.co
223: ollama.com
224: 
225: # === General web & reference ===
226: wikipedia.org
227: wikimedia.org
228: mozilla.org
229: developer.mozilla.org
230: stackoverflow.com
231: stackexchange.com
232: archive.org
233: 
234: # === Search engines ===
235: google.com
236: duckduckgo.com
237: bing.com
238: 
239: # === Biomedical / life sciences ===
240: ncbi.nlm.nih.gov
241: nlm.nih.gov
242: nih.gov
243: europepmc.org
244: biorxiv.org
245: medrxiv.org
246: plos.org
247: biomedcentral.com
248: elifesciences.org
249: nature.com
250: cell.com
251: science.org
252: sciencemag.org
253: 
254: # === Physical sciences, engineering, math, CS ===
255: arxiv.org
256: semanticscholar.org
257: openalex.org
258: dblp.org
259: acm.org
260: ieee.org
261: aps.org
262: acs.org
263: rsc.org
264: iop.org
265: ams.org
266: paperswithcode.com
267: 
268: # === Social sciences, humanities, general journals ===
269: jstor.org
270: ssrn.com
271: crossref.org
272: doi.org
273: orcid.org
274: sciencedirect.com
275: springer.com
276: link.springer.com
277: springernature.com
278: wiley.com
279: onlinelibrary.wiley.com
280: tandfonline.com
281: sagepub.com
282: cambridge.org
283: oup.com
284: muse.jhu.edu
285: 
286: # === Data repositories & open science ===
287: zenodo.org
288: figshare.com
289: osf.io
290: dataverse.org
291: datadryad.org
292: kaggle.com
293: 
294: # === Government / statistical / standards ===
295: nasa.gov
296: cdc.gov
297: fda.gov
298: nsf.gov
299: census.gov
300: noaa.gov
301: usgs.gov
302: bls.gov
303: loc.gov
304: gov.uk
305: who.int
306: europa.eu
307: 
308: # === News & periodicals for research context ===
309: nytimes.com
310: economist.com
311: ft.com
312: reuters.com
313: apnews.com
314: bbc.com
315: bbc.co.uk
316: washingtonpost.com
317: theatlantic.com
318: newyorker.com
319: wsj.com
320: ALLOWLIST
321:   echo "==> Seeded allowlist at $ALLOWLIST_FILE"
322: fi
323: 
324: # Generate a tinyproxy filter file on the host from the allowlist.
325: # Each non-comment domain becomes an anchored regex: (^|\.)domain\.tld$
326: # which matches the domain itself and any subdomain.
327: generate_filter_file() {
328:   local out="$1"
329:   python3 - "$ALLOWLIST_FILE" > "$out" <<'PYEOF'
330: import re, sys
331: with open(sys.argv[1]) as f:
332:     for raw in f:
333:         line = raw.split('#', 1)[0].strip()
334:         if not line:
335:             continue
336:         escaped = re.escape(line)
337:         print(f"(^|\\.){escaped}$")
338: PYEOF
339: }
340: 
341: # ── Colima VM bring-up ───────────────────────────────────────────────────────
342: colima_profile_running() {
343:   colima status -p "$COLIMA_PROFILE" 2>/dev/null | grep -q 'Running'
344: }
345: 
346: destroy_colima_vm() {
347:   echo "==> Destroying Colima VM '$COLIMA_PROFILE'"
348:   colima delete -p "$COLIMA_PROFILE" --force 2>/dev/null || true
349: }
350: 
351: start_colima_vm() {
352:   echo "==> Starting Colima VM '$COLIMA_PROFILE' ($CLAUDE_AGENT_MEMORY_GB GiB RAM, $CLAUDE_AGENT_CPUS CPUs)"
353:   colima start -p "$COLIMA_PROFILE" \
354:     --vm-type vz \
355:     --runtime docker \
356:     --cpu "$CLAUDE_AGENT_CPUS" \
357:     --memory "$CLAUDE_AGENT_MEMORY_GB" \
358:     --mount-type virtiofs \
359:     --network-address
360: }
361: 
362: # Run a command inside the Colima VM.
363: #
364: # SSH passes the remote command as a single string (the server re-tokenizes
365: # it), and additionally re-quotes whatever single string we hand it, so any
366: # attempt to pre-join with printf %q ends up double-quoted into a single
367: # literal word on the remote side. Bypass colima's argv handling entirely by
368: # piping the command line in via stdin — colima ssh hands stdin straight to
369: # the remote shell with no extra quoting.
370: vm_ssh() {
371:   local cmd="" a
372:   for a in "$@"; do
373:     cmd+=" $(printf '%q' "$a")"
374:   done
375:   vm_sh "$cmd"
376: }
377: 
378: # Variant that takes a complete shell command line as a single argument and
379: # runs it in the VM. Use when you want shell pipelines, redirections, or
380: # heredocs evaluated remotely.
381: vm_sh() {
382:   printf '%s\n' "$1" | colima ssh -p "$COLIMA_PROFILE" -- bash
383: }
384: 
385: # ── --rebuild: optionally destroy the Colima VM before starting it ───────────
386: # Container + image removal happens AFTER the VM is up so that `docker`
387: # actually has something to talk to; deleting the VM itself must happen BEFORE
388: # `colima start` so the start-up path recreates a clean VM.
389: if $REBUILD && ! $RELOAD_ALLOWLIST; then
390:   rm -f "$IMAGE_STAMP"
391:   echo
392:   read -r -p "Also delete and recreate the Colima VM '$COLIMA_PROFILE'? This is NOT reversible. [y/N] " confirm
393:   if [[ "${confirm:-}" =~ ^[Yy]$ ]]; then
394:     destroy_colima_vm
395:   else
396:     echo "==> Keeping Colima VM; only image/container will be rebuilt."
397:   fi
398: fi
399: 
400: # ── ensure the VM is running ─────────────────────────────────────────────────
401: if ! colima_profile_running; then
402:   start_colima_vm
403: else
404:   # Warn if the running VM sizing differs from what was requested.
405:   running_json=$(colima list -p "$COLIMA_PROFILE" --json 2>/dev/null || true)
406:   if [[ -n "$running_json" ]]; then
407:     running_cpu=$(printf '%s' "$running_json" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("cpus",""))' 2>/dev/null || echo "")
408:     running_mem=$(printf '%s' "$running_json" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());m=d.get("memory","");print(m)' 2>/dev/null || echo "")
409:     if [[ -n "$running_cpu" && "$running_cpu" != "$CLAUDE_AGENT_CPUS" ]]; then
410:       echo "warning: running VM has $running_cpu CPUs; requested $CLAUDE_AGENT_CPUS. Use --rebuild to resize." >&2
411:     fi
412:     if [[ -n "$running_mem" && "$running_mem" != *"${CLAUDE_AGENT_MEMORY_GB}"* ]]; then
413:       echo "warning: running VM memory is $running_mem; requested ${CLAUDE_AGENT_MEMORY_GB}GiB. Use --rebuild to resize." >&2
414:     fi
415:   fi
416: fi
417: 
418: # Point the local docker CLI at Colima's socket for this profile.
419: if ! docker context use "colima-$COLIMA_PROFILE" &>/dev/null; then
420:   echo "warning: could not switch docker context to colima-$COLIMA_PROFILE; assuming current context talks to the right daemon." >&2
421: fi
422: 
423: # Now that docker is reachable, honor --rebuild by removing the container and
424: # image from the VM's docker runtime. (The VM itself was handled earlier.)
425: if $REBUILD && ! $RELOAD_ALLOWLIST; then
426:   if docker container inspect "$CONTAINER_NAME" &>/dev/null; then
427:     echo "==> --rebuild: removing container '$CONTAINER_NAME'"
428:     docker rm -f "$CONTAINER_NAME" >/dev/null
429:   fi
430:   if docker image inspect "$IMAGE_TAG" &>/dev/null; then
431:     echo "==> --rebuild: removing image '$IMAGE_TAG'"
432:     docker image rm "$IMAGE_TAG" >/dev/null
433:   fi
434: fi
435: 
436: # ── discover bridge IP, host IP, bridge CIDR inside the VM ───────────────────
437: BRIDGE_IP=$(vm_ssh docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null | tr -d '\r' || true)
438: if [[ -z "$BRIDGE_IP" ]]; then
439:   BRIDGE_IP="172.17.0.1"
440:   echo "warning: could not discover docker bridge gateway; falling back to $BRIDGE_IP" >&2
441: fi
442: BRIDGE_CIDR=$(vm_ssh docker network inspect bridge -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null | tr -d '\r' || true)
443: if [[ -z "$BRIDGE_CIDR" ]]; then
444:   BRIDGE_CIDR="172.17.0.0/16"
445: fi
446: 
447: # HOST_IP from inside the VM: default gateway under Colima's vmnet points at
448: # the macOS host when `--network-address` is set. Run `ip route` remotely and
449: # parse the output locally so we don't have to fight with quoting awk's $3
450: # through the SSH argv-join round trip.
451: HOST_IP=$(vm_ssh ip route show default 2>/dev/null | tr -d '\r' | awk '/^default/ {print $3; exit}' || true)
452: if [[ -z "$HOST_IP" ]]; then
453:   for candidate in host.lima.internal host.docker.internal; do
454:     hosts_line=$(vm_ssh getent hosts "$candidate" 2>/dev/null | tr -d '\r' || true)
455:     if [[ -n "$hosts_line" ]]; then
456:       HOST_IP=$(printf '%s\n' "$hosts_line" | awk '{print $1; exit}')
457:       [[ -n "$HOST_IP" ]] && break
458:     fi
459:   done
460: fi
461: if [[ -z "$HOST_IP" ]]; then
462:   echo "warning: could not determine the macOS host IP from inside the VM; local inference ($INFERENCE_LABEL) will not work." >&2
463:   HOST_IP="127.0.0.1"
464: fi
465: 
466: echo "==> VM network: bridge=$BRIDGE_IP cidr=$BRIDGE_CIDR host=$HOST_IP"
467: 
468: # ── tinyproxy install in VM (idempotent) ─────────────────────────────────────
469: if ! vm_ssh sh -c 'command -v tinyproxy' >/dev/null 2>&1; then
470:   echo "==> Installing tinyproxy in Colima VM"
471:   vm_ssh sudo apt-get update -qq
472:   vm_ssh sudo apt-get install -y tinyproxy
473: fi
474: 
475: # Build tinyproxy config and filter on the host, then push into the VM.
476: TMP_WORK=$(mktemp -d)
477: trap 'rm -rf "$TMP_WORK"' EXIT
478: 
479: cat > "$TMP_WORK/tinyproxy.conf" <<CONF
480: User tinyproxy
481: Group tinyproxy
482: Port $TINYPROXY_PORT
483: Listen $BRIDGE_IP
484: Timeout 600
485: DefaultErrorFile "/usr/share/tinyproxy/default.html"
486: StatFile "/usr/share/tinyproxy/stats.html"
487: LogFile "/var/log/tinyproxy/tinyproxy.log"
488: LogLevel Warning
489: MaxClients 100
490: FilterDefaultDeny Yes
491: Filter "/etc/tinyproxy/filter"
492: FilterExtended Yes
493: FilterURLs No
494: ConnectPort 443
495: ConnectPort 80
496: CONF
497: 
498: generate_filter_file "$TMP_WORK/filter"
499: 
500: # Ship both files into the VM. Embed the tarball as a base64 heredoc inside
501: # the remote script so we don't need to pipe stdin alongside the command.
502: TINYPROXY_PAYLOAD=$( (cd "$TMP_WORK" && tar -cf - tinyproxy.conf filter) | base64 )
503: vm_sh "$(cat <<VMSH
504: set -e
505: base64 -d > /tmp/tinyproxy-stage.tar <<'__B64__'
506: $TINYPROXY_PAYLOAD
507: __B64__
508: sudo tar -xf /tmp/tinyproxy-stage.tar -C /tmp
509: sudo install -m 644 /tmp/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
510: sudo install -m 644 /tmp/filter /etc/tinyproxy/filter
511: rm -f /tmp/tinyproxy.conf /tmp/filter /tmp/tinyproxy-stage.tar
512: VMSH
513: )"
514: 
515: # Enable and (re)start/reload tinyproxy. On first run, enable+start; otherwise
516: # reload to pick up filter changes without interrupting in-flight connections.
517: if $RELOAD_ALLOWLIST; then
518:   vm_ssh sudo systemctl reload tinyproxy 2>/dev/null || vm_ssh sudo systemctl restart tinyproxy
519: else
520:   vm_ssh sudo systemctl enable --now tinyproxy >/dev/null 2>&1 || true
521:   vm_ssh sudo systemctl restart tinyproxy
522: fi
523: 
524: # ── iptables rules in the VM (DOCKER-USER) ───────────────────────────────────
525: cat > "$TMP_WORK/firewall-apply.sh" <<FWEOF
526: #!/bin/sh
527: set -e
528: BRIDGE_IP="$BRIDGE_IP"
529: BRIDGE_CIDR="$BRIDGE_CIDR"
530: HOST_IP="$HOST_IP"
531: TINYPROXY_PORT="$TINYPROXY_PORT"
532: INFERENCE_PORT="$INFERENCE_PORT"
533: 
534: # Ensure DOCKER-USER exists (docker creates it on fresh daemons, but be safe).
535: sudo iptables -N DOCKER-USER 2>/dev/null || true
536: sudo iptables -C FORWARD -j DOCKER-USER 2>/dev/null || sudo iptables -I FORWARD 1 -j DOCKER-USER
537: 
538: # Flush our previous rules (identified by comment). Walk the chain by line
539: # number in reverse so deletions don't shift indices underneath us.
540: sudo iptables -S DOCKER-USER | awk '/--comment claude-agent/{print NR-1}' | sort -rn | while read -r i; do
541:   [ -z "\$i" ] || sudo iptables -D DOCKER-USER "\$i" 2>/dev/null || true
542: done
543: # Fallback cleanup by explicit rule spec (in case the awk approach misses).
544: while sudo iptables -S DOCKER-USER 2>/dev/null | grep -q 'claude-agent'; do
545:   rule=\$(sudo iptables -S DOCKER-USER | grep -m1 'claude-agent' | sed 's/^-A/-D/')
546:   [ -z "\$rule" ] && break
547:   # shellcheck disable=SC2086
548:   sudo iptables \$rule || break
549: done
550: 
551: # Established/related first.
552: sudo iptables -I DOCKER-USER 1 -s "\$BRIDGE_CIDR" \
553:   -m conntrack --ctstate ESTABLISHED,RELATED \
554:   -m comment --comment claude-agent -j RETURN
555: 
556: # Container -> in-VM tinyproxy.
557: sudo iptables -I DOCKER-USER 2 -s "\$BRIDGE_CIDR" -d "\$BRIDGE_IP" \
558:   -p tcp --dport "\$TINYPROXY_PORT" \
559:   -m comment --comment claude-agent -j RETURN
560: 
561: # Container -> macOS host inference server (Ollama / omlx).
562: sudo iptables -I DOCKER-USER 3 -s "\$BRIDGE_CIDR" -d "\$HOST_IP" \
563:   -p tcp --dport "\$INFERENCE_PORT" \
564:   -m comment --comment claude-agent -j RETURN
565: 
566: # Container -> in-VM DNS (dockerd's embedded resolver lives on 127.0.0.11,
567: # which the container reaches via its own netns, but some name-lookup paths
568: # use the bridge gateway as resolver. Allow UDP/TCP 53 to the bridge IP.)
569: sudo iptables -I DOCKER-USER 4 -s "\$BRIDGE_CIDR" -d "\$BRIDGE_IP" \
570:   -p udp --dport 53 \
571:   -m comment --comment claude-agent -j RETURN
572: sudo iptables -I DOCKER-USER 5 -s "\$BRIDGE_CIDR" -d "\$BRIDGE_IP" \
573:   -p tcp --dport 53 \
574:   -m comment --comment claude-agent -j RETURN
575: 
576: # Default-deny everything else from the bridge.
577: sudo iptables -A DOCKER-USER -s "\$BRIDGE_CIDR" \
578:   -m comment --comment claude-agent \
579:   -j REJECT --reject-with icmp-admin-prohibited
580: FWEOF
581: chmod +x "$TMP_WORK/firewall-apply.sh"
582: 
583: echo "==> Applying firewall rules in VM"
584: FIREWALL_PAYLOAD=$(base64 < "$TMP_WORK/firewall-apply.sh")
585: vm_sh "$(cat <<VMSH
586: set -e
587: base64 -d > /tmp/firewall-apply.sh <<'__B64__'
588: $FIREWALL_PAYLOAD
589: __B64__
590: sh /tmp/firewall-apply.sh
591: rm -f /tmp/firewall-apply.sh
592: VMSH
593: )"
594: 
595: # ── --reload-allowlist: fast-path exit ───────────────────────────────────────
596: if $RELOAD_ALLOWLIST; then
597:   entry_count=$(grep -cv -E '^\s*(#|$)' "$ALLOWLIST_FILE" || echo 0)
598:   echo "==> Allowlist reloaded ($entry_count entries)"
599:   rm -rf "$TMP_WORK"
600:   trap - EXIT
601:   exit 0
602: fi
603: 
604: # ── inference backend preflight (non-fatal) ──────────────────────────────────
605: echo "==> Probing $INFERENCE_LABEL at http://$HOST_IP:$INFERENCE_PORT from inside VM"
606: case "$BACKEND" in
607:   ollama)
608:     if ! vm_ssh curl -sf --max-time 3 "http://$HOST_IP:$INFERENCE_PORT/api/tags" >/dev/null 2>&1; then
609:       cat >&2 <<WARN
610: warning: Ollama not reachable at http://$HOST_IP:$INFERENCE_PORT from inside the
611: Colima VM. Ensure Ollama is running on the macOS host and bound to 0.0.0.0.
612: On the host, run once:
613:     launchctl setenv OLLAMA_HOST 0.0.0.0:$INFERENCE_PORT
614: and restart the Ollama app. Continuing without local inference.
615: WARN
616:     fi
617:     ;;
618:   omlx)
619:     OMLX_CURL_ARGS=(-sf --max-time 3)
620:     if [[ -n "${OMLX_API_KEY:-}" ]]; then
621:       OMLX_CURL_ARGS+=(-H "Authorization: Bearer $OMLX_API_KEY")
622:     fi
623:     if ! vm_ssh curl "${OMLX_CURL_ARGS[@]}" "http://$HOST_IP:$INFERENCE_PORT/v1/models" >/dev/null 2>&1; then
624:       cat >&2 <<WARN
625: warning: omlx not reachable at http://$HOST_IP:$INFERENCE_PORT from inside the
626: Colima VM. Ensure omlx is running on the host with:
627:     omlx serve --model-dir ~/models
628: or via: brew services start omlx
629: Continuing without local inference.
630: WARN
631:     fi
632:     ;;
633: esac
634: 
635: # ── build the image if missing ───────────────────────────────────────────────
636: if ! docker image inspect "$IMAGE_TAG" &>/dev/null; then
637:   echo "==> Building $IMAGE_TAG from $DOCKERFILE_PATH"
638:   # Build uses --network=host so RUN steps share the VM's network namespace
639:   # and bypass the DOCKER-USER bridge firewall. Without this, apt/curl/npm
640:   # would hit "no route to host" because the default-deny rule rejects
641:   # everything from the bridge CIDR except the tinyproxy allowlist path, and
642:   # legacy-builder build ARGs don't reliably forward HTTP_PROXY to every tool
643:   # (apt, for one, only consults lowercase http_proxy). Runtime containers
644:   # still attach to the bridge and are firewalled normally.
645:   docker build --network=host \
646:     -t "$IMAGE_TAG" -f "$DOCKERFILE_PATH" "$DOCKERFILE_DIR"
647:   date +%s > "$IMAGE_STAMP"
648: elif [[ -f "$IMAGE_STAMP" ]]; then
649:   BUILD_TIME=$(cat "$IMAGE_STAMP")
650:   NOW=$(date +%s)
651:   AGE_DAYS=$(( (NOW - BUILD_TIME) / 86400 ))
652:   if (( AGE_DAYS >= 30 )); then
653:     echo "==> Warning: $IMAGE_TAG is ${AGE_DAYS} days old. Run with --rebuild to refresh."
654:   fi
655: fi
656: 
657: # ── host-side persistent state dirs ──────────────────────────────────────────
658: mkdir -p "$CLAUDE_CONFIG_DIR" "$OPENCODE_CONFIG_DIR" "$OPENCODE_DATA_DIR"
659: [[ -f "$CLAUDE_JSON_FILE" ]] || echo '{}' > "$CLAUDE_JSON_FILE"
660: 
661: # ── inject project .claude/settings.local.json ───────────────────────────────
662: PROJECT_SETTINGS_FILE="$PROJECT_DIR/.claude/settings.local.json"
663: if [[ -f "$PROJECT_SETTINGS_FILE" ]]; then
664:   python3 - "$PROJECT_SETTINGS_FILE" << 'PYEOF'
665: import json, sys
666: path = sys.argv[1]
667: with open(path) as f:
668:     data = json.load(f)
669: changed = False
670: if 'theme' not in data:
671:     data['theme'] = 'light'
672:     changed = True
673: if isinstance(data.get('sandbox'), bool):
674:     data['sandbox'] = {"enabled": True, "autoAllowBashIfSandboxed": True}
675:     changed = True
676: sb = data.setdefault('sandbox', {})
677: fs = sb.setdefault('filesystem', {})
678: aw = fs.setdefault('allowWrite', [])
679: for p in ['/tmp/uv-cache', '$TMPDIR/uv-cache', '/tmp/.venv', '$TMPDIR/.venv']:
680:     if p not in aw:
681:         aw.append(p)
682:         changed = True
683: if sb.get('failIfUnavailable') is not True:
684:     sb['failIfUnavailable'] = True
685:     changed = True
686: if sb.get('allowUnsandboxedCommands') is not False:
687:     sb['allowUnsandboxedCommands'] = False
688:     changed = True
689: if changed:
690:     with open(path, 'w') as f:
691:         json.dump(data, f, indent=2)
692:         f.write('\n')
693:     print(f"==> Migrated {path}")
694: PYEOF
695: else
696:   mkdir -p "$PROJECT_DIR/.claude"
697:   cat > "$PROJECT_SETTINGS_FILE" << 'JSONEOF'
698: {
699:   "theme": "light",
700:   "sandbox": {
701:     "enabled": true,
702:     "autoAllowBashIfSandboxed": true,
703:     "failIfUnavailable": true,
704:     "allowUnsandboxedCommands": false,
705:     "filesystem": {
706:       "allowWrite": ["/tmp/uv-cache", "$TMPDIR/uv-cache", "/tmp/.venv", "$TMPDIR/.venv"]
707:     }
708:   }
709: }
710: JSONEOF
711:   echo "==> Created $PROJECT_SETTINGS_FILE"
712: fi
713: 
714: # ── inject global ~/.claude/settings.json ────────────────────────────────────
715: GLOBAL_SETTINGS_FILE="$CLAUDE_CONFIG_DIR/settings.json"
716: if [[ -f "$GLOBAL_SETTINGS_FILE" ]]; then
717:   python3 - "$GLOBAL_SETTINGS_FILE" << 'PYEOF'
718: import json, sys
719: path = sys.argv[1]
720: with open(path) as f:
721:     data = json.load(f)
722: changed = False
723: if data.get('showThinkingSummaries') is not True:
724:     data['showThinkingSummaries'] = True
725:     changed = True
726: if data.get('coauthorTag') != 'none':
727:     data['coauthorTag'] = 'none'
728:     changed = True
729: if data.get('effortLevel') != 'medium':
730:     data['effortLevel'] = 'medium'
731:     changed = True
732: if changed:
733:     with open(path, 'w') as f:
734:         json.dump(data, f, indent=2)
735:         f.write('\n')
736: PYEOF
737: else
738:   echo '{"showThinkingSummaries": true, "coauthorTag": "none", "effortLevel": "medium"}' > "$GLOBAL_SETTINGS_FILE"
739: fi
740: 
741: # ── inject OpenCode config (inference provider) ─────────────────────────────
742: OPENCODE_CONFIG_FILE="$OPENCODE_CONFIG_DIR/opencode.json"
743: python3 - "$OPENCODE_CONFIG_FILE" "$BACKEND" "http://$HOST_IP:$INFERENCE_PORT/v1" "${OMLX_API_KEY:-}" << 'PYEOF'
744: import json, os, sys
745: path, backend, base_url = sys.argv[1], sys.argv[2], sys.argv[3]
746: api_key = sys.argv[4] if len(sys.argv) > 4 else ""
747: if os.path.exists(path):
748:     with open(path) as f:
749:         try:
750:             data = json.load(f)
751:         except json.JSONDecodeError:
752:             data = {}
753: else:
754:     data = {}
755: data.setdefault('$schema', 'https://opencode.ai/config.json')
756: providers = data.setdefault('provider', {})
757: if backend == "ollama":
758:     entry = providers.setdefault('ollama', {
759:         "npm": "@ai-sdk/openai-compatible",
760:         "name": "Ollama (host)",
761:         "options": {},
762:         "models": {},
763:     })
764:     entry.setdefault('options', {})['baseURL'] = base_url
765: elif backend == "omlx":
766:     entry = providers.setdefault('omlx', {
767:         "npm": "@ai-sdk/openai-compatible",
768:         "name": "omlx (host)",
769:         "options": {},
770:         "models": {},
771:     })
772:     opts = entry.setdefault('options', {})
773:     opts['baseURL'] = base_url
774:     if api_key:
775:         opts['apiKey'] = api_key
776: os.makedirs(os.path.dirname(path), exist_ok=True)
777: with open(path, 'w') as f:
778:     json.dump(data, f, indent=2)
779:     f.write('\n')
780: PYEOF
781: 
782: # ── skills sync (new-container path only) ────────────────────────────────────
783: sync_skills() {
784:   local url="${CLAUDE_SKILLS_ARCHIVE_URL:-https://github.com/aryehj/start-claude/archive/refs/heads/main.tar.gz}"
785:   local dest="$CLAUDE_CONFIG_DIR/skills"
786:   echo "==> Syncing skills from upstream into $dest"
787:   mkdir -p "$dest"
788:   local tmp
789:   tmp=$(mktemp -d)
790:   if curl -fsSL "$url" -o "$tmp/archive.tar.gz" \
791:      && tar -xzf "$tmp/archive.tar.gz" -C "$tmp"; then
792:     local src
793:     src=$(find "$tmp" -maxdepth 2 -type d -name skills | head -n1)
794:     if [[ -n "$src" && -d "$src" ]]; then
795:       for skill_path in "$src"/*/; do
796:         [[ -d "$skill_path" ]] || continue
797:         local name
798:         name=$(basename "$skill_path")
799:         rm -rf "$dest/$name"
800:         cp -R "$skill_path" "$dest/$name"
801:         echo "    injected skill: $name"
802:       done
803:     else
804:       echo "    warning: no skills/ directory in upstream archive" >&2
805:     fi
806:   else
807:     echo "    warning: failed to fetch skills from $url (continuing)" >&2
808:   fi
809:   rm -rf "$tmp"
810: }
811: 
812: # ── container run / reattach ─────────────────────────────────────────────────
813: DOCKER_ENV_ARGS=(
814:   -e "TERM=${TERM:-xterm-256color}"
815:   -e "COLORTERM=${COLORTERM:-}"
816:   -e "TERM_PROGRAM=${TERM_PROGRAM:-}"
817:   -e "CLAUDE_CODE_DISABLE_1M_CONTEXT=1"
818:   -e "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1"
819:   -e "GIT_AUTHOR_NAME=$GIT_USER_NAME"
820:   -e "GIT_AUTHOR_EMAIL=$GIT_USER_EMAIL"
821:   -e "GIT_COMMITTER_NAME=$GIT_USER_NAME"
822:   -e "GIT_COMMITTER_EMAIL=$GIT_USER_EMAIL"
823:   -e "HTTPS_PROXY=http://$BRIDGE_IP:$TINYPROXY_PORT"
824:   -e "HTTP_PROXY=http://$BRIDGE_IP:$TINYPROXY_PORT"
825:   -e "NO_PROXY=localhost,127.0.0.1,$BRIDGE_IP,$HOST_IP"
826: )
827: case "$BACKEND" in
828:   ollama)
829:     DOCKER_ENV_ARGS+=(-e "OLLAMA_HOST=http://$HOST_IP:$INFERENCE_PORT")
830:     ;;
831:   omlx)
832:     DOCKER_ENV_ARGS+=(-e "OMLX_HOST=http://$HOST_IP:$INFERENCE_PORT")
833:     if [[ -n "${OMLX_API_KEY:-}" ]]; then
834:       DOCKER_ENV_ARGS+=(-e "OMLX_API_KEY=$OMLX_API_KEY")
835:     fi
836:     ;;
837: esac
838: 
839: attach_existing() {
840:   echo "==> Attaching to existing container '$CONTAINER_NAME'"
841:   docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true
842:   rm -rf "$TMP_WORK"
843:   trap - EXIT
844:   exec docker exec -it -w "$PROJECT_DIR" "${DOCKER_ENV_ARGS[@]}" "$CONTAINER_NAME" /bin/bash
845: }
846: 
847: if docker container inspect "$CONTAINER_NAME" &>/dev/null; then
848:   # Check that the existing mount matches $PROJECT_DIR. If not, recreate.
849:   existing_mount=$(docker container inspect -f '{{range .Mounts}}{{if eq .Destination "'"$PROJECT_DIR"'"}}{{.Source}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
850:   if [[ "$existing_mount" == "$PROJECT_DIR" ]]; then
851:     # In-use detection: if another interactive shell is already attached, refuse.
852:     running_state=$(docker container inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")
853:     if [[ "$running_state" == "true" ]]; then
854:       if docker top "$CONTAINER_NAME" 2>/dev/null | grep -q '/bin/bash'; then
855:         existing_proj=$(docker container inspect -f '{{range .Mounts}}{{if ne .Destination "/root/.claude"}}{{if ne .Destination "/root/.claude.json"}}{{if ne .Destination "/root/.config/opencode"}}{{if ne .Destination "/root/.local/share/opencode"}}{{.Destination}} {{end}}{{end}}{{end}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null | awk '{print $1}')
856:         echo "warning: container '$CONTAINER_NAME' appears to already have an interactive session (project: $existing_proj). Attaching anyway; two shells sharing state may be confusing." >&2
857:       fi
858:     fi
859:     attach_existing
860:   else
861:     echo "==> Project dir changed; recreating container"
862:     docker rm -f "$CONTAINER_NAME" >/dev/null
863:   fi
864: fi
865: 
866: # Fresh container: sync skills first.
867: sync_skills
868: 
869: echo "==> Creating container '$CONTAINER_NAME'"
870: echo "    project : $PROJECT_DIR  →  $PROJECT_DIR"
871: echo "    proxy   : http://$BRIDGE_IP:$TINYPROXY_PORT  (allowlist: $ALLOWLIST_FILE)"
872: echo "    inference: $INFERENCE_LABEL at http://$HOST_IP:$INFERENCE_PORT"
873: 
874: rm -rf "$TMP_WORK"
875: trap - EXIT
876: exec docker run \
877:   --name "$CONTAINER_NAME" \
878:   -it \
879:   --memory "${CLAUDE_AGENT_MEMORY_GB}g" \
880:   --cpus "$CLAUDE_AGENT_CPUS" \
881:   --add-host=host.docker.internal:host-gateway \
882:   -v "$PROJECT_DIR:$PROJECT_DIR" \
883:   -v "$CLAUDE_CONFIG_DIR:/root/.claude" \
884:   -v "$CLAUDE_JSON_FILE:/root/.claude.json" \
885:   -v "$OPENCODE_CONFIG_DIR:/root/.config/opencode" \
886:   -v "$OPENCODE_DATA_DIR:/root/.local/share/opencode" \
887:   -w "$PROJECT_DIR" \
888:   "${DOCKER_ENV_ARGS[@]}" \
889:   "$IMAGE_TAG" \
890:   bash

(End of file - total 890 lines)
</content>
```

---

## Assistant (Build · Qwen3.5-35B-A3B-6bit · 46.4s)

_Thinking:_


Let me also check the ADR.md to understand any related architecture decisions about networking and inference.



**Tool: read**

**Input:**
```json
{
  "filePath": "/Users/aryehj/Repos/start-claude/ADR.md"
}
```

**Output:**
```
<path>/Users/aryehj/Repos/start-claude/ADR.md</path>
<type>file</type>
<content>
1: # Architecture Decision Records
2: 
3: ## ADR-001: Set `UV_CACHE_DIR` to a writable path in the container environment
4: 
5: **Date:** 2026-04-03
6: **Status:** Accepted
7: 
8: ### Context
9: 
10: Inside the container, Claude Code's sandbox restricts `/root/.cache` to
11: read-only. UV defaults to `/root/.cache/uv` for its cache directory. Every
12: `uv run` invocation fails with:
13: 
14: ```
15: error: Failed to initialize cache at `/root/.cache/uv`
16:   Caused by: failed to create directory `/root/.cache/uv`: Read-only file system (os error 30)
17: ```
18: 
19: Claude Code would then spend multiple tool calls trying to work around this
20: (prefixing `UV_CACHE_DIR=/tmp/...` on individual commands), burning context and
21: often still failing when it forgot the prefix.
22: 
23: ### Decision
24: 
25: Set `UV_CACHE_DIR=/tmp/uv-cache` at multiple levels:
26: 
27: 1. **`CONTAINER_ENV` (`-e UV_CACHE_DIR=/tmp/uv-cache`)** — passed to both
28:    `container run` and `container exec`. This is the critical path: Claude Code
29:    inherits the container's environment, and all `bash -c` subprocesses it spawns
30:    inherit it in turn.
31: 2. **`/root/.bashrc`** — interactive login shells inside the container.
32: 3. **`/etc/environment`** — PAM-based login sessions.
33: 4. **`/etc/profile.d/uv-cache.sh`** — login shells sourcing profile.d.
34: 
35: Layer 1 is sufficient for Claude Code's sandbox. Layers 2-4 are
36: belt-and-suspenders for manual shell sessions.
37: 
38: ### Consequences
39: 
40: - `uv run`, `uv pip`, and `uvx` work out of the box inside the sandbox with no
41:   manual `UV_CACHE_DIR` prefix.
42: - The cache lives under `/tmp/uv-cache`, which is writable but ephemeral — it
43:   does not survive container restarts. This is acceptable because UV
44:   re-downloads packages quickly and the cache is not large.
45: - Requires `--rebuild` to take effect on existing containers.
46: 
47: ## ADR-002: Add `/tmp/uv-cache` to sandbox `filesystem.allowWrite`
48: 
49: **Date:** 2026-04-03
50: **Status:** Accepted
51: 
52: ### Context
53: 
54: ADR-001 set `UV_CACHE_DIR=/tmp/uv-cache` as an environment variable, but this
55: alone was insufficient. Claude Code's bubblewrap sandbox mounts most of `/tmp`
56: as read-only. The env var told uv *where* to write, but the sandbox prevented
57: the actual writes:
58: 
59: ```
60: error: Failed to initialize cache at `/tmp/uv-cache`
61:   Caused by: failed to create directory `/tmp/uv-cache`: Read-only file system (os error 30)
62: ```
63: 
64: Setting `$TMPDIR` (`/tmp/claude`) also failed — the sandbox restricts that path
65: for its own use.
66: 
67: ### Decision
68: 
69: Add `/tmp/uv-cache` to `sandbox.filesystem.allowWrite` in each project's
70: `.claude/settings.local.json`. The script now:
71: 
72: 1. **New projects:** The generated `settings.local.json` includes the
73:    `filesystem.allowWrite` array with `/tmp/uv-cache`.
74: 2. **Existing projects:** The migration block in `start-claude.sh` checks for
75:    the presence of `/tmp/uv-cache` in `allowWrite` and adds it if missing,
76:    alongside the existing boolean→object migration.
77: 
78: ### Consequences
79: 
80: - `uv` commands work inside the sandbox without any manual workarounds.
81: - The env var (`UV_CACHE_DIR`) and sandbox permission (`filesystem.allowWrite`)
82:   are now kept in sync by the same script.
83: - Existing containers need their `settings.local.json` updated (happens
84:   automatically on next `start-claude.sh` run) but do not require `--rebuild`.
85: 
86: ## ADR-004: Resolve `UV_CACHE_DIR` dynamically via `$TMPDIR`
87: 
88: **Date:** 2026-04-04
89: **Status:** Accepted (supersedes parts of ADR-001 and ADR-002)
90: 
91: ### Context
92: 
93: ADR-001 hardcoded `UV_CACHE_DIR=/tmp/uv-cache` and passed it as a static env var
94: via `CONTAINER_ENV`. ADR-002 added `/tmp/uv-cache` to the sandbox `allowWrite` list.
95: In practice, Claude Code's bubblewrap sandbox mounts `/tmp` read-only at the
96: filesystem level before the allowlist is evaluated, so writes to `/tmp/uv-cache`
97: still failed. Users reported that `uv run --with` (which creates temporary
98: virtual environments) also failed for the same reason.
99: 
100: The sandbox sets `$TMPDIR` to a guaranteed-writable directory at runtime. Using
101: this instead of hardcoding `/tmp` resolves both issues.
102: 
103: ### Decision
104: 
105: 1. **Remove `UV_CACHE_DIR` from `CONTAINER_ENV`.** No longer pass it as a static
106:    container env var.
107: 2. **Set `UV_CACHE_DIR="${TMPDIR:-/tmp}/uv-cache"` in `.bashrc` and
108:    `/etc/profile.d/uv-cache.sh`.** The variable resolves at shell startup to the
109:    sandbox-provided writable temp directory. Falls back to `/tmp` for interactive
110:    use outside the sandbox.
111: 3. **Drop `/etc/environment`.** It does not support shell variable expansion, so
112:    it cannot use `$TMPDIR`.
113: 4. **Add `$TMPDIR/uv-cache` to sandbox `filesystem.allowWrite`** alongside the
114:    existing `/tmp/uv-cache` entry, as a belt-and-suspenders measure.
115: 5. **`mkdir -p` on every shell startup.** Each sandbox session may get a fresh
116:    `$TMPDIR`, so the `uv-cache` subdirectory is created at profile load time.
117: 
118: ### Consequences
119: 
120: - `uv run`, `uv pip`, `uvx`, and `uv run --with` all work inside the sandbox
121:   without manual workarounds.
122: - The cache is ephemeral per sandbox session (each may use a different `$TMPDIR`),
123:   which is acceptable — UV re-downloads quickly.
124: - Requires `--rebuild` to bake the new profile scripts into the image. Existing
125:   containers with the old static env var will continue to use `/tmp/uv-cache`
126:   (unchanged behavior) until rebuilt.
127: 
128: ## ADR-003: Set theme in project-level settings, not global `.claude.json`
129: 
130: **Date:** 2026-04-03
131: **Status:** Accepted
132: 
133: ### Context
134: 
135: The script previously set `"theme": "light"` by overwriting `/root/.claude.json`
136: on every `container run`:
137: 
138: ```bash
139: bash -c 'echo "{\"theme\":\"light\"}" > /root/.claude.json && exec bash'
140: ```
141: 
142: `/root/.claude.json` is a top-level file, separate from the `/root/.claude/`
143: directory that is volume-mounted for credential persistence. Because `.claude.json`
144: was not persisted, it was ephemeral — but it also meant the overwrite destroyed
145: any auth-related state that Claude Code wrote there (e.g. `oauthAccount`,
146: `hasCompletedOnboarding`), potentially contributing to re-login prompts on
147: container recreation.
148: 
149: Mounting `.claude.json` as a second volume was considered but adds complexity
150: (seeding the file, merging settings across versions).
151: 
152: ### Decision
153: 
154: Move the theme setting to the project-level `settings.local.json`, which the
155: script already creates and migrates. The `container run` entrypoint is now plain
156: `bash` with no file writes.
157: 
158: - New projects get `"theme": "light"` in the generated `settings.local.json`.
159: - Existing projects get it added by the migration block (same pattern as the
160:   sandbox and `allowWrite` migrations).
161: 
162: ### Consequences
163: 
164: - `.claude.json` is no longer touched by the script. Claude Code manages it
165:   internally; its contents are ephemeral to each container but no longer
166:   clobbered on creation.
167: - Theme is project-scoped, which is how `settings.local.json` is designed to
168:   work. Different projects could theoretically use different themes.
169: - Auth persistence depends solely on the `~/.claude/` volume mount (which
170:   contains `.credentials.json`). If `.claude.json` turns out to hold state
171:   required for session continuity, a separate volume mount would need to be
172:   added. *(This caveat materialised — see ADR-006.)*
173: 
174: ## ADR-005: Sync skills from upstream repo on new container build
175: 
176: **Date:** 2026-04-04
177: **Status:** Accepted
178: 
179: ### Context
180: 
181: The `skills/` directory in this repo is the authoritative copy of the Claude
182: Code skills we want available in every container. Previously, getting them
183: into `~/.claude/skills/` was manual: the README instructed users to `cp -r`
184: or symlink each skill into place. Skills drift quickly, and anyone using an
185: older copy of the shared `.claude` mount had no way of knowing their skills
186: were stale.
187: 
188: The shared mount (`~/.claude-containers/shared/` → `/root/.claude`) already
189: centralises skill storage across containers, so pushing updated skills into
190: it once per container build propagates to every project.
191: 
192: ### Decision
193: 
194: Right before `container run` creates a new container, the script downloads
195: the upstream repo tarball (default: `main` branch of `aryehj/start-claude`,
196: overridable via `CLAUDE_SKILLS_ARCHIVE_URL`), extracts it, and for each
197: directory under the archive's `skills/`:
198: 
199: 1. `rm -rf` the matching directory in `~/.claude-containers/shared/skills/`
200: 2. `cp -R` the upstream version in its place
201: 
202: Skills that exist locally but are absent upstream are left untouched. Fetch
203: or extraction failures produce a warning and container creation proceeds.
204: 
205: ### Consequences
206: 
207: - Every new container (including `--rebuild`, which deletes the existing
208:   container first) picks up the latest upstream skills automatically.
209: - Users can iterate on a skill locally (e.g. edit files under
210:   `~/.claude-containers/shared/skills/plan/`) only until the next container
211:   build clobbers it — local-only edits to synced skills are disposable. Users
212:   who want durable customisations should fork the repo and point
213:   `CLAUDE_SKILLS_ARCHIVE_URL` at their fork, or name their skill differently
214:   so it doesn't collide with an upstream name.
215: - The sync requires network access at container-creation time. Offline
216:   builds still work but skip the sync with a warning.
217: - Re-attaching to an existing container does not trigger a sync, matching
218:   the existing invariant that image/container state only changes on new
219:   builds.
220: 
221: ## ADR-006: Persist `/root/.claude.json` via a file bind-mount
222: 
223: **Date:** 2026-04-04
224: **Status:** Accepted (addresses caveat in ADR-003)
225: 
226: ### Context
227: 
228: ADR-003 moved the theme setting out of `.claude.json` so the script stopped
229: clobbering that file, but left its persistence unaddressed — the file was
230: ephemeral per container. This turned out to break authentication across
231: `--rebuild`: Claude Code stores `oauthAccount` and other auth state in
232: `~/.claude.json`, not just in `~/.claude/.credentials.json`. When `--rebuild`
233: destroyed the container, the fresh container had `.credentials.json` mounted
234: in (via the `~/.claude/` volume) but no `.claude.json`, and Claude Code
235: prompted for re-login every time.
236: 
237: The user confirmed they are container-only (no host Claude install), so
238: separation between host and container `.claude.json` state is not needed.
239: 
240: ### Decision
241: 
242: Add a second bind mount: the host file `~/.claude-containers/claude.json`
243: maps to `/root/.claude.json` in the container. The script creates and
244: initializes the file to `{}` on the host if it does not already exist, so the
245: bind mount resolves to a file rather than an auto-created directory.
246: 
247: The file sits alongside (not inside) `~/.claude-containers/shared/`, because
248: `shared/` is already mounted at `/root/.claude`, and nesting `claude.json`
249: inside it would expose it at the wrong path (`/root/.claude/claude.json`).
250: 
251: ### Consequences
252: 
253: - `claude login` now survives `--rebuild`. Both halves of Claude Code's auth
254:   state (`~/.claude/.credentials.json` and `~/.claude.json`) are persisted on
255:   the host.
256: - All containers share the same `.claude.json`, matching the existing
257:   "shared across containers" model for `~/.claude/`. Per-project config
258:   still lives in each project's `.claude/settings.local.json`, unaffected.
259: - Relies on Apple Containers supporting file-level bind mounts
260:   (`-v host_file:container_file`). If a future version drops that support,
261:   fall back to mounting the parent directory or seeding `.claude.json` into
262:   `~/.claude-containers/shared/` with a symlink.
263: - To reset auth state: delete `~/.claude-containers/claude.json` and
264:   `~/.claude-containers/shared/.credentials.json`, then re-run
265:   `claude login`.
266: 
267: ## ADR-007: Redirect UV project venv via `UV_PROJECT_ENVIRONMENT`
268: 
269: **Date:** 2026-04-07
270: **Status:** Accepted
271: 
272: ### Context
273: 
274: Projects mounted into containers often have a `.venv` directory created on the
275: host (macOS/ARM). The Claude Code agent inside the Linux container sees this
276: `.venv`, attempts to use it, and fails — the binaries are wrong platform, paths
277: are wrong, and the sandbox may block writes. The agent then wastes cycles trying
278: workarounds (manual `--python` flags, prefixing env vars on individual commands,
279: or trying to `rm -rf` and recreate the venv in place).
280: 
281: This is a well-known problem in any "mount host project into dev container"
282: workflow. UV provides `UV_PROJECT_ENVIRONMENT` for exactly this use case: it
283: overrides where `uv sync`, `uv run`, etc. create the project virtual
284: environment (default: `.venv` in the project root).
285: 
286: ### Decision
287: 
288: 1. **Set `UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/.venv"` in `.bashrc` and
289:    `/etc/profile.d/uv-cache.sh`.** Same dynamic `$TMPDIR` resolution pattern
290:    as `UV_CACHE_DIR` (ADR-004). The variable resolves at shell startup to the
291:    sandbox-provided writable temp directory.
292: 2. **`mkdir -p` on every shell startup** to ensure the directory exists (same
293:    pattern as `UV_CACHE_DIR`).
294: 3. **Add `/tmp/.venv` and `$TMPDIR/.venv` to `sandbox.filesystem.allowWrite`**
295:    in each project's `.claude/settings.local.json`, alongside the existing UV
296:    cache entries. Both the default settings block and the migration block are
297:    updated.
298: 
299: UV intentionally keeps `UV_PROJECT_ENVIRONMENT` as a user-level environment
300: variable rather than a `pyproject.toml` / `uv.toml` setting — a project should
301: not be able to direct installs to arbitrary paths on the user's machine. This
302: makes the env-var approach the canonical solution.
303: 
304: ### Consequences
305: 
306: - `uv sync`, `uv run`, `uv add`, etc. create and use a Linux venv in `$TMPDIR/.venv`
307:   instead of the project's `.venv`. The host `.venv` is completely ignored, not
308:   deleted.
309: - The venv is ephemeral per sandbox session (each may use a different `$TMPDIR`),
310:   so `uv sync` must run once per session. This is acceptable — UV installs from
311:   cache quickly, and the cache itself persists across commands within a session.
312: - Requires `--rebuild` to bake the new profile scripts into the image.
313: - Existing project `settings.local.json` files pick up the new `allowWrite`
314:   entries automatically on next `start-claude.sh` run (no rebuild needed for
315:   the sandbox permissions, only for the env var).
316: 
317: ## ADR-010: start-agent: in-VM tinyproxy + DOCKER-USER iptables for egress allowlist
318: 
319: **Date:** 2026-04-15
320: **Status:** Accepted
321: 
322: ### Context
323: 
324: `start-agent.sh` (sibling to `start-claude.sh`) runs both Claude Code and
325: OpenCode in a shared Colima-hosted docker container, and needs a network
326: egress allowlist that the in-container LLM cannot modify. Several placements
327: were possible:
328: 
329: 1. **Host-side pf + host-side proxy.** Strongest isolation — the rules live
330:    two VM boundaries away from the container. But pfctl anchors, launchd
331:    plists for the proxy, vmnet CIDR discovery, and `sudo` for every reload
332:    all bleed setup pain into the user's macOS install.
333: 2. **In-container iptables + in-container proxy.** Trivially simple, but
334:    trivially bypassable: the LLM is `root` inside the container by default,
335:    and a single `iptables -F` (or editing the proxy config) undoes it.
336: 3. **In-VM iptables (DOCKER-USER) + in-VM tinyproxy.** Rules live outside
337:    the container's network namespace, inside a Lima VM the container cannot
338:    reach without a container escape plus VM root — two jumps outside any
339:    realistic behavioral threat model, with none of the host-side setup
340:    overhead of option 1.
341: 
342: ### Decision
343: 
344: Option 3.
345: 
346: - **tinyproxy** runs as a systemd service inside the Colima VM, bound to the
347:   docker bridge gateway IP (discovered at script time via
348:   `docker network inspect bridge`). Config: `FilterDefaultDeny Yes`,
349:   `FilterExtended Yes`, `FilterURLs No`, and a `Filter` file of anchored
350:   regexes (`(^|\.)domain\.tld$`) generated on the host from
351:   `~/.claude-agent/allowlist.txt`. The filter applies to the hostname
352:   portion of HTTPS `CONNECT` requests as well as plain HTTP.
353: - **iptables** rules are inserted into docker's `DOCKER-USER` chain, which
354:   the docker daemon explicitly leaves alone on restart. Four RETURN rules
355:   (established/related, proxy port, host Ollama port, bridge DNS) followed
356:   by a catch-all REJECT for traffic from the bridge CIDR. Every rule is
357:   tagged with `-m comment --comment claude-agent` so the reload path can
358:   remove only its own rules without disturbing any other user rules.
359: - **Re-apply on every run** instead of persisting. The firewall script is
360:   regenerated on the host and shipped into the VM via `colima ssh` base64
361:   on every invocation. No state drift between "rules the host script thinks
362:   are active" and "rules actually active after a VM reboot." The cost is a
363:   ~1s colima-ssh round trip at the start of every launch.
364: - **Allowlist lives at `~/.claude-agent/allowlist.txt`** on the macOS host,
365:   plain text, one domain per line, suffix matching. `--reload-allowlist`
366:   regenerates the filter and reloads tinyproxy (SIGHUP) without touching
367:   the running container — a ~2s fast path.
368: 
369: ### Consequences
370: 
371: - The LLM inside the container has no write path to the iptables rules or
372:   the allowlist. Modifying either requires a container escape plus VM root.
373: - All outbound traffic except the established-return path, the proxy port,
374:   the Ollama port, and DNS to the bridge gateway is REJECTed at the VM
375:   level. Applications inside the container that don't honor `HTTPS_PROXY` /
376:   `HTTP_PROXY` will simply fail to reach anything — an acceptable price,
377:   and arguably a feature (no silent bypass).
378: - Every invocation pays a small latency tax (applying firewall rules), in
379:   exchange for zero configuration drift.
380: - The seed allowlist is intentionally permissive for development and
381:   research workflows; users are expected to prune it.
382: - Per-rule granularity is coarse (hostname match only). If the allowlist
383:   grows past ~200 entries or needs per-path / per-method semantics, squid
384:   with `acl ... dstdomain` is the conventional upgrade.
385: 
386: ## ADR-009: Set git identity via environment variables, not just gitconfig
387: 
388: **Date:** 2026-04-08
389: **Status:** Accepted
390: 
391: ### Context
392: 
393: `git config --global` writes to `/root/.gitconfig` during image build. This
394: works for direct shell usage but fails inside Claude Code's bubblewrap sandbox,
395: which may not expose `/root/.gitconfig` in its mount namespace. When the sandbox
396: runs `git commit`, git cannot auto-detect the author identity and the commit
397: fails with "Author identity unknown".
398: 
399: ### Decision
400: 
401: Set git identity in three layers (matching the existing pattern for other env
402: vars):
403: 
404: 1. **`git config --global`** — baked into the image for non-sandboxed git.
405: 2. **`.bashrc` and `/etc/profile.d/git-identity.sh`** — exports
406:    `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`,
407:    `GIT_COMMITTER_EMAIL` so interactive shells have them.
408: 3. **`CONTAINER_ENV`** — passes the same four variables as container-level env
409:    vars so they are inherited from process birth, before shell init.
410: 
411: Git's `GIT_AUTHOR_*` / `GIT_COMMITTER_*` environment variables take precedence
412: over all config files, so they work regardless of whether the sandbox mounts
413: `/root/.gitconfig`.
414: 
415: ### Consequences
416: 
417: - Git commits work inside the bubblewrap sandbox without needing sandbox config
418:   changes.
419: - The env vars override any repo-level `.gitconfig`. This is acceptable because
420:   the container is single-user and all projects should use the same identity.
421: - Defaults are `Dev` / `dev@localhost`, overridable at script invocation via
422:   `--git-name`/`--git-email` CLI flags or `$GIT_USER_NAME`/`$GIT_USER_EMAIL`
423:   env vars.
424: - Requires `--rebuild` to bake the new profile scripts into the image.
425: 
426: ## ADR-011: start-agent: build the image with `docker build --network=host`
427: 
428: **Date:** 2026-04-15
429: **Status:** Accepted
430: 
431: ### Context
432: 
433: ADR-010's DOCKER-USER egress allowlist rejects every packet from the docker
434: bridge CIDR that isn't destined for tinyproxy, host Ollama, or bridge DNS.
435: Runtime containers honor `HTTPS_PROXY` and route their traffic through
436: tinyproxy, but `docker build` RUN steps also execute inside short-lived
437: containers attached to the bridge — and those build containers have no proxy
438: env set. The first install step (`apt-get update && apt-get install ...`) hit
439: the REJECT rule immediately:
440: 
441: ```
442: W: Failed to fetch http://deb.debian.org/debian/dists/bookworm/InRelease
443:    Could not connect to deb.debian.org:80 — connect (113: No route to host)
444: ```
445: 
446: Three options to unstick the build:
447: 
448: 1. **Route the build through tinyproxy via `--build-arg HTTP_PROXY=...`.**
449:    Docker treats the proxy vars as predefined build ARGs and exposes them as
450:    env vars during RUN. In practice this is unreliable on the legacy builder:
451:    some tools (notably apt) only consult the lowercase `http_proxy`, and
452:    propagating both cases through every RUN step means either touching the
453:    Dockerfile or writing an `/etc/apt/apt.conf.d/99proxy` shim.
454: 2. **Temporarily drop the REJECT rule while building, then re-add.** Simple
455:    in theory, but introduces a timing window where every bridge-attached
456:    container is unfirewalled, plus a cleanup path that has to survive build
457:    failures and SIGINT.
458: 3. **`docker build --network=host`.** RUN steps share the Colima VM's host
459:    network namespace. The VM's netns is not subject to the `FORWARD` chain
460:    (DOCKER-USER only fires for packets being routed between interfaces), so
461:    the build has full egress. Runtime containers still attach to the docker
462:    bridge via `docker run` and remain firewalled normally — the `--network`
463:    flag applies only to the build.
464: 
465: ### Decision
466: 
467: Option 3. `docker build` is invoked with `--network=host` in `start-agent.sh`.
468: 
469: ### Consequences
470: 
471: - Build-time network egress is unrestricted. Acceptable because we trust the
472:   Dockerfile — it's checked into the repo, the image is rebuilt only on
473:   explicit `--rebuild`, and the LLM running at runtime has no write path to
474:   the Dockerfile or the build invocation.
475: - Runtime egress enforcement is unchanged. Containers created by `docker run`
476:   attach to the bridge and pass through `DOCKER-USER` like before; the six
477:   smoke tests in `README.md` (default-deny, allowed-via-proxy,
478:   denied-via-proxy, Ollama carve-out, env sanity, hot-reload) all pass with
479:   this configuration.
480: - No Dockerfile changes needed to handle proxies, and no timing window where
481:   the bridge is unfirewalled. The whole interaction is local to the
482:   `docker build` line.
483: - If we ever migrate to BuildKit (the legacy builder is deprecation-warned on
484:   every run), the same flag works there too — BuildKit accepts
485:   `--network=host` and honors it identically for RUN steps. No lock-in.
486: 
487: ## ADR-012: start-agent: omlx as an alternative local inference backend
488: 
489: **Date:** 2026-04-16
490: **Status:** Accepted
491: 
492: ### Context
493: 
494: `start-agent.sh` hard-coded Ollama as the local inference backend. Ollama
495: binds to `localhost` by default; reaching it from the Colima VM requires
496: rebinding to `0.0.0.0`, which exposes the model server to every device on
497: the user's LAN. The `plans/ollama-host-firewall.md` plan addresses this with
498: a host-side pf firewall, but that adds operational complexity (anchor files,
499: LaunchDaemons, interface-scoped rules).
500: 
501: [omlx](https://github.com/jundot/omlx) is an MLX-based inference server for
502: Apple Silicon with an OpenAI-compatible API and built-in `--api-key` support.
503: When started with an API key, omlx returns 401/403 to unauthenticated
504: requests, so it can safely bind to `0.0.0.0` without a host-side firewall —
505: LAN peers that probe the port get rejected at the application layer.
506: 
507: ### Decision
508: 
509: Add omlx as an alternative backend behind `--backend=omlx` (default remains
510: `ollama`). A single `case "$BACKEND"` dispatch block governs all
511: backend-specific behavior:
512: 
513: 1. **Port.** `INFERENCE_PORT` is 11434 for Ollama, 8000 for omlx. All
514:    downstream references (iptables carve-out, preflight probe, OpenCode
515:    config, container env) use `$INFERENCE_PORT`.
516: 2. **API key.** `OMLX_API_KEY` from the host env is passed into the
517:    container as an env var and written into the OpenCode provider config's
518:    `apiKey` field. Missing key is a warning, not a hard error (the user may
519:    run omlx without auth during local testing).
520: 3. **Container env vars.** `OLLAMA_HOST` is only set for the `ollama`
521:    backend; `OMLX_HOST` and `OMLX_API_KEY` are set for `omlx`. Neither
522:    backend pollutes the other's namespace.
523: 4. **OpenCode config.** The `ollama` and `omlx` provider entries use
524:    separate keys in `opencode.json` and coexist across backend switches.
525: 5. **Preflight probe.** Ollama probes `/api/tags`; omlx probes `/v1/models`
526:    with a Bearer token if the key is set.
527: 
528: ### Consequences
529: 
530: - Users on shared networks can use `--backend=omlx` and skip the host-side
531:   pf firewall entirely. The `plans/ollama-host-firewall.md` plan is
532:   unnecessary when using omlx.
533: - No Dockerfile changes. omlx runs on the macOS host; the container reaches
534:   it via the same iptables carve-out used for Ollama (just a different port).
535: - Adding future backends (llama.cpp, vLLM, etc.) means adding one more
536:   `case` branch in the dispatch, one conditional in the env/preflight/config
537:   blocks, and one doc section. The pattern scales linearly.

(End of file - total 537 lines)
</content>
```

---

## Assistant (Build · Qwen3.5-35B-A3B-6bit · 68.6s)

_Thinking:_


Based on the script analysis, the container is firewalled with a default-deny policy allowing only: tinyproxy (for domains in the allowlist), the host inference port for Ollama/omlx, and DNS to the bridge gateway. I can now provide a recommendation for enabling the container to call openai/anthropic-compatible APIs on the host.



Based on the analysis, here's the recommended approach:

## Current State

The container is firewalled at the VM level (ADR-010) with a default-deny policy. The DOCKER-USER chain only allows:
- Established/related connections
- Traffic to tinyproxy for allowlisted domains
- Traffic to host inference server (Ollama: 11434, omlx: 8000)
- DNS to bridge gateway (127.0.0.11)

## Recommendation

**Add a firewall carve-out for the host port running the openai/anthropic-compatible server.**

### Option A: Add to Allowlist (Recommended)

1. Add the API server hostname/IP to `~/.claude-agent/allowlist.txt`
2. Add an iptables rule allowing traffic to the host's API port, OR
3. If using a domain, route through tinyproxy (which requires the server to be allowlisted)

**Best for**: Server running on a domain you control (e.g., `api.yourcompany.com`)

### Option B: Dedicated Port Carve-out

Add an iptables rule similar to the Ollama/omlx carve-out:

```bash
# Container -> macOS host API server
sudo iptables -I DOCKER-USER 3 -s "$BRIDGE_CIDR" -d "$HOST_IP" \
  -p tcp --dport $API_PORT \
  -m comment --comment claude-agent -j RETURN
```

**Best for**: Server on `localhost` or known IP/port, especially if using self-signed certs

### Option C: Loopback Routing

Run your server bound to `127.0.0.1` on the host, and add a route from the Colima VM:

```bash
vm_ssh "sudo ip route add $HOST_IP/32 via $GATEWAY_IP"
```

**Best for**: Quick testing, but adds routing complexity

## Implementation

The cleanest approach is **Option A with dedicated port**: Add the host IP + API port to the firewall rules in `start-agent.sh` (around line 562 where Ollama is handled), similar to:

```bash
# Container -> macOS host API server
sudo iptables -I DOCKER-USER 3 -s "\$BRIDGE_CIDR" -d "\$HOST_IP" \
  -p tcp --dport "\$API_PORT" \
  -m comment --comment claude-agent -j RETURN
```

Then pass `API_PORT` as an env var or CLI arg (mirroring the `--backend=ollama|omlx` pattern). This keeps the allowlist simple (no need to parse API URLs) and gives you explicit control over which ports are exposed.

---

## User

Save your plan to plans/qwen-35bmoe.md in your working directory.


---

## Assistant (Build · Qwen3.5-35B-A3B-6bit · 154.5s)

_Thinking:_


I'll save the analysis and recommendation to the specified path as requested.



**Tool: write**

**Input:**
```json
{
  "filePath": "/Users/aryehj/Repos/start-claude/plans/qwen-35bmoe.md",
  "content": "# Plan: Allow Container to Call Host Local LLM Server\n\n## Objective\n\nEnable the `claude-agent` container created by `start-agent.sh` to call an openai- or anthropic-compatible LLM server running on the macOS host loopback interface (`127.0.0.1`).\n\n## Problem\n\nThe container's egress is firewalled at the VM level (ADR-010) with a default-deny policy. The DOCKER-USER chain only allows:\n- Established/related connections\n- Traffic to tinyproxy for allowlisted domains\n- Traffic to host inference server ports (Ollama: 11434, omlx: 8000)\n- DNS to bridge gateway (127.0.0.11)\n\nA host-local API server bound to `127.0.0.1` is not reachable from inside the container.\n\n## Proposed Solution\n\nAdd a dedicated iptables carve-out for the API server port, similar to the existing Ollama/omlx rules.\n\n### Implementation Steps\n\n1. **Add CLI flag for API port** (in `start-agent.sh`):\n   ```bash\n   CLI_API_PORT=\"\"\n   # ... in args parsing:\n   --api-port=*) CLI_API_PORT=\"${1#--api-port=}\" ;;\n   ```\n\n2. **Add API port to environment**:\n   ```bash\n   API_PORT=\"8080\"  # default, or from CLI/env\n   ```\n\n3. **Add iptables rule** (after line 564, where Ollama rule is):\n   ```bash\n   # Container -> macOS host API server\n   sudo iptables -I DOCKER-USER 4 -s \"\\$BRIDGE_CIDR\" -d \"\\$HOST_IP\" \\\n     -p tcp --dport \"\\$API_PORT\" \\\n     -m comment --comment claude-agent -j RETURN\n   ```\n\n4. **Pass API host URL to container** (in `DOCKER_ENV_ARGS`, after line 829):\n   ```bash\n   -e \"API_BASE_URL=http://$HOST_IP:$API_PORT/v1\"\n   ```\n\n5. **Update OpenCode config** (after line 780, add omlx-compatible entry):\n   ```python\n   elif backend == \"api\":\n       entry = providers.setdefault('api', {\n           \"npm\": \"@ai-sdk/openai-compatible\",\n           \"name\": \"API (host)\",\n           \"options\": {},\n           \"models\": {},\n       })\n       entry.setdefault('options', {})['baseURL'] = base_url\n   ```\n\n6. **Add preflight probe** (after line 633):\n   ```bash\n   API_CURL_ARGS=(-sf --max-time 3)\n   if [[ -n \"${API_KEY:-}\" ]]; then\n     API_CURL_ARGS+=(-H \"Authorization: Bearer $API_KEY\")\n   fi\n   if ! vm_ssh curl \"${API_CURL_ARGS[@]}\" \"http://$HOST_IP:$API_PORT/v1/models\" >/dev/null 2>&1; then\n     echo \"warning: API server not reachable at http://$HOST_IP:$API_PORT from inside VM\" >&2\n   fi\n   ```\n\n### Environment Variables\n\n- `API_PORT` (default: 8080)\n- `API_BASE_URL` (container-internal: `http://$HOST_IP:$API_PORT/v1`)\n- `API_KEY` (optional, for API key auth)\n\n### Usage\n\n```bash\n# Default port 8080\nstart-agent.sh --api-port 8080\n\n# With API key\nAPI_KEY=abc123 start-agent.sh --api-port 8000\n```\n\n## Alternative Approaches Considered\n\n### Option A: Tinyproxy Allowlist\n\nAdd the API server domain to `allowlist.txt` and route through tinyproxy.\n- **Pros**: Uses existing allowlist mechanism\n- **Cons**: Requires domain resolution, slower, doesn't work for `localhost` IPs\n\n### Option B: Host Network Mode\n\nRun container with `--network=host` to access host loopback directly.\n- **Pros**: Simple, no firewall changes\n- **Cons**: Breaks container isolation, defeats purpose of egress allowlist\n\n### Option C: SSH Tunnel\n\nCreate SSH tunnel from container to host.\n- **Pros**: Encrypted, no firewall changes\n- **Cons**: Adds complexity, requires SSH setup, slower\n\n## Why Port Carve-out is Best\n\n1. **Simplicity**: Single iptables line, mirrors existing Ollama pattern\n2. **Performance**: Direct routing, no proxy overhead\n3. **Flexibility**: Works for any port, any binding\n4. **Security**: Explicit allowlist, no trust boundary changes\n5. **Maintainability**: Easy to add/remove, follows existing patterns\n\n## Testing\n\n1. Run API server on host: `ollama serve --host 127.0.0.1` (or your API server)\n2. Start container: `./start-agent.sh --api-port 11434`\n3. Inside container: `curl http://<HOST_IP>:11434/api/tags`\n4. Verify OpenCode config: `cat ~/.config/opencode/opencode.json`\n5. Test LLM call via Claude Code\n\n## Files to Modify\n\n- `start-agent.sh`: Add API port support, firewall rule, env vars\n- `dockerfiles/claude-agent.Dockerfile`: No changes needed (API runs on host)\n- `plans/`: This file\n\n## Notes\n\n- HOST_IP is discovered at runtime (line 451 in start-agent.sh), so the rule automatically works regardless of network configuration\n- The rule is added to DOCKER-USER chain at position 4, after Ollama/omlx carve-out\n- Uses same `-m comment --comment claude-agent` pattern for consistent rule management\n- API_KEY support mirrors omlx implementation (ADR-012)\n"
}
```

**Output:**
```
Wrote file successfully.
```

---

## Assistant (Build · Qwen3.5-35B-A3B-6bit · 19.1s)

_Thinking:_


The user asked me to save my plan to a file. I've created a comprehensive plan document at plans/qwen-35bmoe.md that outlines the recommended approach for allowing the container to call a local LLM server on the host loopback. The plan includes:

1. Analysis of the current firewall setup
2. Recommended approach (port carve-out in iptables)
3. Detailed implementation steps
4. Alternative approaches considered
5. Testing instructions

The document is complete and ready for the user to review.



Plan saved to `plans/qwen-35bmoe.md`.

**Summary:** Add a dedicated iptables carve-out for the API server port, mirroring the existing Ollama/omlx pattern. This allows direct routing to host loopback without proxy overhead while maintaining the egress allowlist security model.

---

