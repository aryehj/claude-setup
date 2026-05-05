# Egress firewall for start-claude.sh (research.py-inspired)

## Status

- [ ] Phase 1: Non-root `agent` user + `$HOME` relocation in the image
- [ ] Phase 2: tinyproxy + iptables packages + container entrypoint (firewall-apply, drop privileges)
- [ ] Phase 3: Host-side allowlist file, template, seed/reseed plumbing
- [ ] Phase 4: Wire bind-mount, proxy env vars, and revised `container run` / `container exec` calls
- [ ] Phase 5: `--reload-allowlist` fast path
- [ ] Phase 6: Tests, README, CLAUDE.md, ADR-033

## Context

`start-claude.sh` today gives every project its own Apple Containers microVM
running `claude-dev:latest`. There is no egress firewall — the in-container
agent has unrestricted outbound access to anything the host can route to. The
existing `start-agent.sh` and `research.py` siblings both enforce VM-level
egress controls (tinyproxy + iptables in `start-agent.sh`'s shared Colima VM,
Squid + iptables in `research.py`'s Colima VM) but `start-claude.sh` does not,
because Apple Containers does not give us a "VM the container runs inside but
cannot touch" — each container *is* its own microVM, so root in the container
is root in the kernel that holds the iptables rules.

ADR-010 considered "in-container iptables + in-container proxy" for
`start-agent.sh` and rejected it: "trivially bypassable: the LLM is `root`
inside the container by default, and a single `iptables -F` (or editing the
proxy config) undoes it." That conclusion holds *while the agent is root*. The
design here closes that loophole by running the agent as a non-root user inside
the container — `iptables -F` requires `CAP_NET_ADMIN`, which a non-root
process does not have.

The result is loosely structurally similar to `research.py`: a default-deny
allowlist, a forward proxy, iptables rules that bracket which traffic is
allowed to leave. The trust placement is different (in-container, but
privilege-walled off from the agent) and the policy shape is different
(allowlist vs. denylist), but the building blocks rhyme.

Concrete starting points in the current code:
- `start-claude.sh:196-278` — the bash heredoc that builds `claude-dev:latest`.
  Everything image-level is here: apt installs, Node, uv, Claude Code CLI,
  bashrc, profile.d. All paths assume `$HOME=/root`.
- `start-claude.sh:309-345` — host state directories (`CLAUDE_CONFIG_DIR`,
  `CLAUDE_JSON_FILE`) and global settings.
- `start-claude.sh:376-387` — `container run` invocation. Bind-mounts
  `$CLAUDE_CONFIG_DIR → /root/.claude` and `$CLAUDE_JSON_FILE →
  /root/.claude.json`. CMD is `bash`. No `--user`, no entrypoint script.
- `start-claude.sh:166-172` — re-attach path: `container exec -it -w … bash`
  on an existing container.
- `dockerfiles/claude-agent.Dockerfile` — out of scope (different script).
  But `start-agent.sh:520-534` and `start-agent.sh:738-774` are the reference
  for tinyproxy config + filter generation that this plan reuses.

## Goals

- Every newly-built `claude-dev:latest` ships with tinyproxy + iptables and a
  non-privileged `agent` user. The agent shell drops privileges before any
  user-controlled code runs.
- Outbound traffic from the agent UID is REJECTed unless it goes through
  in-container tinyproxy, which only forwards to allowlisted domains.
- The allowlist file is bind-mounted `:ro` into the container; even if the
  agent escalates to root, the source-of-truth file on disk remains read-only
  to anything inside the container.
- The host-side allowlist lives at `~/.claude-dev/allowlist.txt`, seeded from
  `templates/start-claude-allowlist.txt` on first run, edited by the user, and
  applied via `start-claude.sh --reload-allowlist [container-name]` without
  recreating the container.
- Firewall is **on by default** for any new container. Existing containers
  built from the pre-firewall image keep their current behavior until the user
  runs `--rebuild`.
- No per-project allowlist override (deferred). One shared list governs every
  start-claude container.
- No state migration of `~/.claude-containers/`: changing the in-container
  $HOME from `/root` to `/home/agent` happens at the bind-mount target, not on
  the host, so the host-side directory keeps its existing path.

## Approach

Three coordinated changes carry the design.

**The image grows a non-root agent user and the firewall machinery.** All of
the existing tooling (Claude Code CLI, uv, Node, git identity, UV_CACHE_DIR
exports) gets relocated under `/home/agent/`. tinyproxy and iptables packages
get installed at image-build time. A new `/usr/local/bin/firewall-apply.sh`
script runs at container start: it generates the tinyproxy filter from the
RO-mounted allowlist, applies a small iptables ruleset based on
`-m owner --uid-owner`, starts tinyproxy as user `tinyproxy`, and `exec`s
into a bash login shell as user `agent`.

**The script's `container run` and `container exec` paths drop privileges.**
The host script picks up a new bind-mount (`~/.claude-dev/allowlist.txt →
/etc/claude-dev/allowlist.txt:ro`), passes `HTTP_PROXY` /  `HTTPS_PROXY` /
`NODE_USE_ENV_PROXY=1` env vars (matching `start-agent.sh`'s wiring), and runs
the container's entrypoint as root so the firewall can apply, then internally
drops to `agent`. Re-attach (`container exec`) goes via `runuser -u agent --
bash` so existing-container reconnects also land in the unprivileged user.

**The reload UX matches `start-agent.sh`'s fast path.** Editing
`~/.claude-dev/allowlist.txt` and running `start-claude.sh --reload-allowlist
[container]` `container exec`s into the named container as root, regenerates
the in-container filter file, and SIGHUPs tinyproxy — no container restart, no
agent disruption.

The biggest design risk is that Apple Containers's microVM kernel may lack
some netfilter modules (e.g., `xt_owner`, `xt_conntrack`). If `xt_owner` is
missing, the per-UID match fails and we fall back to a coarser scheme: drop
all egress except loopback + DNS + the proxy's outbound UID, achieved by
having tinyproxy be the only process besides PID 1's setup that uses the
network. Either way the agent UID can't egress directly — it's just a question
of which iptables match expression encodes that. Verification step in
Unknowns.

## Unknowns / To Verify

1. **Does Apple Containers's microVM kernel ship `xt_owner` and
   `xt_conntrack`?** The whole "agent UID can't egress, tinyproxy UID can"
   structure depends on `iptables -m owner --uid-owner`. Verify by building a
   throwaway image with `iptables -L` and `iptables -m owner -h` once Phase 1
   image is up. If `xt_owner` is missing, fall back to the coarser scheme
   (Approach §3): default-DROP egress, allow only loopback, DNS, and rely on
   tinyproxy being the sole egressing process. *Affects: Phase 2 step 4.*

2. **Does `container exec` accept `--user`?** Apple Containers's CLI may or
   may not support per-exec UID selection. If not, the re-attach path uses
   `runuser -u agent -- bash` inside the exec command rather than relying on
   the flag. Verify by running `container exec --user agent <name> id`
   against any existing container; if the flag is unrecognized, route around
   it with `runuser`. *Affects: Phase 4 step 4.*

3. **Does `container run` propagate `CMD ["/usr/local/bin/firewall-apply.sh"]`
   when an explicit command (`bash`) is passed?** The current invocation at
   `start-claude.sh:386` ends with `"$IMAGE_TAG" bash`, which overrides the
   image CMD. The new design wants the entrypoint to *always* run regardless
   of trailing args. Two paths: (a) use a Dockerfile `ENTRYPOINT` (Apple
   Containers may or may not honor `ENTRYPOINT` separately from `CMD`); (b)
   change the host script to launch the entrypoint script directly and have
   it `exec` the user's command. Verify behavior with a one-off test:
   build an image with `ENTRYPOINT ["/bin/echo", "ENT"]` and `CMD ["arg"]`,
   then `container run image foo` — see whether `ENT foo` or `foo` runs.
   Prefer (b) regardless of result, since it is portable. *Affects: Phase 2
   step 5; Phase 4 step 3.*

4. **Bind-mount file ownership and mode propagation under Apple Containers's
   virtiofs.** A file owned by macOS UID 501 on the host needs to be readable
   by `agent` inside the container, but writable to *no one*. macOS file
   permissions usually surface as "owned by the host UID, world-readable, world-
   non-writable" inside a Linux container. Verify by mounting a host file
   `0644 root:root` (or the macOS user-owned equivalent) and `cat`ing it as
   the agent user. The `:ro` bind-mount option provides the unwritable-from-
   inside guarantee even if the underlying mode would allow writes. *Affects:
   Phase 4 step 1.*

5. **`tinyproxy` bind interface inside the container.** The default
   `Listen 127.0.0.1` is correct here (loopback is per-netns, the host doesn't
   see it). But Claude Code may run tools inside its own bubblewrap sandbox,
   which could remount or limit `/proc/net/tcp` visibility. Verify the
   in-bwrap shell can still hit `http://127.0.0.1:8888` by running a probe
   from inside a sandboxed Claude Code session. If broken, bind tinyproxy to
   the container's eth0 IP instead and update env vars. *Affects: Phase 2 step
   3, Phase 6 smoke tests.*

6. **Existing containers: do we forcibly recreate, or warn?** The current
   `start-claude.sh:167-172` re-attaches to an existing container without
   touching the image. After the new image lands, an existing pre-firewall
   container will keep running with no firewall. Decide between (a) silent
   re-attach (least disruption, surprise-free) or (b) print a one-line warning
   like "this container predates the firewall; --rebuild to apply." Either
   is defensible; (b) is recommended. Verifiable by inspecting the container's
   image digest against the current `claude-dev:latest`. *Affects: Phase 4
   step 5.*

---

## Phase 1: Non-root `agent` user + `$HOME` relocation in the image

### Steps

1. In the setup heredoc at `start-claude.sh:196-278`, before any tool installs,
   create the agent user:
   ```
   useradd --create-home --shell /bin/bash --uid 1000 agent
   ```
   Use UID 1000 (typical for Debian first-non-system user). Document the
   choice; do not match the host UID — Apple Containers's virtiofs handles UID
   translation for us, and pinning a Linux UID is more stable across hosts.
   Do NOT add `agent` to `sudoers`.

2. Move every `~/.bashrc`, `/root/.local/bin/claude`, `~/.gitconfig`, etc.
   reference under `/home/agent/` instead of `/root/`. Concretely:
   - `claude` installer (`start-claude.sh:241`): run as `agent` via
     `runuser -u agent -- bash -c 'curl -fsSL https://claude.ai/install.sh |
     bash'` so the binary lands in `/home/agent/.local/bin/`. Symlink to
     `/usr/local/bin/claude` stays.
   - `bashrc` writes (`start-claude.sh:243-271`): write to
     `/home/agent/.bashrc` with `chown agent:agent` afterwards. Keep
     `/etc/profile.d/uv-cache.sh` and `/etc/profile.d/git-identity.sh` as-is
     — those are system-wide and the agent user picks them up at login.
   - `git config --global` (`start-claude.sh:264-265`): run with
     `runuser -u agent -- git config --global …` so it writes to
     `/home/agent/.gitconfig`.

3. The container's `$HOME` for the agent must be `/home/agent`. Add an
   explicit `WORKDIR` only in the entrypoint exec (Phase 2); the Dockerfile
   itself stays minimal (`FROM scratch + ADD rootfs.tar`).

4. Update `CLAUDE_CONFIG_DIR` and `CLAUDE_JSON_FILE` bind-mount targets in the
   `container run` block at `start-claude.sh:382-383` from `/root/.claude` and
   `/root/.claude.json` to `/home/agent/.claude` and `/home/agent/.claude.json`.
   Host paths (`$HOME/.claude-containers/shared/`, `$HOME/.claude-containers/
   claude.json`) are unchanged. CLAUDE.md will note the in-container path
   change.

5. Adjust `CONTAINER_ENV` at `start-claude.sh:47-54`: `TMPDIR=/tmp` stays.
   Add `HOME=/home/agent`. `GIT_*` vars are per-user-agnostic, no change.

### Acceptance criteria

- After build, `container run --rm claude-dev:latest id agent` returns a
  user with UID 1000 and no `sudo`/`wheel` group membership.
- `runuser -u agent -- claude --version` prints a version string (CLI is on
  the agent's PATH, binary symlinked to `/usr/local/bin/claude`).
- `runuser -u agent -- bash -c 'echo $HOME'` prints `/home/agent`.

---

## Phase 2: tinyproxy + iptables + container entrypoint

### Steps

1. In the same setup heredoc (`start-claude.sh:200-205`), extend the
   `apt-get install` line to include `tinyproxy iptables iptables-persistent`.
   `iptables-persistent` is unused at runtime but pulls in `xtables-addons-
   common` on some Debian variants, which sometimes carries `xt_owner`. If
   verification (Unknown #1) shows `xt_owner` is already in stock Debian
   bookworm's `iptables` package, drop `iptables-persistent`.

2. Disable the default tinyproxy systemd unit (the container has no systemd):
   ```
   systemctl disable tinyproxy 2>/dev/null || true
   ```
   (Most likely no-op since systemd isn't running, but harmless.) The
   entrypoint launches tinyproxy directly.

3. Create `/etc/tinyproxy/tinyproxy.conf` at image-build time with the
   filter configuration. Reuse `start-agent.sh:742-759`'s shape:
   ```
   User tinyproxy
   Group tinyproxy
   Port 8888
   Listen 127.0.0.1
   Timeout 600
   LogFile "/var/log/tinyproxy/tinyproxy.log"
   LogLevel Info
   MaxClients 100
   FilterDefaultDeny Yes
   Filter "/etc/tinyproxy/filter"
   FilterExtended Yes
   FilterURLs No
   ConnectPort 443
   ConnectPort 80
   ```
   The filter file (`/etc/tinyproxy/filter`) is generated by the entrypoint at
   container start; do not bake any default content into the image so the file
   on disk always matches the bind-mounted allowlist.

4. Create `/usr/local/bin/firewall-apply.sh` (also baked into the image).
   Pseudocode:
   ```sh
   #!/bin/sh
   set -e
   ALLOWLIST=/etc/claude-dev/allowlist.txt
   FILTER=/etc/tinyproxy/filter

   # Generate filter from RO allowlist mount.
   if [ -f "$ALLOWLIST" ]; then
     python3 -c "
   import re,sys
   for raw in open('$ALLOWLIST'):
     line=raw.split('#',1)[0].strip()
     if not line: continue
     print(f'(^|\\\\.){re.escape(line)}\$')
   " > "$FILTER"
   else
     # No allowlist mounted — closed-world default.
     : > "$FILTER"
     echo "warning: $ALLOWLIST missing; firewall will block all egress" >&2
   fi
   chmod 0644 "$FILTER"

   # Apply iptables rules. Conditional on xt_owner availability — see
   # Unknown #1. Branch decided at first run, cached as needed.
   /usr/local/bin/firewall-rules.sh

   # Start tinyproxy as user tinyproxy.
   tinyproxy -c /etc/tinyproxy/tinyproxy.conf
   # tinyproxy daemonizes; PID returned to caller.

   # Drop privileges and exec the user's command.
   exec runuser -u agent -- "$@"
   ```
   Place the iptables rule application in a separate
   `/usr/local/bin/firewall-rules.sh` so the reload path (Phase 5) can re-run
   only the filter regen + tinyproxy reload.

5. Create `/usr/local/bin/firewall-rules.sh` with the iptables ruleset.
   Two variants depending on Unknown #1.

   **xt_owner-available variant:**
   ```sh
   #!/bin/sh
   set -e
   iptables -F OUTPUT 2>/dev/null || true
   # Loopback always allowed (agent → tinyproxy is over lo).
   iptables -A OUTPUT -o lo -j ACCEPT
   # Established/related (return path).
   iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
   # tinyproxy can egress freely; it is the only way out.
   iptables -A OUTPUT -m owner --uid-owner tinyproxy -j ACCEPT
   # DNS for tinyproxy's own resolver work — already covered by --uid-owner
   # above, but keep an explicit rule for clarity if root setup needs it.
   iptables -A OUTPUT -p udp --dport 53 -m owner --uid-owner tinyproxy -j ACCEPT
   # Agent: REJECT all egress except loopback (matched above).
   iptables -A OUTPUT -m owner --uid-owner agent -j REJECT --reject-with icmp-admin-prohibited
   # Belt & suspenders: any uid not enumerated above is DROPped if
   # the destination is non-loopback.
   iptables -A OUTPUT -j DROP
   ```

   **xt_owner-unavailable fallback:** default-DROP OUTPUT, then ALLOW
   loopback, ALLOW UDP 53 (so tinyproxy's resolver works), ALLOW
   ESTABLISHED,RELATED. Tinyproxy must run from PID 1's userspace before any
   agent process starts; once it has its outgoing sockets open they ride
   ESTABLISHED. New tinyproxy connections are NEW state — so the fallback also
   needs an "allow all NEW from inside this container" rule, undermining the
   guarantee. Document this in the ADR; consider it a hard prerequisite that
   `xt_owner` exist. If verification shows it doesn't, escalate to the user
   before continuing implementation.

6. Bake the new scripts into the image at the right modes:
   ```
   chmod 0755 /usr/local/bin/firewall-apply.sh /usr/local/bin/firewall-rules.sh
   chown root:root /usr/local/bin/firewall-apply.sh /usr/local/bin/firewall-rules.sh
   ```
   Both are owned by root and not modifiable by `agent`.

### Acceptance criteria

- `container run --rm claude-dev:latest /usr/local/bin/firewall-rules.sh &&
  iptables -L OUTPUT` shows the expected rule set.
- After the entrypoint runs, the container's PID-1-tree has tinyproxy bound
  to `127.0.0.1:8888` and a bash login shell as `agent`.

---

## Phase 3: Host-side allowlist file, template, seed/reseed

### Steps

1. Add `templates/start-claude-allowlist.txt`. Seed content: a tighter subset
   of `start-agent.sh`'s allowlist focused on what a coding agent actually
   needs. Reuse the entry-classification logic from
   `start-agent.sh:222-511`, but trim research-specific entries (academic
   journals, news outlets, government statistics) — those belong to
   `start-agent.sh`. Required entries:
   - Anthropic / Claude (`anthropic.com`, `claude.ai`, `claude.com`)
   - Source-control READ-ONLY: `githubusercontent.com`, `githubassets.com`,
     `codeload.github.com`, `git-scm.com`. Omit `github.com` (write surface).
   - Package registries: npm, pypi, crates, rubygems, go-proxy, etc.
   - OS package repos: `debian.org`, `ubuntu.com`, `deb.nodesource.com`,
     `astral.sh`.
   - Docs: `mozilla.org`, `developer.mozilla.org`, `stackoverflow.com`,
     `readthedocs.io`, `docs.docker.com`.
   - Standards: `w3.org`, `whatwg.org`, `ietf.org`.

2. In `start-claude.sh`, add a constants block for the allowlist:
   ```bash
   ALLOWLIST_DIR="$HOME/.claude-dev"
   ALLOWLIST_FILE="$ALLOWLIST_DIR/allowlist.txt"
   ALLOWLIST_TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/templates/start-claude-allowlist.txt"
   ```

3. Add a seed step that runs before the existing-container check (so seed
   happens on every invocation, regardless of whether we attach or create):
   ```bash
   mkdir -p "$ALLOWLIST_DIR"
   if [[ ! -f "$ALLOWLIST_FILE" ]] || $RESEED_ALLOWLIST; then
     cp "$ALLOWLIST_TEMPLATE" "$ALLOWLIST_FILE"
     echo "==> Seeded allowlist at $ALLOWLIST_FILE"
   fi
   ```

4. Add `--reseed-allowlist` to the argparse loop at `start-claude.sh:23-34`,
   matching the existing `--reseed-global-claudemd` shape.

### Acceptance criteria

- First run on a fresh `$HOME` writes `~/.claude-dev/allowlist.txt` with
  template contents. Second run does not overwrite. `--reseed-allowlist`
  forces overwrite.

---

## Phase 4: `container run` / `container exec` rewiring

### Steps

1. Add the allowlist bind-mount and proxy env vars to `container run` at
   `start-claude.sh:376-387`:
   ```
   -v "$ALLOWLIST_FILE:/etc/claude-dev/allowlist.txt:ro"
   -e "HTTP_PROXY=http://127.0.0.1:8888"
   -e "HTTPS_PROXY=http://127.0.0.1:8888"
   -e "NO_PROXY=localhost,127.0.0.1"
   -e "NODE_USE_ENV_PROXY=1"
   ```
   The `:ro` ensures the agent (even if escalated) cannot rewrite the
   allowlist source.

2. Update `CONTAINER_ENV` at `start-claude.sh:47-54` to also include the
   proxy env vars and `HOME=/home/agent`. The `container exec` re-attach path
   reads `${CONTAINER_ENV[@]}` and so picks them up automatically.

3. Change the `container run` command from trailing `bash` to trailing
   `/usr/local/bin/firewall-apply.sh /bin/bash`. The entrypoint script (Phase
   2 step 4) runs as PID 1 (root), applies firewall, drops to agent, then
   `exec`s into the trailing `/bin/bash` as agent. This avoids the Dockerfile
   `ENTRYPOINT` ambiguity (Unknown #3).

4. Update the re-attach path at `start-claude.sh:166-172`. The existing call:
   ```
   container exec -it -w "$PROJECT_DIR" "${CONTAINER_ENV[@]}" "$CONTAINER_NAME" /bin/bash
   ```
   becomes:
   ```
   container exec -it -w "$PROJECT_DIR" "${CONTAINER_ENV[@]}" "$CONTAINER_NAME" runuser -u agent -- /bin/bash -l
   ```
   The `-l` makes it a login shell so `/etc/profile.d/uv-cache.sh` and
   `/etc/profile.d/git-identity.sh` are sourced. If Unknown #2 confirms
   `container exec --user agent` works, swap the `runuser` for it; otherwise
   keep `runuser`.

5. Existing-container detection: when an existing container is attached,
   compare its image to the current `claude-dev:latest`. If they differ
   (i.e., image was rebuilt), print a one-line warning:
   ```
   warning: container '$CONTAINER_NAME' was built from an older image without
   the egress firewall. Run 'start-claude.sh --rebuild' to apply it.
   ```
   Continue with re-attach regardless. (Unknown #6: this is the "warn, don't
   force" choice.)

### Acceptance criteria

- A new container started with the new script runs `id` inside as
  `uid=1000(agent)`.
- From inside the agent shell, `curl -sS --max-time 5 https://claude.ai >/dev/null`
  succeeds; `curl -sS --max-time 5 https://example.com >/dev/null` fails (not
  in default seed allowlist) with proxy-side rejection.
- From inside the agent shell, `curl -sS --max-time 5 --noproxy '*'
  https://claude.ai >/dev/null` fails with EHOSTUNREACH (iptables blocks
  direct egress).
- `cat /etc/claude-dev/allowlist.txt` succeeds; `echo x >>
  /etc/claude-dev/allowlist.txt` fails with EROFS or EACCES.
- `iptables -F` from inside the agent shell fails with EPERM.

---

## Phase 5: `--reload-allowlist` fast path

### Steps

1. Add `--reload-allowlist [container-name]` to the arg parser at
   `start-claude.sh:23-34`. The container-name positional is optional;
   if omitted, default to `basename "$(pwd)"` (matching the existing
   container-name resolution).

2. Implement the reload path:
   ```bash
   if $RELOAD_ALLOWLIST; then
     if [[ "$(container inspect "$CONTAINER_NAME" 2>/dev/null)" == "[]" ]]; then
       echo "error: container '$CONTAINER_NAME' does not exist." >&2
       exit 1
     fi
     # The bind-mount is RO; the allowlist file is already updated on disk
     # because we don't copy it through the script — host edits propagate
     # via virtiofs. Just regen the in-container filter and SIGHUP tinyproxy.
     container exec "$CONTAINER_NAME" /usr/local/bin/firewall-reload.sh
     echo "==> Reloaded allowlist in '$CONTAINER_NAME'"
     exit 0
   fi
   ```

3. Add `/usr/local/bin/firewall-reload.sh` to the image (Phase 2 step 6
   bakes it in). It regenerates the filter from `/etc/claude-dev/allowlist.txt`
   (using the same Python snippet as `firewall-apply.sh`) and `pkill -HUP -x
   tinyproxy`.

4. Note the iptables rules don't change on reload — only the filter file +
   tinyproxy SIGHUP. iptables already has the static "agent → REJECT, tinyproxy
   → ACCEPT" structure.

5. Document in `--help` (the comment block at `start-claude.sh:1-13`): one
   shared list governs all start-claude containers; reloading is per-container
   because each container regenerates its own in-memory filter, but they all
   read the same host file.

### Acceptance criteria

- Editing `~/.claude-dev/allowlist.txt` and running `start-claude.sh --reload-
  allowlist <name>` updates the running container's filter without
  disconnecting the user's shell or restarting any process other than
  tinyproxy itself.

---

## Phase 6: Tests, README, CLAUDE.md, ADR-033

### Steps

1. Add `tests/test-claude-firewall.sh` — analogous to
   `tests/test-agent-firewall.sh`. Run from inside a `claude-dev` container,
   expect six checks:
   - `id` confirms the user is `agent` (UID 1000), not root.
   - `iptables -F` fails with EPERM.
   - `curl -sS --max-time 5 https://claude.ai >/dev/null` succeeds (allowlist
     hit).
   - `curl -sS --max-time 5 https://example.com >/dev/null` fails (not in
     allowlist; tinyproxy 403).
   - `curl -sS --max-time 5 --noproxy '*' https://claude.ai >/dev/null`
     fails (proxy bypass blocked at iptables).
   - `cat /etc/claude-dev/allowlist.txt` works; `echo x >>
     /etc/claude-dev/allowlist.txt` fails.
   Exit 0 only if all six pass.

2. Update README.md `start-claude.sh` section:
   - "Mounts" table: add the allowlist row, change `/root/.claude` to
     `/home/agent/.claude`.
   - New "Egress allowlist" subsection mirroring the `start-agent.sh`
     section (host file location, edit + reload workflow, why github.com is
     omitted).
   - Note that the firewall is on by default and `--rebuild` is needed to
     apply it to existing containers.

3. Update CLAUDE.md "Key decisions":
   - Add a bullet under start-claude.sh's section: "Egress firewall (default-
     deny tinyproxy + iptables) runs in-container; agent shell drops to a
     non-root user so the firewall can't be flushed. Allowlist at
     `~/.claude-dev/allowlist.txt`, RO-bind-mounted at `/etc/claude-dev/
     allowlist.txt`. See ADR-033."
   - Adjust the existing "claude is symlinked into /usr/local/bin" bullet —
     installer now runs as `agent`, not root, so the home dir changes from
     `/root` to `/home/agent`. (Symlink itself is unchanged.)
   - Adjust the `~/.claude` shared-mount bullet: in-container target moves
     to `/home/agent/.claude`. Host path unchanged.

4. Add ADR-033 to ADR.md. Title: "start-claude: in-container firewall via
   non-root agent + tinyproxy + iptables (uid-owner match)". Body covers:
   - **Context.** ADR-010's reasoning (in-container firewall trivially
     bypassable with root agent) — true *only while the agent is root*.
     Apple Containers can't host the firewall in a "VM the container can't
     touch" the way Colima can. Two options: (a) host-side pf + host-side
     proxy, with substantial macOS setup pain; (b) in-container firewall
     with privilege drop to a non-root user. Chose (b) — keeps
     `start-claude.sh` self-contained with no host-side daemon, no `sudo` on
     reload, no pf anchors.
   - **Decision.** New `agent` user (UID 1000), tinyproxy bound to
     `127.0.0.1:8888`, iptables OUTPUT chain with `-m owner --uid-owner`
     enforcement, allowlist bind-mounted `:ro` at
     `/etc/claude-dev/allowlist.txt`, agent shell launched via `runuser
     -u agent`. Default-on for new containers; existing pre-firewall
     containers warn but keep working until `--rebuild`.
   - **Threat model.** Boundary is "agent UID can't make new outbound
     connections except via the proxy." A container escape (Linux LPE, kernel
     bug) defeats the firewall — same threat shape as `start-agent.sh`'s
     in-VM firewall after a VM root escape. Allowlist `:ro` mount means even
     a successful in-container privilege escalation can't rewrite the
     source-of-truth allowlist file (only the in-container filter, which is
     regenerated from it on every reload).
   - **Rejected alternatives.** Host-side pf + Squid (operational pain).
     Sidecar proxy container with Apple Containers network isolation
     (Apple Containers's network controls don't cleanly express
     "agent can only reach proxy"). No firewall at all (status quo;
     mismatched against `start-agent.sh` and `research.py`).
   - **Cross-references.** ADR-010 (start-agent.sh's VM-level enforcement);
     ADR-014 (SearXNG, not adopted here — start-claude.sh has no websearch
     story); future cross-link to ADR for the sandbox-trust-boundary plan
     when it lands.

5. Run static checks:
   - `bash -n start-claude.sh`
   - `grep -n '/root' start-claude.sh` — confirm only the comments / setup-
     image bookkeeping remain (no live mounts, env vars, or runtime paths
     pointing at `/root`).
   - `grep -nE 'allowlist' README.md CLAUDE.md ADR.md plans/start-claude-
     egress-firewall.md` — sanity check the new copy is internally consistent.

### Acceptance criteria

- `tests/test-claude-firewall.sh` exits 0 inside a freshly built container.
- README and CLAUDE.md mention the firewall, the allowlist path, and the
  agent UID. ADR-033 exists and is the highest-numbered ADR.

---

## Notes

- **Per-project allowlist override is deferred.** A future phase could honor
  `$PROJECT_DIR/.claude/allowlist.txt` if present, layered over the shared
  default. Out of scope here; one shared list keeps the design simple.

- **State migration.** No host-side state moves. The host directory
  `~/.claude-containers/shared/` keeps its existing path; only the
  in-container bind-mount target changes from `/root/.claude` to
  `/home/agent/.claude`. Existing auth (`~/.credentials.json`,
  `~/.claude.json`) survives unchanged. The shared mount is presented to a
  non-root user, which means `agent` needs read access — verify ownership
  surfaces correctly through virtiofs (Unknown #4). If permissions don't line
  up, the entrypoint can `chown -R agent:agent /home/agent/.claude` on every
  start (slow for big skills dirs, but correctness wins).

- **start-agent.sh is unchanged.** This plan touches `start-claude.sh`,
  `templates/`, `tests/`, README.md, CLAUDE.md, ADR.md only. The ADR
  references ADR-010 to explain why the trust placement differs.

- **`xt_owner` is the load-bearing assumption.** If Unknown #1 resolves to
  "missing," the design's per-UID guarantee collapses to a coarser default-
  DROP, which can't cleanly distinguish "agent making a NEW connection" from
  "tinyproxy making a NEW connection." Treat that as a hard stop and escalate
  to the user before continuing — there is no clean fallback that preserves
  the threat model. (One escape hatch: build a custom kernel module list into
  the Apple Containers VM image, but that's far beyond this plan's scope.)

- **No webfetch / websearch story for start-claude.sh.** Unlike
  `start-agent.sh` (which ships SearXNG by default for OpenCode), Claude Code
  in `start-claude.sh` has its own websearch tool and doesn't need a local
  search aggregator. The proxy controls outbound traffic for whatever the
  agent does; SearXNG is out of scope here.
