# Fix start-claude resume hang

## Status

- [ ] Wait for microVM readiness before `container exec`

## Context

`start-claude.sh` reliably hangs on resume after `/exit`. The reattach path is:

```bash
# start-claude.sh:167-172
if [[ "$(container inspect "$CONTAINER_NAME" 2>/dev/null)" != "[]" ]]; then
  echo "Container '$CONTAINER_NAME' already exists — attaching."
  container start "$CONTAINER_NAME" 2>/dev/null || true
  container exec -it -w "$PROJECT_DIR" "${CONTAINER_ENV[@]}" "$CONTAINER_NAME" /bin/bash
  exit 0
fi
```

`container start` on Apple Containers is asynchronous: the CLI returns before
the per-project microVM is fully booted and its vsock stdio channel is ready.
The immediately-following `container exec` then races the boot and stalls.
`start-agent.sh` does not have this problem because it uses Docker against an
already-warm Colima VM, where `docker start` is just a namespace restart inside
a long-running host.

The script already uses a polling pattern elsewhere — at start-claude.sh:281-283
it polls `container inspect ... | grep -q '"status":"stopped"'` to wait for the
setup container to finish. The fix here is the symmetric "wait until running"
poll on the resume path, plus a probe that confirms the VM actually accepts
exec connections (status alone is not sufficient — Apple Containers reports
`running` before the agent inside the VM is listening).

The user chose this minimal fix shape: keep per-project microVMs, just close
the readiness race. They explicitly declined the broader cleanup (dropping
`|| true`, `exec`-ing the final command). Do not bundle those in.

## Goals

- Resume after `/exit` no longer hangs.
- No change to the fresh-create path or to anything outside the reattach block.
- No change to `start-agent.sh`, the Dockerfile, or any settings.
- Diff is small enough to read in one screen.

## Unknowns / To Verify

- **Exact readiness signal from `container inspect` for a started container.**
  The script's existing pattern checks for `"status":"stopped"`. The running
  state is almost certainly `"status":"running"`, but confirm by running
  `container inspect <name>` against an attached container before writing the
  grep pattern. Why it matters: a wrong status string makes the loop spin
  forever. How to verify: `container inspect <some-running-container> | grep status`.
- **Whether a status-only poll is sufficient, or whether an exec probe is
  also needed.** Apple Containers has historically reported `running` before
  the in-VM agent accepts exec — that is the actual race. The plan below
  includes a `container exec ... true` probe as belt-and-suspenders. If
  testing shows the status check alone reliably resolves the hang, the exec
  probe can be dropped. How to verify: try status-only first; if hangs
  recur, keep the exec probe.

---

## Steps

1. In `start-claude.sh`, replace the three-line reattach block at lines 167-172
   with a version that, after `container start`, waits for the microVM to be
   reported `running` and then probes that `container exec` actually works
   before launching the interactive shell. Concretely:
   - After `container start "$CONTAINER_NAME" 2>/dev/null || true`, poll
     `container inspect "$CONTAINER_NAME"` until its output contains
     `"status":"running"` (verify the exact string per Unknowns). Use
     `sleep 0.1` between probes to match the existing pattern at line 282.
   - Then poll `container exec "$CONTAINER_NAME" true >/dev/null 2>&1` until
     it returns 0. This confirms the vsock exec channel is live, which is
     the actual condition the interactive `container exec` needs.
   - Cap both polls with a timeout (e.g., 30 seconds total) and, on timeout,
     print a clear error message to stderr suggesting `--rebuild` and exit
     non-zero. Do not fall through to `container exec` after a timeout —
     that reproduces the original hang.
   - Then run the existing `container exec -it -w "$PROJECT_DIR" "${CONTAINER_ENV[@]}" "$CONTAINER_NAME" /bin/bash`
     unchanged.

2. Manually test:
   - From a state where the container exists and is stopped, run
     `start-claude.sh` and confirm the shell attaches without hanging.
     Run `/exit` from inside Claude Code, then re-run `start-claude.sh` —
     repeat several times to confirm the hang is gone across multiple
     resume cycles.
   - Confirm the fresh-create path is unaffected: `start-claude.sh --rebuild`
     in a project, then `start-claude.sh` again — first invocation builds
     fresh, second resumes successfully.
   - Force a timeout to verify the error path: temporarily set the timeout
     low (e.g., 1 second) and confirm the script exits with the intended
     error message instead of hanging.

### Files

- `start-claude.sh` — only the reattach block at lines 167-172.

### Testing

Covered inline in step 2. There is no unit test harness for `start-claude.sh`
itself; verification is manual and behavioral. The existing tests under
`tests/` target `start-agent.sh`, `research.py`, and the Vane eval harness —
none cover the Apple Containers reattach path, and adding one is out of scope
for this fix.

---

## Notes

- The user explicitly rejected the broader cleanup of the reattach block
  (dropping `|| true` on `container start`, `exec`-ing the final
  `container exec`). Those would also be improvements but are out of scope.
- If the status-only poll turns out to be sufficient on its own, prefer
  dropping the exec probe — fewer moving parts. Decide based on testing,
  not on speculation.
- The polling timeout should fail loud rather than fall through. Silent
  fallthrough would recreate the original symptom.
