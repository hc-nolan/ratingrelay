---
date: 2026-05-10
topic: dry-run-mode
---

# Dry-Run Mode

## Summary

Add a dry-run toggle to the manual run dialog that executes the full relay pipeline — fetch, match, dedup — but suppresses all writes to external services. The result appears as a normal job in the job list with a "DRY RUN" badge and shows the same per-track event log as a real run.

---

## Problem Frame

When a user isn't sure their threshold is calibrated correctly, or is running a new relay for the first time, the only way to know what will sync is to run it and observe the result. A mis-sync to Last.fm or ListenBrainz (e.g., accidentally marking 300 tracks as loved) has no automated undo path — the user must manually reverse writes service by service. The same risk applies to mapping correctness: a recently resolved unmatched track mapping might be wrong, and there is currently no way to verify it without writing to a live service.

The gap is the absence of a commitment-free preview. The tool is fast enough to run the pipeline read-only; the only thing preventing it is that the write calls are unconditional.

---

## Key Flows

- F1. Dry-run a relay
  - **Trigger:** User clicks "Run" on a relay card where recurring is disabled
  - **Steps:**
    1. Run dialog opens; user enables the "Dry run" toggle
    2. User submits the dialog
    3. A one-off job is created with the dry-run flag set; the job is queued immediately
    4. The worker picks up the job, marks it running, and executes the full pipeline — source fetch, matching, dedup — without calling any external write APIs
    5. Per-track events are recorded (success/skip/error per track per target) based on what would have happened
    6. The job completes; aggregate counts reflect the simulated outcome
    7. The job list and detail page display the result with a "DRY RUN" badge
  - **Outcome:** User sees the full per-track outcome that a real run would have produced, with no writes made and no sync records updated
  - **Escape path:** If the source fetch itself fails (credentials invalid, service unreachable), the job fails as normal — dry-run does not mask source-side errors
  - **Covered by:** R1, R2, R3, R4, R5, R6, R7, R8, R9, R10

---

## Requirements

**Run dialog**

- R1. The run dialog includes a "Dry run" toggle. The toggle is hidden or disabled when the recurring option is active.
- R2. Submitting the dialog with the dry-run toggle enabled creates a one-off job with the dry-run flag set. Recurring and interval settings are not applied.

**Job execution**

- R3. A dry-run job executes the full relay pipeline: source fetch, track matching, and sync-record deduplication. All external writes to Plex, Last.fm, and ListenBrainz are suppressed.
- R4. Matching failures (unmatched tracks) appear as errors in the dry-run result — the simulation is faithful, not optimistic.
- R5. Tracks already present in the sync history appear as skipped. The simulation reads real sync state to produce this result.
- R6. A dry-run job does not write new sync records. A subsequent real run of the same relay must produce the same outcome as if the dry-run had not occurred.
- R7. Per-track event records are written so the job detail page shows the full simulated outcome (success / skip / error, per track per target service).

**Job presentation**

- R8. Dry-run jobs display a "DRY RUN" badge in the job list and job detail page.
- R9. Dry-run jobs use the same status lifecycle as normal jobs (queued → running → completed / partial / failed).
- R10. A completed dry-run job's aggregate counts (`tracks_fetched`, `tracks_ok`, `tracks_err`, `tracks_skipped`) reflect the simulated outcome, not actual writes.

---

## Acceptance Examples

- AE1. **Covers R1.** Given the run dialog is open and the recurring toggle is enabled, the dry-run toggle is not interactive (hidden or visually disabled).

- AE2. **Covers R3, R5.** Given track X was synced in a previous job (present in sync history), when a dry-run encounters track X, its event record shows it as skipped — not as a write attempt.

- AE3. **Covers R6.** Given a dry-run job completes with 20 tracks shown as "would sync," when the user runs the same relay as a real job immediately after, the real job syncs the same 20 tracks (the dry-run did not consume them as already-synced).

- AE4. **Covers R4.** Given a track that has no mapping and cannot be matched to the target service, when a dry-run encounters it, the event record shows it as an error — not silently skipped or counted as a success.

- AE5. **Covers R8, R9.** Given a dry-run job that completes, the job list shows the job with status "completed" and a "DRY RUN" badge. The job detail page also displays the badge.

---

## Success Criteria

- A user uncertain about their threshold or mapping configuration can run a dry-run and see the exact per-track outcome before making any writes.
- A real run of the same relay after a dry-run produces the same result as if the dry-run had not occurred — no sync state is polluted.
- Planning can proceed without inventing behavior for toggle visibility, sync record handling, event log content, or the badge display contract.

---

## Scope Boundaries

- Dry-run is not available for recurring or scheduled jobs.
- No "apply this dry-run" action that converts the dry-run result directly into writes without re-running the pipeline.
- Dry-run for reset mode is out of scope.
- The dry-run does not provide a diff against the previous job's result (that is a separate feature — see ideation doc).

---

## Key Decisions

- **`dry_run: bool` flag on the Job, not a separate status value:** Keeps the status lifecycle uniform and makes dry-run history queryable without special-casing the status field. The flag also makes it explicit in the API response without ambiguity.
- **One-off only:** A scheduled dry-run that never commits is misleading — the user's intent with recurring jobs is to automate real writes. Restricting dry-run to manual runs removes this confusion.
- **Writes suppressed, events still recorded:** The per-track event log is the primary value of a dry-run. Without it, the result is just aggregate counts with no actionable detail.
- **Simulation reads real sync state:** Ignoring sync history would show tracks that a real run would skip, producing an inaccurate preview. Reading the real sync records ensures the "would skip" count is accurate.

---

## Dependencies / Assumptions

- The Job model requires a schema migration to add `dry_run: bool` (default `false` for all existing rows).
- The frontend run dialog must conditionally hide or disable the toggle based on the recurring field state.
- The worker and relay execution layer must respect the flag to suppress writes without skipping the fetch, match, or dedup steps.

---

## Outstanding Questions

### Deferred to Planning

- [Affects R3, R6][Technical] How write suppression is threaded through `execute_relay` and the individual write functions — whether via a flag parameter, a wrapped callback, or another mechanism. The choice affects how much of the existing relay code must be modified vs. extended.
