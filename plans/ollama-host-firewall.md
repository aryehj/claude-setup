# ollama-host-firewall

> **Note:** This plan is unnecessary if using `--backend=omlx`.
> omlx's `--api-key` flag provides application-layer auth that
> prevents unauthorized LAN access without a host-side firewall.
> See ADR-012 in `ADR.md`.

Host-side macOS firewall so Ollama (TCP 11434) on the Mac is reachable only
from (a) the loopback interface and (b) the vmnet bridges that
`start-claude.sh` and `start-agent.sh` attach their containers/VMs to.
Everything else — Wi-Fi/LAN, Thunderbolt bridge, VPN tunnels, AirDrop —
is dropped.

This plan exists because the work was started mid-conversation and not
finished; the goal is to make it safe to pick up later without redoing the
discovery.

## Status

- [x] Phase 0: Discovery (interfaces, current Ollama bind, baseline tests)
- [ ] Phase 1: Install pf anchor + load into running ruleset
- [ ] Phase 2: Persist via LaunchDaemon
- [ ] Phase 3: Flip Ollama to `0.0.0.0` and verify container path works
- [ ] Phase 4: End-to-end smoke tests (local, LAN, container)

## Context

### Why this is needed

`start-agent.sh`'s documented setup (see `CLAUDE.md` → "Ollama via host
networking") has the user run
`launchctl setenv OLLAMA_HOST 0.0.0.0:11434` so that the Colima container
can reach Ollama via the VM's default gateway (the macOS host). Binding to
`0.0.0.0` is what makes this work — but it also exposes Ollama to every
device on the user's Wi-Fi and any routed VPN peer. The user explicitly
does not want LAN to reach it.

### What the in-flight state actually is

Discovery done during the original session:

- **Wi-Fi**: `en0`, IPv4 `192.168.3.142/24`, gateway `192.168.3.1`.
- **vmnet bridges** (paired with `vmenet0` / `vmenet1`):
  - `bridge100` — Apple Containers (`start-claude.sh`).
  - `bridge101` — Colima (`start-agent.sh`). IPv4 `192.168.65.1/24` on the
    Mac side; the VM sees the Mac at `192.168.65.1`.
- **Other interfaces present**: `en1`–`en7`, `bridge0` (Thunderbolt),
  `anpi*`, `ap1`, `awdl0`, `llw0`, `utun0`–`utun3`. The user wants all of
  these **blocked**. Stated rationale: "I would rather debug a blockage
  later than realize I'd left something critical open when it's exploited."
- **Current Ollama bind**: `localhost:11434` (IPv4 only), confirmed via
  `sudo lsof -iTCP:11434 -sTCP:LISTEN`. This means LAN is currently
  unreachable *by accident*, and also that the Colima container's
  Ollama path is currently broken (baseline `curl` from inside the
  claude-agent container to `http://192.168.65.1:11434/api/tags` returned
  exit 7, connection refused — host reachable, nothing listening there).
- **VM→host firewall carve-out** (from `start-agent.sh`'s DOCKER-USER
  iptables rules) is working: the baseline curl got to the host and was
  refused at the application layer, not dropped at the network layer.

### Why we're doing firewall *then* rebind, not rebind *then* firewall

If we rebind Ollama to `0.0.0.0` first, there is a window — possibly
long, depending on when the user next finishes the plan — where Ollama
is reachable from every device on the Wi-Fi. Installing pf first, then
rebinding, closes that window. Phase ordering is deliberate; do not
reorder.

### Threat model and design decision

The goal is "no LAN peer can reach Ollama; loopback and vmnet bridges
can." Two mechanisms were considered:

1. **Bind-based**: keep Ollama on `localhost` and port-forward into the
   VM. Breaks the `start-agent.sh` HOST_IP pattern, requires maintaining
   port-forwards, and couples host config to VM topology. Rejected.
2. **Firewall-based** (chosen): rebind Ollama to `0.0.0.0` and use pf
   to allow only on `lo0`, `bridge100`, `bridge101`. Interface-scoped
   rules, not source-IP rules — simpler, and naturally handles both
   IPv4 and IPv6 without extra rules.

pf rule style: allow-list with `quick` (first-match-wins). Any new
interface the user adds later (a second Wi-Fi, a new VPN, another
bridge) is blocked by default unless explicitly added to the allow
list. Matches the user's stated preference for failing closed.

### Why interface-scoped and not source-IP-scoped

Source-IP rules would have to enumerate the Colima vmnet CIDR
(`192.168.65.0/24`) and the Apple Containers CIDR, and would need to
be updated if Colima resizes its subnet. Interface-scoped rules match
by interface name, which is stable across Colima restarts and vmnet
CIDR changes. Also easier to read.

## Goals

- Ollama on `0.0.0.0:11434` reachable from:
  - the macOS host itself (loopback),
  - any container on `bridge100` (Apple Containers / `start-claude.sh`),
  - any container on `bridge101` (Colima / `start-agent.sh`).
- Ollama unreachable from every other interface: `en0`, `en1`–`en7`,
  `bridge0`, `anpi*`, `ap1`, `awdl0`, `llw0`, `utun*`, and anything
  added in the future that isn't explicitly added to the allow list.
- Configuration persists across reboots via LaunchDaemon, since macOS
  resets pf state on boot.
- Revert is a documented three-command sequence.

## Artifacts

### pf anchor file — `/etc/pf.anchors/com.user.ollama`

Mirrors Apple's default `/etc/pf.conf` anchor references first (so
we don't silently strip macOS built-ins when we load this as the main
ruleset), then our rules:

```
scrub-anchor "com.apple/*"
nat-anchor "com.apple/*"
rdr-anchor "com.apple/*"
dummynet-anchor "com.apple/*"
anchor "com.apple/*"
load anchor "com.apple" from "/etc/pf.anchors/com.apple"

# Ollama: allow loopback + vmnet bridges, block everything else to :11434
pass in quick on lo0 proto tcp to any port 11434
pass in quick on bridge100 proto tcp to any port 11434
pass in quick on bridge101 proto tcp to any port 11434
block drop in quick proto tcp to any port 11434
```

`quick` short-circuits on first match. Loopback and both vmnet bridges
pass; everything else hitting port 11434 is dropped silently
(`block drop`, not `block return`, so the port is a black hole to
LAN scanners). Rules apply to both IPv4 and IPv6 (pf defaults to
both address families unless `inet`/`inet6` is specified).

### LaunchDaemon — `/Library/LaunchDaemons/com.user.ollama-firewall.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.ollama-firewall</string>
    <key>ProgramArguments</key>
    <array>
        <string>/sbin/pfctl</string>
        <string>-Ef</string>
        <string>/etc/pf.anchors/com.user.ollama</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/var/log/ollama-firewall.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/ollama-firewall.log</string>
</dict>
</plist>
```

Ownership/mode must be `root:wheel` / `644` or `launchctl bootstrap`
will refuse to load it.

## Phase 1: Install pf anchor + load into running ruleset

### Steps

1. Write the anchor file content above to `/etc/pf.anchors/com.user.ollama`
   via `sudo tee`. The file must be owned by root.
2. Parse-check the ruleset without loading it:
   `sudo pfctl -nf /etc/pf.anchors/com.user.ollama`. Expect no output
   (or only non-fatal warnings). If it errors, fix syntax before
   proceeding — do not run step 3 with a broken file, because `-Ef`
   will still toggle pf enablement even if the parse fails.
3. Enable pf and load the rules as the main ruleset:
   `sudo pfctl -Ef /etc/pf.anchors/com.user.ollama`.
4. Verify active:
   `sudo pfctl -sr | grep 11434` — the four `pass`/`block` lines should
   be echoed back.

### Files

- `/etc/pf.anchors/com.user.ollama` (new)

### Testing

- `sudo pfctl -s info` — `Status: Enabled`.
- `sudo pfctl -sr | grep 11434` — four lines present.

## Phase 2: Persist via LaunchDaemon

### Steps

1. Write the plist content above to
   `/Library/LaunchDaemons/com.user.ollama-firewall.plist`.
2. `sudo chown root:wheel /Library/LaunchDaemons/com.user.ollama-firewall.plist`
3. `sudo chmod 644 /Library/LaunchDaemons/com.user.ollama-firewall.plist`
4. `sudo launchctl bootstrap system /Library/LaunchDaemons/com.user.ollama-firewall.plist`

### Files

- `/Library/LaunchDaemons/com.user.ollama-firewall.plist` (new)
- `/var/log/ollama-firewall.log` (created by launchd on first run)

### Testing

- `sudo launchctl print system/com.user.ollama-firewall` — state looks
  healthy (no `last exit code` red flags).
- Reboot the Mac, then re-run `sudo pfctl -sr | grep 11434` — rules
  still present.

## Phase 3: Flip Ollama to 0.0.0.0 and verify container path works

Order matters: pf rules must already be active before rebinding, so LAN
never sees an exposed port.

### Steps

1. Sanity-check Phase 1 is done: `sudo pfctl -sr | grep 11434` returns
   four lines.
2. `launchctl setenv OLLAMA_HOST 0.0.0.0:11434` — persists across Ollama
   restarts via launchd's environment.
3. Restart Ollama so it picks up the new bind. If using the menu-bar
   app: quit from the menu, relaunch. If using `ollama serve` in a
   terminal: kill and restart.
4. Verify the new bind:
   `sudo lsof -iTCP:11434 -sTCP:LISTEN` — should now show `*:11434`
   (or `0.0.0.0:11434`), not `localhost:11434`.

### Files

- None. This phase is pure state.

### Testing

- From the Mac host itself:
  `curl -sS http://127.0.0.1:11434/api/tags` → returns JSON.
- From inside a running `claude-agent` container (the same path that
  returned exit 7 during baseline testing):
  `curl -sS -m 5 http://192.168.65.1:11434/api/tags` → returns JSON.

## Phase 4: End-to-end smoke tests

Three-way verification that the firewall is doing what we intend.

### Steps

1. **Localhost path works.** Run on the Mac:
   `curl -sS http://127.0.0.1:11434/api/tags` → JSON.
2. **Container path works.** From inside a running `claude-agent`
   container: `curl -sS -m 5 http://192.168.65.1:11434/api/tags` → JSON.
   If the container isn't running, use:
   `docker --context colima-claude-agent run --rm claude-agent:latest curl -sS -m 5 http://192.168.65.1:11434/api/tags`
3. **LAN path blocked.** From another device on the same Wi-Fi (phone,
   laptop): `curl -m 5 http://192.168.3.142:11434/api/tags` → hangs for
   5s then times out (exit 28). If it refuses instantly (exit 7), the
   `block drop` didn't take effect — check `pfctl -sr` and `pfctl -si`.
4. **LAN path blocked from the same Mac's LAN interface** (defense
   against loopback-via-en0 weirdness). On the Mac itself:
   `curl -m 5 http://192.168.3.142:11434/api/tags` → should also time
   out, because traffic to the Mac's own LAN IP arrives via `en0`
   before it gets to the stack, and pf's `in` rule on `en0` drops it.
5. **VPN path blocked.** If a VPN is up (`utun*` interface has a peer
   address), from a peer on the VPN: same curl, same expected timeout.
   Optional — only run if the user has an active VPN to test against.

### Files

- None. Verification only.

## Revert

Three commands, in order:

```bash
sudo launchctl bootout system /Library/LaunchDaemons/com.user.ollama-firewall.plist
sudo rm /Library/LaunchDaemons/com.user.ollama-firewall.plist
sudo pfctl -F rules && sudo pfctl -f /etc/pf.conf
sudo rm /etc/pf.anchors/com.user.ollama
```

Optionally also:
`launchctl unsetenv OLLAMA_HOST` and restart Ollama, to return to the
original `localhost:11434` bind.

## Open questions / things to verify at pick-up time

1. **Is Ollama still bound to `localhost`?** Re-run
   `sudo lsof -iTCP:11434 -sTCP:LISTEN` before starting. If the user
   has already rebound it since this plan was written, Phase 3 step 2
   may be a no-op but Phase 1 and 2 still need to happen.
2. **Has the Colima vmnet bridge changed numbers?** `ifconfig bridge101`
   should still show `192.168.65.1`. If Colima was reinstalled, the
   bridge could be `bridge102` / `bridge103`. Update the pf anchor file
   to match before loading. Check with:
   `ifconfig -l | tr ' ' '\n' | grep bridge` and cross-reference with
   `colima status -p claude-agent` / `docker context inspect colima-claude-agent`.
3. **Has Apple Containers's bridge changed?** Same check for `bridge100`.
   If only one of `start-claude.sh` / `start-agent.sh` is in use, the
   unused bridge's `pass` rule is harmless and can stay.
4. **Does the user want `bridge0` (Thunderbolt) blocked or allowed?**
   Answered in the original conversation: **blocked**. Documented here
   so a future pass doesn't re-litigate it.
5. **Does the user want `utun*` (VPN) blocked?** Answered: **blocked**.
   Same note.

## Notes

- **Why allow on bridge interfaces instead of source CIDR.** Interface-
  scoped rules survive Colima subnet changes without edits, and they
  don't need a separate rule for IPv6 link-local addresses that might
  appear on the bridge.
- **Why `block drop` and not `block return`.** `drop` is silent — LAN
  scanners see a black hole. `return` sends a TCP RST or ICMP
  unreachable, which leaks the fact that the host is up and filtering.
  Silence is the smaller information disclosure.
- **Why we load our file as the main ruleset and not as a named
  anchor.** macOS's `/etc/pf.conf` gets reset by OS updates, so
  editing it to `anchor "com.user.ollama"` + `load anchor …` is
  fragile. Loading our file directly with `pfctl -f` replaces the
  main ruleset entirely, which is why we include the Apple anchor
  references at the top of our file — those are what `/etc/pf.conf`
  would have loaded anyway. Net effect: our file is a superset of
  stock pf.conf.
- **Why the LaunchDaemon re-runs `pfctl -Ef` at boot.** pf's
  enablement and loaded ruleset do not survive reboot. `-Ef`
  idempotently (re-)enables pf and (re-)loads the file. Running it at
  boot covers the reboot case; running it on `start-agent.sh`
  invocation would be defensive but is not necessary given the
  LaunchDaemon.
- **Why no host-side tinyproxy or squid.** That would belong in
  `start-agent.sh`'s scope, not in a host-level Ollama firewall. This
  plan is deliberately narrow: one port, one allow list.
- **Conversation baseline worth preserving.** During discovery, the
  baseline curl from inside the claude-agent container to
  `http://192.168.65.1:11434/api/tags` returned exit 7 (connection
  refused). That confirmed three things at once: VM→host path open,
  iptables DOCKER-USER allowlist carve-out working, and Ollama not
  yet listening on the bridge IP. Any future debugging should
  re-run that exact curl as the first sanity check.
