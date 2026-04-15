# start-agent

A new script, `start-agent.sh`, loosely based on `start-claude.sh`,
that uses Colima as the VM/runtime, runs Claude Code and OpenCode side by
side, enforces a network egress allowlist at a layer the in-container LLM
cannot modify, and routes local inference to Ollama on the macOS host.

## Status

- [x] Phase 1: Prerequisites, Colima VM bring-up, sizing knobs
- [x] Phase 2: Base image with Claude Code + OpenCode + dev tooling
- [x] Phase 3: In-VM network isolation (tinyproxy + DOCKER-USER iptables)
- [x] Phase 4: Ollama connectivity from container to macOS host
- [x] Phase 5: Launch, attach, rebuild, allowlist-reload UX
- [ ] Phase 6: Host-side research sidecar (Perplexity-style) — deferred per plan

Implementation notes:
- The plan's Unknowns #1, #3, #7 were resolved via web research (OpenCode
  installs via `npm i -g opencode-ai@latest`; Ollama provider uses
  `@ai-sdk/openai-compatible` with `baseURL`; tinyproxy's hostname filter
  does apply to HTTPS CONNECT — the "won't work for HTTPS" caveat only
  applies to URL/path filtering, which we disable via `FilterURLs No`).
- Unknowns #4, #5, #6 (host reachability, `host.docker.internal` under
  Colima, `DOCKER-USER` chain existence) are handled defensively at runtime
  rather than pre-committed: `HOST_IP` discovery falls back from the
  default route to `host.lima.internal` / `host.docker.internal`,
  `DOCKER-USER` is auto-created if absent, and `--add-host=host-gateway`
  is passed belt-and-suspenders. Verification happens on first real run.
- The single-shared-container in-use detection is a soft warning rather
  than a hard refusal; two shells in the same container is not actually
  harmful, and the mount-mismatch path already recreates the container
  when project dir changes.

## Context

The repo already contains `start-claude.sh`, which uses Apple's `container`
CLI to spin up a per-project microVM from a shared `claude-dev:latest` image.
That script's patterns are documented in `CLAUDE.md` and are worth reusing:
`--rebuild` semantics, env-var-overridable RAM/CPU, host-shared `~/.claude`
bind mount, `~/.claude.json` file bind, project-level settings injection,
upstream skills sync, image staleness check, git identity forwarding via
env vars, `TERM`/`COLORTERM`/`TERM_PROGRAM` forwarding, sandbox-safe UV
cache and venv directories keyed off `$TMPDIR`.

The new script does **not** replace `start-claude.sh`. It lives alongside it
as a sibling at `start-agent.sh` and adds four things `start-claude.sh`
does not have:

1. **Colima as the runtime**, giving one shared Lima VM that hosts docker
   containers, instead of one Apple Containers microVM per project.
2. **VM-level network egress filtering** enforced via iptables in the Colima
   VM, with a tinyproxy instance (also in the VM, outside the container) that
   applies a domain allowlist. Rules live outside the container's network
   namespace, so the LLM inside cannot touch them without a container escape
   plus VM root — two jumps outside any realistic behavioral threat model.
3. **Both Claude Code and OpenCode** installed in the same image.
4. **Ollama integration**: the container is pointed at Ollama running on the
   macOS host so OpenCode (and any Claude plugin that wants local models)
   can serve 30B-class models from the host GPU/ANE.

The script operates on a single shared VM and a single shared container. On
launch it bind-mounts `$(pwd)` into the container at the same path. No
per-project containers; the working directory changes determine the mount.

## Goals

- One Colima VM (`claude-agent` profile), vz backend, docker runtime.
- Default sizing **8 GB / 6 CPUs**, overridable via env
  (`CLAUDE_AGENT_MEMORY`, `CLAUDE_AGENT_CPUS`) and CLI flags
  (`--memory=VALUE`, `--cpus=VALUE`).
- One shared docker container (`claude-agent`) with both `claude` and
  `opencode` CLIs installed.
- `$(pwd)` bind-mounted into the container at the same path at launch.
- Default egress: **deny**. Allowed: (a) container → in-VM tinyproxy on its
  listen IP/port; (b) container → macOS host on Ollama's port (11434); (c)
  established/related return traffic. Everything else REJECTed.
- tinyproxy enforces a domain allowlist. The allowlist file lives on the
  macOS host at `~/.claude-agent/allowlist.txt`, plain text, one domain
  per line, `#` comments supported, suffix-matching semantics (so
  `github.com` covers `api.github.com`, `codeload.github.com`, etc.).
- The LLM inside the container has no write path to the allowlist or the
  iptables rules. Editing requires editing a file on the macOS host and
  running `start-agent.sh --reload-allowlist`.
- `--rebuild` rebuilds the image, and on an additional confirmation also
  deletes and recreates the Colima VM itself.
- `--reload-allowlist` regenerates tinyproxy's filter file inside the VM and
  reloads tinyproxy without touching the running container.

## Unknowns / To Verify

These are factual questions that must be resolved **during** implementation
rather than guessed at in this plan. Each is load-bearing for the step it
is attached to — the plan deliberately does not fabricate a specific answer
because a confident wrong answer is worse than an explicit verification step.

1. **OpenCode install command.** The canonical Linux install instructions
   live at `https://opencode.ai/` and/or
   `https://github.com/sst/opencode`. Before writing Phase 2's Dockerfile,
   fetch one of those pages and confirm the current install method (likely
   a curl-pipe installer, possibly an npm package, possibly a direct
   binary download). Do not hard-code a URL that has not been verified.
   → Phase 2.

2. **OpenCode config and credentials paths.** Where does OpenCode store
   (a) its config file, (b) provider credentials, (c) per-project state?
   These need to be persisted via a host bind mount analogous to
   `~/.claude-containers/shared/` → `/root/.claude`. Inspect a fresh
   OpenCode install inside the base image (or check the docs) to find the
   paths. → Phase 2, Phase 5.

3. **OpenCode's Ollama provider config schema.** Determine how OpenCode is
   pointed at an Ollama endpoint — config-file key, environment variable,
   CLI flag, or a combination. Confirm whether `OLLAMA_HOST` alone is
   enough, or whether a config entry under a `provider` / `model` section
   is also required. → Phase 4.

4. **Reachability of the macOS host from inside a Colima-hosted container.**
   The single most important unknown. Options:
   - (a) Ollama bound to `0.0.0.0:11434` on macOS (via
     `launchctl setenv OLLAMA_HOST 0.0.0.0:11434` then Ollama restart).
     Container reaches it via the vmnet gateway IP visible to the VM.
   - (b) `colima` / `lima` port-forward from host → VM, then the container
     reaches `host.docker.internal:11434` or the docker bridge gateway.
   - (c) socket_vmnet so the VM gets a vmnet IP and the host is at a
     known fixed address.

   Verify by running, inside the Colima VM: `ip route show default` to
   find the gateway, then `curl <gateway>:11434/api/tags` from both the
   VM and from inside a test container. Pick whichever route works and
   requires the least host-side configuration. Recommended starting
   point: (a). → Phase 3, Phase 4.

5. **`host.docker.internal` behavior under Colima.** Docker Desktop maps
   this name to the macOS host. Colima's docker runtime may map it to the
   Colima VM itself, not define it at all, or require
   `--add-host=host.docker.internal:host-gateway` on `docker run`. Verify
   before relying on it. → Phase 4.

6. **Docker's `DOCKER-USER` iptables chain presence under Colima.** Modern
   docker versions create a `DOCKER-USER` chain that the daemon does not
   clobber on restart and that user rules can be inserted into. Verify
   it exists inside the Colima VM after `colima start`. If not, create it
   and wire it via `iptables -I FORWARD 1 -j DOCKER-USER`. → Phase 3.

7. **tinyproxy filter semantics for HTTPS `CONNECT`.** tinyproxy supports
   `FilterDefaultDeny`, `FilterExtended` (regex), `FilterURLs`, and
   `Filter` directives. Confirm that with `FilterDefaultDeny Yes` plus a
   filter file of regex-anchored host patterns
   (e.g. `(^|\.)github\.com$`), filtering applies to the HTTPS `CONNECT`
   method and not just HTTP `GET`. If tinyproxy does not filter CONNECT
   hosts out of the box, either use a different proxy (squid with
   `acl ... dstdomain`) or patch the tinyproxy config. → Phase 3.

8. **OpenCode's sandbox behavior (if any).** Claude Code uses a
   bubblewrap-based sandbox. Determine whether OpenCode has a comparable
   sandbox mode and, if so, how to enable it in config. The network
   allowlist in this plan enforces egress regardless, so this is a
   defense-in-depth question, not a blocker. → Phase 5.

---

## Phase 1: Prerequisites, Colima VM bring-up, sizing knobs

### Steps

1. Create `start-agent.sh` at the repo root. Copy the arg-parsing
   scaffold from `start-claude.sh` (lines 16–43) and adapt:
   - `CLAUDE_CONTAINER_MEMORY` → `CLAUDE_AGENT_MEMORY`, default `8`
     (interpret as GiB; accept `8`, `8G`, or `8GB` and normalize to the
     integer GiB that `colima start --memory` expects).
   - `CLAUDE_CONTAINER_CPUS` → `CLAUDE_AGENT_CPUS`, default `6`.
   - Add `--memory=VALUE` and `--cpus=VALUE` CLI flags that override the
     env vars.
   - Keep `--git-name` / `--git-email`.
   - Add `--rebuild` and `--reload-allowlist`.
   - `PROJECT_DIR` defaults to `$(pwd)`; normalize to an absolute path.
2. Preflight:
   - `command -v colima` — error with
     `Install with: brew install colima docker` if missing.
   - `command -v docker` — error similarly.
3. Use `claude-agent` as the Colima profile name
   (`colima start -p claude-agent …`). Keeps this VM separate from other
   profiles the user may have.
4. Check VM state: `colima status -p claude-agent 2>/dev/null` and inspect
   exit code / output.
5. `--rebuild` behavior:
   - Prompt the user for confirmation before destroying VM state
     (`Continue? [y/N]`). Colima VM deletion is **not reversible** —
     everything inside the VM (installed extras, docker image cache,
     shell history) is gone. This is a meaningful difference from
     `start-claude.sh`, where `container rm` only affects a single
     microVM.
   - On confirm: `colima delete -p claude-agent --force`; remove
     `$IMAGE_STAMP`.
6. If the profile is not running, start it:
   ```
   colima start -p claude-agent \
     --vm-type vz \
     --runtime docker \
     --cpu  "$CLAUDE_AGENT_CPUS" \
     --memory "$CLAUDE_AGENT_MEMORY_GB" \
     --mount-type virtiofs \
     --network-address
   ```
   `--network-address` gives the VM a vmnet-visible IP, making host
   reachability and iptables rule construction straightforward in Phase 3.
7. If the VM is already running and the user's requested memory/CPU
   differs from what the VM was started with (`colima list -p claude-agent`
   reports it), **warn** that the running VM's sizing differs and that
   `--rebuild` is required to resize. Do not silently ignore.
8. Point the local `docker` CLI at the Colima docker socket. Colima
   registers a docker context named `colima-claude-agent` on start; use
   `docker context use colima-claude-agent` or set `DOCKER_HOST` to the
   socket path reported by `colima status -p claude-agent --json`. Verify
   with `docker info` that the client is talking to the expected VM.

### Files

- `start-agent.sh` (new)

### Testing

- `./start-agent.sh --help` prints usage.
- With no prior VM: a new Colima profile comes up with the requested
  memory/CPU (verify `colima list`).
- Re-run: fast path, no second `colima start`.
- `./start-agent.sh --memory=12G --cpus=8` warns if a running VM's
  sizing differs; applies on first-start otherwise.
- `./start-agent.sh --rebuild` prompts for confirmation and on
  `y` recreates the VM.

---

## Phase 2: Base image with Claude Code + OpenCode + dev tooling

### Steps

1. Define `IMAGE_TAG=claude-agent:latest` and
   `IMAGE_STAMP=$HOME/.claude-agent-image-built` (distinct from
   `start-claude.sh`'s `$HOME/.claude-dev-image-built` so the two scripts
   don't clobber each other's staleness tracking).
2. Check for the image in Colima's docker:
   `docker image inspect "$IMAGE_TAG" &>/dev/null`.
3. If missing, build it. Choice: inline `bash -c` heredoc like
   `start-claude.sh`, or a checked-in Dockerfile. Recommend a Dockerfile
   at `dockerfiles/claude-agent.Dockerfile` invoked via
   `docker build -t claude-agent:latest -f dockerfiles/claude-agent.Dockerfile .`
   — more readable and layer-cache-friendly than the inline approach.
4. Dockerfile contents — reproduce the existing setup from
   `start-claude.sh` (lines 172–262) and add OpenCode:
   - Base: `debian:bookworm-slim`.
   - `apt-get install -y --no-install-recommends`: `bash curl wget git
     ca-certificates gnupg build-essential python3 python3-pip jq
     ripgrep fd-find unzip bubblewrap socat libseccomp2 libseccomp-dev`.
   - `apt-get upgrade -y`; touch `/var/lib/apt/last-upgrade` for the
     apt-age warning block from `start-claude.sh` lines 189–197.
   - Node LTS via `https://deb.nodesource.com/setup_lts.x`, then
     `npm install -g npm@latest @anthropic-ai/sandbox-runtime`.
   - `uv` via
     `curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh`.
   - Claude Code CLI:
     `export PATH="/root/.local/bin:$PATH"`;
     `curl -fsSL https://claude.ai/install.sh | bash`;
     `ln -sf /root/.local/bin/claude /usr/local/bin/claude`.
     Match `start-claude.sh` lines 213–218 exactly.
   - **OpenCode CLI** — install via whichever method Unknown #1 resolves
     to. Do not hard-code a URL until verified. If the official installer
     is a curl-pipe, use it and symlink its binary into `/usr/local/bin/opencode`
     if needed. If it's an npm package, use `npm install -g <name>` after
     confirming the package name.
   - Write `/etc/profile.d/uv-cache.sh` with the dynamic
     `${TMPDIR:-/tmp}` pattern for `UV_CACHE_DIR` and
     `UV_PROJECT_ENVIRONMENT`, exactly as `start-claude.sh` lines 231–236
     (see ADR-001 and ADR-007 in `ADR.md`).
   - Write `/etc/profile.d/disable-1m-context.sh` and
     `/etc/profile.d/disable-adaptive-thinking.sh`.
   - Mirror the `/root/.bashrc` appends from `start-claude.sh` lines
     225–244 and 252–255.
   - Placeholder `git config --global user.{name,email}` (actual values
     come from env vars set at `docker run` time, since the sandbox may
     not see `/root/.gitconfig` — same reasoning as `start-claude.sh`
     lines 247–261).
5. After build succeeds, write `date +%s > "$IMAGE_STAMP"`.
6. On subsequent runs, check the stamp: if >= 30 days old, print a warning
   recommending `--rebuild`. Match `start-claude.sh` lines 152–159.

### Files

- `start-agent.sh` (modified)
- `dockerfiles/claude-agent.Dockerfile` (new)

### Testing

- First run builds the image; `docker image ls` shows `claude-agent:latest`;
  stamp file exists.
- Second run skips the build.
- `docker run --rm claude-agent:latest which claude opencode` — both print
  paths.
- `docker run --rm claude-agent:latest claude --version` and
  `docker run --rm claude-agent:latest opencode --version` — both print
  versions.
- `--rebuild` deletes the image and rebuilds from scratch.

---

## Phase 3: In-VM network isolation (tinyproxy + DOCKER-USER iptables) (Opus recommended)

This phase has the most implementation-time judgment calls: network
topology discovery, placement of iptables rules relative to docker's own
chains, tinyproxy config edge cases, and debugging when things don't
connect.

### Steps

1. **Install tinyproxy inside the Colima VM, not inside the container.**
   Run via `colima ssh -p claude-agent`:
   ```
   sudo apt-get update
   sudo apt-get install -y tinyproxy
   ```
   This puts tinyproxy in the Lima VM where docker itself runs — one
   layer above the container's network namespace.
2. **Compute the docker bridge IP.** Inside the VM, run
   `docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}'`.
   Expect `172.17.0.1`, but do not hard-code — bind tinyproxy to whatever
   the bridge actually uses. Save as `BRIDGE_IP`.
3. **Write `/etc/tinyproxy/tinyproxy.conf`** (via
   `colima ssh -- sudo tee`). Key directives:
   ```
   User tinyproxy
   Group tinyproxy
   Port 8888
   Listen <BRIDGE_IP>
   Timeout 600
   FilterDefaultDeny Yes
   Filter "/etc/tinyproxy/filter"
   FilterExtended Yes
   FilterURLs No
   ConnectPort 443
   ConnectPort 80
   LogLevel Warning
   ```
   Verify (Unknown #7) that `FilterDefaultDeny Yes` applies to HTTPS
   `CONNECT` requests. If it does not, switch to squid before proceeding.
4. **Generate `/etc/tinyproxy/filter`** from the host-side allowlist. The
   host file is `~/.claude-agent/allowlist.txt`. For each non-comment
   line in that file containing `domain.tld`, emit a regex line like
   `(^|\.)domain\.tld$` (escape dots). Push the generated filter file
   into the VM via `colima ssh -- sudo tee /etc/tinyproxy/filter`.
5. **Seed the host-side allowlist on first run** if
   `~/.claude-agent/allowlist.txt` does not yet exist. Header comment
   should tell the user where to edit and how to reload. Seed categories:

   ```
   # Edit this file on the macOS host.
   # Apply changes: ./start-agent.sh --reload-allowlist
   # Suffix match: 'github.com' also matches 'api.github.com'.

   # === Anthropic / AI coding agents ===
   anthropic.com
   claude.ai
   opencode.ai

   # === Source control & code hosting ===
   github.com
   githubusercontent.com
   githubassets.com
   codeload.github.com
   gitlab.com
   bitbucket.org
   git-scm.com

   # === Package registries ===
   npmjs.org
   npmjs.com
   yarnpkg.com
   pypi.org
   pythonhosted.org
   crates.io
   rust-lang.org
   rubygems.org
   packagist.org
   pkg.go.dev
   proxy.golang.org
   hex.pm

   # === Container / image registries ===
   docker.io
   docker.com
   quay.io
   ghcr.io

   # === OS package repos ===
   debian.org
   ubuntu.com
   deb.nodesource.com
   astral.sh

   # === Model & dataset hubs ===
   huggingface.co
   ollama.com

   # === General web & reference ===
   wikipedia.org
   wikimedia.org
   mozilla.org
   developer.mozilla.org
   stackoverflow.com
   stackexchange.com
   archive.org

   # === Search engines ===
   google.com
   duckduckgo.com
   bing.com

   # === Biomedical / life sciences ===
   ncbi.nlm.nih.gov
   nlm.nih.gov
   nih.gov
   europepmc.org
   biorxiv.org
   medrxiv.org
   plos.org
   biomedcentral.com
   elifesciences.org
   nature.com
   cell.com
   science.org
   sciencemag.org

   # === Physical sciences, engineering, math, CS ===
   arxiv.org
   semanticscholar.org
   openalex.org
   dblp.org
   acm.org
   ieee.org
   aps.org
   acs.org
   rsc.org
   iop.org
   ams.org
   paperswithcode.com

   # === Social sciences, humanities, general journals ===
   jstor.org
   ssrn.com
   crossref.org
   doi.org
   orcid.org
   sciencedirect.com
   springer.com
   link.springer.com
   springernature.com
   wiley.com
   onlinelibrary.wiley.com
   tandfonline.com
   sagepub.com
   cambridge.org
   oup.com
   muse.jhu.edu

   # === Data repositories & open science ===
   zenodo.org
   figshare.com
   osf.io
   dataverse.org
   datadryad.org
   kaggle.com

   # === Government / statistical / standards ===
   nasa.gov
   cdc.gov
   fda.gov
   nsf.gov
   census.gov
   noaa.gov
   usgs.gov
   bls.gov
   loc.gov
   gov.uk
   who.int
   europa.eu

   # === News & periodicals for research context ===
   nytimes.com
   economist.com
   ft.com
   reuters.com
   apnews.com
   bbc.com
   bbc.co.uk
   washingtonpost.com
   theatlantic.com
   newyorker.com
   wsj.com
   ```

   This is a seed, not a final list. The user is expected to prune or
   extend. It deliberately leans permissive for development and research
   workflows.

6. **Apply iptables rules in the Colima VM.** Use docker's
   `DOCKER-USER` chain, which the docker daemon does not clobber on
   service restart and which is traversed before docker's own rules
   (Unknown #6). Conceptually (the exact CIDR for `BRIDGE_CIDR` and the
   exact `HOST_IP` are computed at script time, not hard-coded):

   ```
   # Flush any previous claude-agent rules in DOCKER-USER (idempotent).
   iptables -S DOCKER-USER | grep 'claude-agent' | ... | iptables -D ...

   # Return established/related traffic early.
   iptables -I DOCKER-USER 1 -s $BRIDGE_CIDR -m conntrack \
     --ctstate ESTABLISHED,RELATED -j RETURN \
     -m comment --comment claude-agent

   # Allow container -> in-VM tinyproxy.
   iptables -I DOCKER-USER 2 -s $BRIDGE_CIDR -d $BRIDGE_IP \
     -p tcp --dport 8888 -j RETURN \
     -m comment --comment claude-agent

   # Allow container -> macOS host Ollama port.
   iptables -I DOCKER-USER 3 -s $BRIDGE_CIDR -d $HOST_IP \
     -p tcp --dport 11434 -j RETURN \
     -m comment --comment claude-agent

   # Default-deny everything else from the bridge.
   iptables -A DOCKER-USER -s $BRIDGE_CIDR -j REJECT \
     --reject-with icmp-admin-prohibited \
     -m comment --comment claude-agent
   ```

   Tag every rule with `--comment claude-agent` so the reload path can
   find and delete only its own rules without disturbing others.
7. **Persistence across VM restarts.** Simpler than `iptables-persistent`
   or systemd units: re-apply the rules on every invocation of
   `start-agent.sh`. Put the firewall commands inside a shell
   script on the host (`firewall-apply.sh`), and on each script run push
   it into the VM via `colima ssh -- sudo bash -s < firewall-apply.sh`.
   This way there is no state drift — the host script is always the
   source of truth, and a VM reboot just means "run the script again."
8. **Enable and start tinyproxy** inside the VM via
   `colima ssh -- sudo systemctl enable --now tinyproxy`. The base
   Colima Ubuntu image uses systemd, so this is straightforward.
9. **Implement `--reload-allowlist` as an early-exit path** that:
   - Reads `~/.claude-agent/allowlist.txt`.
   - Regenerates `/etc/tinyproxy/filter` inside the VM.
   - `colima ssh -- sudo systemctl reload tinyproxy` (SIGHUP reloads the
     filter file without terminating in-flight connections).
   - Re-applies the iptables rules (cheap and idempotent; covers the
     case where the VM was restarted since the last launch).
   - Prints a one-line "Allowlist reloaded (N entries)" and exits 0.
     Takes ~2s or less.

### Files

- `start-agent.sh` (modified — firewall + proxy orchestration
  functions)
- `~/.claude-agent/allowlist.txt` (user-editable, seeded on first run;
  lives on the macOS host, not in the repo)

### Testing

- `colima ssh -- sudo iptables -L DOCKER-USER -v -n` — the four
  claude-agent rules are present.
- `colima ssh -- sudo systemctl status tinyproxy` — active, listening
  on `$BRIDGE_IP:8888`.
- From inside a running claude-agent container:
  - `curl -x http://$BRIDGE_IP:8888 https://github.com` — succeeds.
  - `curl -x http://$BRIDGE_IP:8888 https://www.google.com` — succeeds.
  - `curl -x http://$BRIDGE_IP:8888 https://evil.example.com` — tinyproxy
    returns 403.
  - `curl --max-time 3 https://github.com` (no proxy) — connection
    refused / times out (iptables REJECT).
  - `iptables -F` — fails, container lacks CAP_NET_ADMIN by default.
    This is the "LLM cannot easily modify" property: even a fully
    compromised container cannot touch the VM-level rules.
- Edit `~/.claude-agent/allowlist.txt` on the host, add `example.com`,
  run `./start-agent.sh --reload-allowlist`, and retry the
  third `curl` above with `example.com` — now succeeds. Total reload
  time under 2s.

---

## Phase 4: Ollama connectivity from container to macOS host

### Steps

1. **Resolve Unknown #4** (reachability). Starting point: ask the user to
   set `OLLAMA_HOST=0.0.0.0:11434` on macOS once, via
   `launchctl setenv OLLAMA_HOST 0.0.0.0:11434` followed by a restart of
   the Ollama app. Document this in `README.md`.
2. **Compute `HOST_IP` from the VM's perspective** at script start:
   ```
   HOST_IP=$(colima ssh -p claude-agent -- ip route show default | awk '{print $3}')
   ```
   Under Colima with `--network-address`, the default gateway from inside
   the VM is the macOS host. If this is empty or unreachable, fall back
   to trying `host.lima.internal` (Lima often auto-resolves this) and
   then to `host.docker.internal`. The first one that answers a
   `curl -sf http://<name>:11434/api/tags` is the winner.
3. **Feed `HOST_IP` into Phase 3's iptables rule** (step 6, the Ollama
   RETURN rule).
4. **Pass `OLLAMA_HOST` into the container** via `docker run -e`:
   ```
   -e "OLLAMA_HOST=http://$HOST_IP:11434"
   ```
   Both Claude Code (if any plugin uses Ollama) and OpenCode pick it up.
5. **Configure OpenCode to use Ollama as a provider.** Depends on
   Unknown #3. Likely a one-time config-file injection similar to how
   `start-claude.sh` injects `.claude/settings.local.json` — edit
   OpenCode's config (path TBD) to set Ollama as the provider with
   `baseURL` pointing at `$OLLAMA_HOST`. Write this as a Python
   migration block styled after `start-claude.sh` lines 88–122.
6. **Preflight check on every run**: before starting the container,
   `colima ssh -- curl -sf --max-time 3 http://$HOST_IP:11434/api/tags`.
   On failure, print:
   ```
   warning: Ollama not reachable at http://$HOST_IP:11434 from inside the
   Colima VM. Ensure Ollama is running on the host and bound to 0.0.0.0.
   See README for setup. Continuing without local inference.
   ```
   Do not exit — the container is still useful with hosted Claude.

### Files

- `start-agent.sh` (modified)
- `README.md` (new section: "Using Ollama with start-agent")
- OpenCode config path (resolved in Unknown #2) — one-time injection

### Testing

- With Ollama running on the host bound to `0.0.0.0:11434`, from inside
  the container: `curl -s $OLLAMA_HOST/api/tags` lists models.
- `opencode` with a configured local model answers a prompt using a
  30B-class model served by host Ollama.
- Stop Ollama on the host and re-run the script — preflight prints the
  warning.
- Inference latency from container ≈ latency from host directly (all
  hops are local virtio/bridge).

---

## Phase 5: Launch, attach, rebuild, allowlist-reload UX

### Steps

1. **Single shared container named `claude-agent`.** Check existence via
   `docker container inspect claude-agent &>/dev/null`.
2. **Existing container** case:
   - If the container's existing project-dir bind mount (inspect
     `.HostConfig.Binds`) does not match the current `$PROJECT_DIR`,
     remove the container (`docker rm -f claude-agent`) and fall through
     to the new-container path. Prefer recreation over trying to
     hot-swap mounts.
   - Otherwise: `docker start claude-agent` then
     `docker exec -it -w "$PROJECT_DIR" <env…> claude-agent /bin/bash`.
     Forward env vars (`TERM`, `COLORTERM`, `TERM_PROGRAM`,
     `GIT_AUTHOR_*`, `GIT_COMMITTER_*`, `OLLAMA_HOST`, `HTTPS_PROXY`,
     `HTTP_PROXY`, `NO_PROXY`).
3. **New container** case — `docker run` with:
   - `--name claude-agent`
   - `-it`
   - `-m "$CLAUDE_AGENT_MEMORY_G"`
   - `--cpus "$CLAUDE_AGENT_CPUS"`
   - `-v "$PROJECT_DIR:$PROJECT_DIR"` — project bind mount at the same
     path on both sides.
   - `-v "$CLAUDE_CONFIG_DIR:/root/.claude"` where
     `CLAUDE_CONFIG_DIR="$HOME/.claude-containers/shared"` (same dir as
     `start-claude.sh` — intentionally shared; both scripts can mount
     it without conflict as long as only one runs at a time).
   - `-v "$CLAUDE_JSON_FILE:/root/.claude.json"` for the top-level
     `.claude.json` file bind (same reasoning as `start-claude.sh`
     lines 294–298).
   - `-v "$OPENCODE_CONFIG_DIR:/root/.config/opencode"` — **path to
     confirm in Unknown #2.**
   - `-w "$PROJECT_DIR"`
   - `--add-host=host.docker.internal:host-gateway` — belt and
     suspenders for tools that hard-code the name.
   - Environment:
     ```
     -e "TERM=$TERM"
     -e "COLORTERM=${COLORTERM:-}"
     -e "TERM_PROGRAM=${TERM_PROGRAM:-}"
     -e "CLAUDE_CODE_DISABLE_1M_CONTEXT=1"
     -e "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1"
     -e "GIT_AUTHOR_NAME=$GIT_USER_NAME"
     -e "GIT_AUTHOR_EMAIL=$GIT_USER_EMAIL"
     -e "GIT_COMMITTER_NAME=$GIT_USER_NAME"
     -e "GIT_COMMITTER_EMAIL=$GIT_USER_EMAIL"
     -e "OLLAMA_HOST=http://$HOST_IP:11434"
     -e "HTTPS_PROXY=http://$BRIDGE_IP:8888"
     -e "HTTP_PROXY=http://$BRIDGE_IP:8888"
     -e "NO_PROXY=localhost,127.0.0.1,$BRIDGE_IP,$HOST_IP"
     ```
   - Image: `claude-agent:latest`
   - Command: `bash`
4. **In-use detection.** Before creating or reusing the container, check
   `docker top claude-agent 2>/dev/null` (or a sentinel file inside the
   container) to see if another session is already inside. If yes,
   print the project dir of the existing session (from its bind
   mount) and refuse to attach. Avoids two terminals fighting over the
   same shared container.
5. **Skills sync.** Before launching the container, sync upstream skills
   into `$CLAUDE_CONFIG_DIR/skills/` using the same mechanism as
   `start-claude.sh` lines 331–358. Same `CLAUDE_SKILLS_ARCHIVE_URL`
   env-var. No changes needed.
6. **Project settings injection.** `.claude/settings.local.json` in the
   user's `$PROJECT_DIR` — same migration block as `start-claude.sh`
   lines 85–140. Copy verbatim.
7. **Global user settings injection.** `$GLOBAL_SETTINGS_FILE` —
   `showThinkingSummaries`, `coauthorTag: none`, `effortLevel: medium`.
   Same as `start-claude.sh` lines 301–329. Copy verbatim.
8. **Help text** at the top of the script, covering: `--rebuild`,
   `--reload-allowlist`, `--memory`, `--cpus`, `--git-name`,
   `--git-email`, plus a one-paragraph note pointing to
   `~/.claude-agent/allowlist.txt` and the `--reload-allowlist` flag.

### Files

- `start-agent.sh` (final assembly)
- `README.md` — add a section documenting the new script alongside
  `start-claude.sh`.
- `CLAUDE.md` — add a short "start-agent.sh key decisions"
  section mirroring the style of the existing decisions block.
- `ADR.md` — new ADR documenting (a) choice of in-VM tinyproxy +
  DOCKER-USER iptables over host-side pf, (b) single-shared-VM/
  single-shared-container design, (c) the re-apply-on-every-run
  persistence strategy for firewall rules.

### Testing

- From an arbitrary project dir: `./start-agent.sh` brings up
  the VM (if needed), builds the image (first run only), seeds the
  allowlist (first run only), applies iptables rules, starts tinyproxy,
  launches the container, and drops into bash at `$(pwd)`.
- Inside: `pwd` returns the host project dir, `ls` shows project
  contents (bind mount works).
- Inside: `claude` and `opencode` both authenticate via persisted
  credentials and respond to a prompt.
- Inside: `curl -sf https://github.com` succeeds; `curl -sf
  https://evil.example.com` fails with 403 from tinyproxy.
- Exit the container, re-run — fast reattach path.
- Re-run from a different project dir — detects mount mismatch,
  recreates the container with the new mount.
- Open a second terminal, re-run — in-use detection refuses the second
  attach and reports the first session's project dir.
- `./start-agent.sh --reload-allowlist` — edits to the host
  allowlist take effect in <2s without affecting the running container.
- `./start-agent.sh --rebuild` — prompts for confirmation,
  deletes the VM, recreates everything from scratch.
- Attempt from inside the container to modify firewall state
  (`iptables -F`, editing `/etc/tinyproxy/tinyproxy.conf`) — both fail.

---

## Phase 6: Host-side research sidecar (Perplexity-style) — open design

**Intentionally under-specified.** This phase sketches the integration
points without committing to a specific project, deployment topology, or
config schema. The implementer should explore the tradeoffs at the time
they start work on it rather than inherit decisions that may be stale.

### Intent

Add a local "research server" (a Perplexity-clone — Perplexica, Farfalle,
OpenPerplex, or similar; or even a hand-rolled SearXNG + LLM orchestrator)
that the agents inside the VM can query for complex web-research tasks,
without blowing up the domain allowlist. The server handles the fan-out to
many search engines and the fetching of arbitrary result pages; the agents
see a single narrow API.

### Architectural constraint

The allowlist model in Phase 3 assumes a small, human-curated set of
allowed destinations. A research server's whole job is to reach arbitrary
domains — if it runs behind the same allowlist, it's useless; if it runs
with a proxy bypass, the agents can funnel arbitrary fetches through it
and effectively sidestep the allowlist. The cleanest resolution is the
same pattern already used for Ollama: **the sidecar runs on the macOS
host, not inside the VM**, and the VM reaches it through a single allowed
port. The host has normal internet; the VM stays sandboxed; the interface
between them is a single TCP port added to the iptables allowlist.

Running it inside the VM is also a valid option, just with different
tradeoffs. Defer the decision to implementation time.

### Decisions to make at implementation time

1. **Which project.** Perplexica (Next.js + SearXNG + Ollama/remote LLM),
   Farfalle, OpenPerplex, or a hand-rolled SearXNG + thin orchestrator.
   Each has different maturity, resource footprint, and config ergonomics.
   Pick based on what the ecosystem looks like at the time. Do not
   hard-code a choice here.
2. **Host or in-VM.** Start with host-side for the reasons above. If it
   turns out to be awkward to run certain components on macOS (e.g., a
   container-heavy compose stack), reconsider in-VM with a scoped
   proxy-bypass rule.
3. **LLM backend.** Reuse the host Ollama from Phase 4, or point at a
   hosted API. If Ollama, no new host-side dependency beyond what Phase 4
   already assumes.
4. **Search backend.** SearXNG is the standard. Whether to run a dedicated
   instance for this sidecar or point at an existing public one is a
   reliability-vs-privacy call the user can make.
5. **API shape the agents see.** HTTP JSON is the path of least
   resistance. Whether to expose the raw Perplexica/Farfalle API or wrap
   it in a thinner, agent-friendly shim (e.g., a single
   `POST /research {query}` endpoint returning a cited answer) is a
   question worth asking once the chosen project's native API is known.
6. **Port and auth.** Pick a port. Decide whether to require a shared
   secret / bearer token between the agents and the sidecar, or rely on
   host-gateway reachability as the sole access control. Shared secret is
   cheap and worth it for defense in depth.

### Integration points with the existing plan

This is what the implementer should wire up regardless of which project
and topology are chosen:

- **iptables rule** (Phase 3, step 6): add one RETURN rule for
  `-d $HOST_IP -p tcp --dport $SIDECAR_PORT`, tagged with the same
  `--comment claude-agent` so the reload path handles it uniformly.
- **`NO_PROXY` entry** (Phase 5, step 3): add the host-gateway hostname
  or IP and the sidecar port to `NO_PROXY` so the agents' HTTP clients
  bypass tinyproxy when calling the sidecar.
- **Environment variable** (Phase 5, step 3): export something like
  `RESEARCH_API=http://$HOST_IP:$SIDECAR_PORT` into the container so the
  agents (and any skill that wants to use it) can find it without
  configuration.
- **Preflight check** (Phase 4, step 6 pattern): on script start, probe
  the sidecar's health endpoint from inside the VM. On failure, warn but
  do not exit — the sidecar is optional, not load-bearing.
- **Agent-side skill or config**: whatever shape the agents need to
  actually *call* the sidecar (a Claude Code skill, an OpenCode tool, a
  shell helper) is out of scope for this phase and belongs in whichever
  agent's config/skill system is appropriate.

### Non-goals for this phase

- Prescribing a specific project, version, or install command.
- Writing the host-side launcher (launchd plist, brew service, or manual
  start). That's a choice the user should make based on how they manage
  other host services.
- Adding the sidecar's outbound destinations to `~/.claude-agent/allowlist.txt`.
  The sidecar has its own (unrestricted) network stack on the host; the
  allowlist governs only the VM's egress.

### Files (at minimum)

- `start-agent.sh` — one new iptables rule, one `NO_PROXY` addition, one
  env-var export, one preflight probe. Small footprint in the script.
- `README.md` — document how to start the sidecar and point it at Ollama.
- No new files inside the VM or the image.

### Testing (at minimum)

- With the sidecar running on the host: from inside the container,
  `curl -sf $RESEARCH_API/<health-endpoint>` succeeds.
- From inside the container, a research query via the sidecar returns a
  cited answer within a reasonable time budget.
- With the sidecar stopped: preflight warns; the container still starts
  normally; the agents continue to work (just without the research tool).
- Allowlist enforcement still holds for direct egress — the container
  cannot reach arbitrary domains directly, only through the sidecar.

---

## Notes

- **Why in-VM tinyproxy + iptables, not host pf.** Considered and
  rejected. Host-side pf plus a host-side tinyproxy is marginally more
  isolated (the LLM is three layers away from the rules instead of two),
  but the setup pain — launchd plist, pfctl anchor file, vmnet CIDR
  discovery, `sudo` for every reload — outweighs the benefit. The
  threat model is "LLM running as an unprivileged user in a docker
  container cannot modify rules." DOCKER-USER iptables satisfies that
  because the container's netns is distinct from the VM's. Escalation
  to VM root from a compromised container is outside the behavioral
  threat model. If the stronger isolation is ever needed, a follow-up
  ADR can add host-side pf as a second layer without redesigning the
  rest.

- **Why DOCKER-USER, not a fresh chain.** Docker rewrites its iptables
  rules on service restart; user rules in its own chains get clobbered.
  `DOCKER-USER` is the explicitly-supported integration point that
  docker never touches.

- **Why re-apply firewall rules on every script run, instead of
  persisting.** Simpler. No `iptables-persistent` package, no systemd
  unit, no state drift between "rules the host script thinks are
  active" and "rules actually active after the last VM reboot." The
  cost is a ~1s colima-ssh round trip at the start of every invocation,
  which is negligible.

- **Allowlist is additive only.** Human edits add/remove domain entries.
  The set of open ports and protocols (TCP 8888 to the bridge IP, TCP
  11434 to the host) is fixed in the iptables rules. Keeping the
  editing surface (plain text domain list) and the enforcement surface
  (ports in code) separate makes the editing path very small and hard
  to misuse.

- **Destructive `--rebuild` requires confirmation.** Deleting a Colima
  VM is not reversible. Prompt first. This is a meaningful divergence
  from `start-claude.sh`, where `container rm` only affects a
  single-project microVM.

- **Single shared container caveat.** Two terminals running the script
  from two different directories cannot coexist — the second attach
  either fails the mount check or collides with the first session.
  The in-use detection in Phase 5 step 4 is the guard. If
  multi-project concurrency becomes a real need, move to
  per-project container names derived from `basename "$PROJECT_DIR"`
  (at which point the script starts to look more like
  `start-claude.sh`, just with Colima underneath).

- **Alternative proxy engines considered.** tinyproxy's filter is
  simple but coarse. If the allowlist grows past ~200 entries or
  needs finer semantics (per-path, per-method), squid with
  `acl ... dstdomain` is the conventional upgrade. Deferred until the
  need actually appears.

- **Seed allowlist is permissive, not final.** The list in Phase 3
  step 5 is a starting point oriented at development and research
  workflows. It errs toward permissive for the research/periodical
  categories; the user is expected to prune it to match their actual
  usage.
