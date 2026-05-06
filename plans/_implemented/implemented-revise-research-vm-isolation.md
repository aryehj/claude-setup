# Revise research-vm-isolation.md

## Status

- [x] Update the plan file to reflect current state

## Context

`plans/research-vm-isolation.md` proposed a 3-phase plan:
1. Create `research.sh` with isolated Colima VM (~400-500 lines bash)
2. Remove Vane from `start-agent.sh` (keep SearXNG for OpenCode)
3. Update documentation

`plans/implemented-research-python.md` was executed instead for Phase 1, producing `research.py` (813 lines, stdlib-only Python) with feature parity. ADR-018 documents this decision and the evaluation criteria that led to keeping Python. The original `research-vm-isolation.md` is now stale:
- Phase 1 references `research.sh` but `research.py` exists
- Phase 3 proposes ADR-018 for Vane extraction, but ADR-018 already documents the Python probe
- Status checkboxes are all unchecked despite Phase 1 being complete

Phases 2 and 3 are still pending work and should remain actionable.

## Goals

- `research-vm-isolation.md` accurately reflects that Phase 1 was implemented as `research.py`
- Phases 2 and 3 remain as pending work with updated references
- Line number references in Phase 2 are verified accurate
- ADR numbering in Phase 3 uses ADR-021 (next available)

---

## Steps

### 1. Update Status section

Change:
```markdown
- [ ] Phase 1: Create research.sh with isolated Colima VM
- [ ] Phase 2: Remove Vane from start-agent.sh (keep SearXNG)
- [ ] Phase 3: Update documentation
```

To:
```markdown
- [x] Phase 1: Create research script with isolated Colima VM (implemented as `research.py`)
- [ ] Phase 2: Remove Vane from start-agent.sh (keep SearXNG)
- [ ] Phase 3: Update documentation
```

### 2. Add implementation note to Phase 1

Insert at the top of Phase 1 (after `## Phase 1:` heading):

```markdown
> **Implemented.** This phase was completed as `research.py` (Python, stdlib-only)
> rather than `research.sh` (bash). See `plans/implemented-research-python.md` for
> the rationale and ADR-018 for the evaluation. The steps below are preserved for
> historical reference; actual implementation details are in `research.py`.
```

### 3. Update Phase 2 references

In Phase 2, change all occurrences of `research.sh` to `research.py` in the prose text. Specifically:
- The migration note at the end of Phase 2 ("Users who used Vane... should run `research.sh` instead") becomes "should run `research.py` instead"

### 4. Update Phase 3 step 1

Change the Layout section addition from:
```
- Add `research.sh` to the Layout section
```
To:
```
- Add `research.py` to the Layout section (already present if added during Phase 1 implementation)
```

### 5. Update Phase 3 step 1 key decisions

Change:
```
- Add a new "research.sh key decisions" section
```
To:
```
- Add a new "research.py key decisions" section (or verify it exists from Phase 1 implementation)
```

### 6. Update Phase 3 step 2 ADR numbering

Change:
```
- Add ADR-018 documenting the extraction of Vane into `research.sh`
```
To:
```
- Add ADR-021 documenting the extraction of Vane from `start-agent.sh` into standalone `research.py`. Reference ADR-018 (which documents the Python language choice) as prior art.
```

Also update the ADR-016 reference to note that ADR-018 covers the Python probe, not the Vane extraction:
```
- Update ADR-016 status: "**Superseded by ADR-021** for the Vane portion. The default-on SearXNG decision still stands for OpenCode's websearch backend."
```

### 7. Update Notes section

In the Notes section, change:
```
- **Migration path**: Users who used Vane at `localhost:3000` should run `research.sh` instead.
```
To:
```
- **Migration path**: Users who used Vane at `localhost:3000` should run `research.py` instead.
```

### 8. Verify Phase 2 line numbers

The plan references specific line numbers in `start-agent.sh`. Verify these are still accurate:
- Lines 195-197 (Vane variables): confirmed still accurate
- Lines 493-496 (rebuild cleanup): confirmed 493-495 exist
- Lines 799-823 (Vane container lifecycle): now 802-816 based on current grep output
- Lines 1181-1182 (startup output): now line 1182

Update any drifted line numbers in the Phase 2 steps.

## Files

- `plans/research-vm-isolation.md` (modify)

## Testing

- Read the updated plan and verify it makes sense as a standalone document
- Verify all file/line references resolve to the correct locations
- Confirm Phases 2 and 3 are actionable for a future implementer

## Notes

- This is a documentation-only change to an existing plan file
- No code changes; no changes to `research.py`, `start-agent.sh`, or ADR.md
- The actual Vane removal (Phase 2) and doc updates (Phase 3) remain future work
