---
title: "feat: Add dry-run mode for relay jobs"
type: feat
status: active
date: 2026-05-10
origin: docs/brainstorms/2026-05-10-dry-run-mode-requirements.md
---

# feat: Add dry-run mode for relay jobs

## Summary

Thread a `dry_run` flag from the Job model through the relay execution layer, suppressing external writes and sync record updates while leaving the fetch, match, and dedup paths unchanged. A toggle in the run dialog enables dry-run on manual one-off jobs; completed dry-run jobs display a "DRY RUN" badge in the job list and detail view.

---

## Problem Frame

Without a preview mode, the only way to verify relay behavior is to run it live. A mis-sync to Last.fm or ListenBrainz has no automated undo path. See origin document for full problem framing.

---

## Requirements

- R1. Run dialog includes a "Dry run" toggle, hidden when recurring is active or source is reset mode. (Reset-mode exclusion is a plan-level addition; origin AE1 only tested the recurring case. The exclusion is established in Scope Boundaries and Key Technical Decisions.)
- R2. Submitting with dry-run enabled creates a one-off job with `dry_run: true`; `recurring=true` combined with `dry_run=true` is rejected at the API level.
- R3. A dry-run job executes the full relay pipeline — fetch, match, dedup — suppressing all external writes.
- R4. Matching failures appear as errors in dry-run (faithful, not optimistic).
- R5. Already-synced tracks appear as skipped; `_load_synced` runs against real sync state.
- R6. Dry-run jobs do not write new `SyncRecord` rows; a subsequent real run sees unchanged sync state.
- R7. Per-track `TrackEvent` rows are written so the job detail page shows the full simulated outcome.
- R8. Dry-run jobs display a "DRY RUN" badge; same status lifecycle (queued → running → completed/partial/failed).
- R9. Aggregate counts reflect the simulated outcome.

**Origin flows:** F1 (dry-run a relay)
**Origin acceptance examples:** AE1 (recurring toggle hides dry-run), AE2 (already-synced → skipped), AE3 (sync state unchanged after dry-run), AE4 (unmatched → error), AE5 (badge displayed)

---

## Scope Boundaries

- Dry-run is not available for recurring or scheduled jobs.
- No "apply this dry-run" action.
- Reset-mode sources are excluded from dry-run.
- SSE event structure is unchanged — existing event types cover dry-run output.
- Job detail event list UI is unchanged — it already renders per-track outcomes.

### Deferred to Follow-Up Work

- Per-job "what changed" diff view (separate ideation survivor — distinct feature).
- Per-track retry from job detail (separate ideation survivor).

---

## Context & Research

### Relevant Code and Patterns

- `ratingrelay/models/jobs.py` — `Job` SQLModel; pattern for adding optional fields with defaults
- `ratingrelay/routes/jobs.py` — `JobCreateRequest`, `_job_to_dict`, duplicate-check logic, `create_job` handler
- `ratingrelay/core/worker.py` — `run_job`; reads job fields, calls `execute_relay`, schedules next run
- `ratingrelay/core/relay.py` — `execute_relay`, `write_to_plex`, `write_to_lastfm`, `write_to_listenbrainz`; `_load_synced`, `_record_sync`, `TrackCallback` / `_noop_track` pattern
- `ratingrelay/models/sync.py` — `SyncRecord`; `_record_sync` is called exactly once per successful write inside each write function
- `frontend/src/lib/types/jobs.ts` — `JobData` interface (canonical frontend type)
- `frontend/src/routes/+page.svelte` — `queueJob` function, `recurring` state, `buildRelayPayload`, run dialog markup
- `frontend/src/routes/jobs/[id]/+page.svelte` — job detail page; badge display area
- `frontend/src/lib/relay.ts` — `statusColor` and label utilities (model for any badge helpers)

### Institutional Learnings

- No directly applicable `docs/solutions/` entries found; plan follows established codebase patterns.

---

## Key Technical Decisions

- **`dry_run` flag on Job, not a separate status:** Keeps the status lifecycle uniform and avoids status-string proliferation. The flag is independently queryable without affecting the existing status machine. (see origin: Key Decisions)
- **Write suppression inside each write function, not at `execute_relay` boundary:** The three write functions each have distinct early-exit paths (credential failure, dedup skip). Suppression at the function level preserves all existing behavior and requires the smallest targeted change — the flag is received and acted on at the last possible moment before the external call.
- **Read-only operations still run during dry-run:** Plex library scan (`searchTracks`) and MBID lookup (`_lookup_recording_mbid`) are both read-only and produce the match/no-match result the user is trying to preview. Skipping them would produce an optimistic simulation that hides the most common failure mode.
- **Dry-run jobs excluded from duplicate check on both sides:** A dry-run doesn't consume relay state, so it shouldn't block real jobs or be blocked by them. The duplicate check is a guard against redundant writes, which dry-run never performs.
- **API rejects `dry_run=true` + `recurring=true`:** The UI hides the toggle when recurring is active, but a direct API call could still send the combination. A 400 at the API level makes the invariant explicit and testable.
- **Toggle hidden (not merely disabled) for reset source mode:** Reset is a destructive action with a fundamentally different execution path (`isinstance(source, ResetFromConfig)` short-circuits at `execute_relay`). Dry-run is not meaningful for reset, and the existing reset button area already has distinct styling — hiding keeps the surface clean.

---

## High-Level Technical Design

> *This table illustrates the intended behavior split between real and dry-run execution paths. It is directional guidance for review, not implementation specification.*

| Operation | Real run | Dry run |
|---|---|---|
| Source fetch | Runs | Runs (unchanged) |
| `_load_synced` (read sync history) | Runs | Runs (read real state) |
| Match / mapping lookup | Runs | Runs |
| External write (rate / love / feedback) | Called | Suppressed |
| `_record_sync` write | Written | Suppressed |
| `TrackEvent` write | Written | Written |
| SSE broadcast | Broadcast | Broadcast |
| Next run scheduled | If recurring | Never |
| Duplicate check | Applies | Excluded both ways |

---

## Implementation Units

### U1. Job model, migration, and API layer

**Goal:** Add `dry_run: bool` to the `Job` model, generate the Alembic migration, extend `_job_to_dict` and `JobCreateRequest`, and update the duplicate-check and create-job handler.

**Requirements:** R2, R8, R9

**Dependencies:** None

**Files:**
- Modify: `ratingrelay/models/jobs.py`
- Create: `alembic/versions/<hash>_add_dry_run_to_job.py`
- Modify: `ratingrelay/routes/jobs.py`
- Test: `tests/test_jobs_api.py` (create if absent)

**Approach:**
- Add `dry_run: bool = Field(default=False)` to `Job`; generate migration with `uv run alembic revision --autogenerate`. Note: if `tracks_skipped` was never captured in a prior migration, autogenerate may also emit it in the same file — inspect the generated file before applying. If `tracks_skipped` appears, verify it is absent from the live schema; the safer alternative is to hand-write `ALTER TABLE job ADD COLUMN dry_run BOOLEAN NOT NULL DEFAULT 0` to scope the migration to the single intended column.
- Add `dry_run: bool = False` to `JobCreateRequest`; validate that `dry_run=True` with `recurring=True` returns 400
- In `_job_to_dict`, include `dry_run`
- In the duplicate-check loop, skip the check entirely when either the new request has `dry_run=True` or an existing active job has `dry_run=True` (exclude dry-run jobs from both sides)
- Pass `dry_run=body.dry_run` when constructing `Job`

**Patterns to follow:**
- Existing optional fields in `Job` (`interval_minutes`, `run_at`) for the model pattern
- `JobCreateRequest` `recurring`/`interval_minutes` validation for the validation pattern

**Test scenarios:**
- Happy path: `POST /api/relay/jobs` with `dry_run: true` returns 201 with `dry_run: true` in response body
- Happy path: `POST /api/relay/jobs` without `dry_run` returns job with `dry_run: false`
- Error path: `POST` with `dry_run: true, recurring: true` returns 400
- Edge case: `POST` with `dry_run: true` while a real active job with matching config exists → 201 (not blocked)
- Edge case: `POST` real job while a dry_run job with matching config is active → 201 (not blocked)
- Edge case: two identical `dry_run: true` jobs queued → both accepted (no duplicate check between dry-run jobs)

**Verification:**
- Migration applies cleanly; existing jobs get `dry_run=false` by default
- All six test scenarios pass
- `GET /api/relay/jobs` and `GET /api/relay/jobs/{id}` both include `dry_run` in the response

---

### U2. Relay core — write suppression

**Goal:** Add `dry_run: bool = False` to `execute_relay` and the three write functions; suppress external API calls and `_record_sync` writes when `dry_run=True`, while leaving all read-only operations intact.

**Requirements:** R3, R4, R5, R6, R7, R9

**Dependencies:** None (independent of U1 at the relay layer)

**Files:**
- Modify: `ratingrelay/core/relay.py`
- Test: `tests/test_relay_dry_run.py` (create)

**Approach:**
- Add `dry_run: bool = False` to `execute_relay` signature; pass it through to each `write_to_*` call in the normal relay path (not to reset functions — reset is excluded)
- Add `dry_run: bool = False` to `write_to_plex`, `write_to_lastfm`, `write_to_listenbrainz`
- Extend the `TrackCallback` type with an optional `skipped: bool = False` parameter. The existing call sites that pass positional arguments are unaffected; the worker closure is updated to accept and branch on it (see U3).
- The already-synced check (`_load_synced` → `if norm_key in already_synced`) runs before the dry_run suppression branch — dry-run does not change when or how skips are detected, only whether `on_track` is called for them. In dry-run mode, call `on_track(track, target, skipped=True)` before the `continue` so a `TrackEvent` row is written for the skipped track. The `tracks_skipped` counter still comes from the return value (unchanged); `on_track` for skipped tracks does not increment `tracks_ok` or `tracks_err`.
- In each write function, after the match succeeds, branch on `dry_run`: if true, call `on_track(track, target, True, None)` directly and continue (skip the external call and `_record_sync`); if false, execute the existing write + record path
- `_load_synced` and `_load_mappings` calls remain unconditional — they run before the match loop in all cases
- The Plex library scan (`searchTracks`) and MBID lookup (`_lookup_recording_mbid`) also remain unconditional
- Credential-failure early exits are unconditional — dry_run doesn't bypass credential checks. Note: `write_to_listenbrainz` uses a manual `if not token:` guard rather than a try/except around a resolver — treat it as already correct rather than conforming it to the plex/lastfm pattern

**Patterns to follow:**
- The `_noop_track` pattern for how to skip a write while still calling the callback
- The existing `if norm_key in already_synced: skipped += 1; continue` pattern for the skip path

**Test scenarios:**
- Happy path: dry_run=True with matched tracks → `on_track` called with `success=True`, `_record_sync` not called
- Error path: dry_run=True with unmatched track (no Plex match / missing artist-title / no MBID) → `on_track` called with `success=False`, error message preserved
- Integration: dry_run=False after dry_run=True on same tracks → `_load_synced` finds no new rows, same tracks are written (Covers AE3)
- Error path: dry_run=True with credential error → same early-exit behavior as real run, error reported via `on_track`
- Edge case: dry_run=True with track already in `already_synced` set → `on_track` called with `skipped=True`, a `TrackEvent` row is written, no new `SyncRecord` (Covers AE2, R5, R7)

**Verification:**
- No `SyncRecord` rows are created during a dry-run execution
- Per-track `on_track` callback fires for every track (success, skip, or error) during dry-run
- A real run immediately after a dry-run produces the same track-level outcome as if the dry-run never ran

---

### U3. Worker — thread dry_run to execute_relay

**Goal:** Pass `job.dry_run` to `execute_relay` in the worker; ensure dry-run jobs never schedule a next recurring run.

**Requirements:** R3, R6, R8

**Dependencies:** U1 (job has `dry_run` field), U2 (`execute_relay` accepts `dry_run`)

**Files:**
- Modify: `ratingrelay/core/worker.py`
- Test: `tests/test_worker.py` (create or extend)

**Approach:**
- In `run_job`, pass `dry_run=job.dry_run` to `execute_relay`
- The "schedule next run" block already only runs when `job.recurring` is true; since dry-run jobs are always created with `recurring=False`, no additional guard is strictly necessary — but add an explicit `and not job.dry_run` condition to the recurring check to make the invariant explicit and testable
- The `on_track` closure gains a `skipped: bool = False` parameter (from U2's `TrackCallback` extension); when `skipped=True`, increment nothing — the `tracks_skipped` count comes from the result dict return value, not from the callback path

**Patterns to follow:**
- The existing `on_fetch_complete` / `on_track` callback passing pattern in `run_job`

**Test scenarios:**
- Integration: a dry_run job completes without a new `Job` row being created for the next run (Covers AE3 / R6)
- Integration: SSE events (status, track, done) are broadcast for dry_run jobs (same as real run)
- Edge case: `job.dry_run=True` and `job.recurring=True` (should not occur via normal API after U1 validation, but the worker guard still holds — no next run is scheduled)

**Verification:**
- A dry-run job executes end-to-end and reaches a terminal status without spawning a follow-up job
- SSE stream delivers the same event types for dry-run as for real jobs

---

### U4. Frontend — dry-run toggle in run dialog

**Goal:** Add a `dryRun` state variable and toggle to the run dialog; include `dry_run` in the `queueJob` payload; hide the toggle when recurring is active or the source is reset mode.

**Requirements:** R1, R2, R8

**Dependencies:** U1 (API accepts `dry_run`)

**Files:**
- Modify: `frontend/src/lib/types/jobs.ts`
- Modify: `frontend/src/routes/+page.svelte`

**Approach:**
- Add `dry_run: boolean` to the `JobData` interface in `$lib/types/jobs.ts`
- Add `let dryRun = $state(false)` alongside the existing `recurring` state in `+page.svelte`
- Reset `dryRun` to `false` when the recurring toggle is turned on (a `$effect` watching `recurring`)
- In `queueJob`, add `dry_run: dryRun` to the payload object; ensure `dryRun` is always `false` when `recurring` is `true` (the reset effect is the primary guard, but defensively force `false` at call time)
- In the run dialog markup, add the dry-run toggle as a distinct labeled block between the SCHEDULE section and the run button — not inside the SCHEDULE section (whose label doesn't describe a preview mode). Hide it with `{#if}` when `recurring` is true or `selectedSource === 'reset'`.
- When `dryRun === true`, render the run button label as "Preview Run" instead of "Queue Job", following the existing conditional label pattern (the button already has "Queue Reset" for reset source and "Queueing…" during submission).

**Patterns to follow:**
- `let recurring = $state(false)` and its associated markup for the toggle pattern
- The `$effect` that gates `intervalMinutes` display on `recurring` for the conditional reveal pattern

**Test scenarios:**
- Happy path: recurring=false, source≠reset → dry-run toggle is rendered and interactive
- Edge case: recurring=true → dry-run toggle is not rendered (Covers AE1)
- Edge case: source=reset → dry-run toggle is not rendered
- Integration: submitting with dry-run toggle enabled sends `dry_run: true` in the POST body
- Edge case: user enables recurring after enabling dry-run → `dryRun` resets to false
- Edge case: dry-run toggle enabled → run button renders as "Preview Run" (not "Queue Job")

**Verification:**
- The toggle appears in the UI when eligible and is absent when ineligible (recurring active or reset source)
- A queued job created via the toggle has `dry_run: true` visible in the job list response

---

### U5. Frontend — DRY RUN badge on job list and detail

**Goal:** Display a "DRY RUN" badge on jobs with `dry_run: true` in both the job list (home page) and the job detail page.

**Requirements:** R8

**Dependencies:** U4 (`JobData` has `dry_run`; API returns the field)

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/jobs/[id]/+page.svelte`

**Approach:**
- In the job list item template in `+page.svelte`, add a conditional badge element next to the status chip when `job.dry_run` is true
- In the job detail page, add the same badge near the job header status display
- Style as a small distinguishing label (not a status chip — it's an attribute of the job, not its lifecycle state); keep styling consistent between the two pages
- On the job detail page, when `job.dry_run === true`, render the `tracks_ok` stat label as "Would sync" instead of "Synced" — the fixed label is factually incorrect when no writes were made (R7, R9)

**Patterns to follow:**
- Existing status badge/chip markup in both pages for the visual pattern
- The `statusColor` utility in `$lib/relay.ts` for how service attributes are surfaced as labels

**Test scenarios:**
- Happy path: job list renders "DRY RUN" badge for `dry_run: true` jobs (Covers AE5)
- Happy path: job detail page renders "DRY RUN" badge for `dry_run: true` jobs (Covers AE5)
- Edge case: `dry_run: false` jobs show no badge in either view
- Edge case: `dry_run: true` job detail page → `tracks_ok` stat label reads "Would sync", not "Synced"

**Verification:**
- Both views display the badge consistently for dry-run jobs
- No visual regression on non-dry-run jobs

---

## System-Wide Impact

- **Interaction graph:** `execute_relay` and the three write functions gain a new boolean parameter; `run_job` in the worker passes it through. No callbacks, middleware, or observers are affected beyond the targeted call chain.
- **Error propagation:** Credential-failure early exits in write functions are unconditional — they run regardless of `dry_run`. The behavior contract for error paths is unchanged.
- **State lifecycle risks:** The primary risk is a dry-run accidentally writing a `SyncRecord`, which would cause a subsequent real run to skip tracks. The suppression in U2 must cover all three `_record_sync` call sites (one per write function). The test in U2 ("real run after dry-run produces same outcome") directly guards this.
- **API surface parity:** `_job_to_dict` (U1) must include `dry_run` so both the list endpoint and the SSE-adjacent get endpoint return the field; `JobData` in TypeScript (U4) must match. Both are covered in their respective units.
- **Integration coverage:** The real run after dry-run scenario (AE3) requires an integration test that actually reads `SyncRecord` rows — a unit test mocking `_record_sync` would not prove the invariant.
- **Unchanged invariants:** The SSE event structure (`status`, `track`, `progress`, `done`) is unchanged. The job status lifecycle (queued → running → completed/partial/failed) is unchanged. The `on_track` callback gains one optional `skipped: bool = False` parameter (U2/U3) — all existing positional call sites are unaffected; only the worker closure and the dry-run skipped-branch callers handle it explicitly.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `_record_sync` called in a code path not covered by dry_run guard | Three write functions each have one `_record_sync` call site; U2 test verifies zero SyncRecord rows after dry-run |
| Plex library scan during dry-run is slow (loads all tracks) | Unchanged from real run — no additional latency; acceptable given the preview value |
| Alembic migration adds column with default; concurrent reads during migration | SQLite ALTER TABLE is synchronous; single-user self-hosted tool, no concurrent migration risk |
| Frontend sends `dry_run: true` with `recurring: true` despite UI guard | U1 API-level 400 rejection is the backstop |

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-10-dry-run-mode-requirements.md](docs/brainstorms/2026-05-10-dry-run-mode-requirements.md)
- Related ideation: [docs/ideation/2026-05-10-user-control-surface-ideation.md](docs/ideation/2026-05-10-user-control-surface-ideation.md)
