---
title: "refactor(engine): extract engine/ and restore reconcile-don't-append"
type: refactor
status: active
date: 2026-11-12
ticket: RR-2
origin: docs/plans/2026-11-12-v3-baseline-and-workstreams.md
---

# refactor(engine): extract engine/ and restore reconcile-don't-append

## Summary

Move the relay out of `ratingrelay/core/relay.py` into a headless-first
`ratingrelay/engine/` package that has no FastAPI, request-schema, session or SSE
dependency, then fix the six regressions v2 introduced against the v1 behaviour
contract. The engine becomes the only place sync semantics exist; `api/` (today
`routes/` + `core/worker.py`) marshals into it and the future `cli/` will call it
directly.

Slices land as separate commits on one branch. Every slice keeps `just test` and
`just lint` green; no slice may leave the pipeline half-converted.

## Requirements

- R1. `engine/` imports neither FastAPI nor `ratingrelay.schemas.*` nor
  `sqlalchemy.AsyncSession`. Service clients are injected; the credential/mapping/
  sync store is a protocol.
- R2. Track identity is the MusicBrainz **recording** MBID; a track without one is
  unmatched, never guessed. The MBID cache in `SyncRecord` is read, not just written.
- R3. Reconcile every run: compare the source's current intents against the intents
  previously written to each target, converge the target, and only undo writes this
  tool made. A changed rating re-propagates; a withdrawn love un-loves.
- R4. Dry run and a real run make **identical decisions**; only the write calls are
  suppressed, through one boundary rather than a `dry_run` branch per writer.
- R5. Matching ladder MBID → exact → variant → containment. Only a unique match at a
  fuzzy tier is written; ambiguous candidates go to the unmatched queue attached to
  the entry, and an existing record is never silently re-pointed.
- R6. Outcomes are typed (status + reason + candidates), so `/unmatched` reads fields
  instead of matching error strings.
- R7. Reset undoes only this tool's writes; a whole-service wipe is a separate,
  explicitly-labelled action.
- R8. Plex fetch filters server-side (`userRating>>=` / `userRating<<=`) instead of
  fetching the library and filtering in Python.
- R9. No behaviour change may be introduced without a test that fails without it.
  The engine gets the fetch/match/reconcile/write/reset suite the repo never had.

## Slices

### S1 — Engine skeleton: normalisation, types, matching (done, `3b66003`)

`ratingrelay/engine/{__init__,normalize,types,matching}.py`. Pure functions and frozen
dataclasses only: no DB, no clients, no FastAPI. `normalize`/`track_key` become the
single source of truth for key formatting (existing `sync_records.keys` and
`track_mapping.keys` must keep hashing to the same string), `comparison_form` and
`variant_form` port v1's lax/containment forms, and `match_track` implements the
ladder with ambiguity handled explicitly. `core/relay.py` and `routes/unmatched.py`
import the key helpers instead of owning copies.

Exit: `tests/test_engine_matching.py` pins compatibility of the key format, all four
tiers, the ambiguous/unmatched paths, and intent keys.

### S2 — Engine pipeline behind ports (done)

What landed, and where:

```
engine/plan.py            WritePlan: the decision, plus the commit closure that writes it
engine/thread.py          run_sync, moved down from core/
engine/store.py           ports: SyncStore, MappingStore, ProgressSink + in-memory doubles
engine/credentials.py     plain credential values (no settings, no DB)
engine/services/*         plex / lastfm / listenbrainz / musicbrainz adapters
engine/pipeline.py        _apply (skip, plan, commit) + run_tracks + run
core/credentials.py       settings > stored row, resolved once at the edge
core/adapters.py          request config -> Source/Target, dict <-> Track
db/store.py               SqlSyncStore, SqlMappingStore (the ports, implemented)
```

`core/relay.py` keeps its public names (`execute_relay`, `fetch_*`, `write_to_*`,
`reset_*`) and its `(ok, err, skipped)` returns, so the router and
the worker did not change in this slice; under them it now builds targets and calls
the engine. Credential precedence (settings > env > DB) is unchanged, resolved in
`core/credentials.py` instead of per writer.

The clients live under `engine/services/` rather than `engine/clients/`: a client and
the source/target that uses it are one concern here, and splitting them would have
made a five-line adapter into three files.

Exit: `tests/test_engine_pipeline.py` drives the real pipeline against fake service
clients — dry run takes the same decisions as a real run, a repeated run skips,
ambiguity writes nothing and attaches candidates, a failed write is typed and not
recorded, and mappings/MBID resolution reach the right call. The 15 pre-existing tests
still pass with their assertions intact (only their injection points moved, because
the fakes must now sit where the adapters build clients).

### S3 — Intent-keyed SyncRecord + reconciliation (done)

What landed, and where:

```
models/sync.py            SyncRecord gains intent, rating, target_key (all nullable)
alembic/c1d2e3f4a5b6      additive migration + backfill of intent and LBZ target_key
engine/types.py           OutcomeStatus.REVERTED, Reason.WITHDRAWN / UNRECORDED_TARGET,
                          target_key on WritePlan/TrackOutcome, reverted count on TargetReport
engine/store.py           SyncedTrack.target_key, SyncedTrack.track (display names from the key)
engine/services/*         undo(record) on all three targets, each keyed by target_key
engine/pipeline.py        _apply compares the stored intent before skipping, _reconcile
                          undoes what the source withdrew; run_tracks takes source_service
db/store.py               SqlSyncStore.remember upserts; load reads intent/rating/target_key
core/relay.py             reverted writes count as ok; _record_sync removed (nothing used it)
```

`intent` is what reconciliation compares: `love`, `hate` or `rating:<value>`. A record
whose intent matches the plan is skipped; one that differs is written again; one whose
key the source no longer rates is undone through `target_key` and then forgotten. A
row with a NULL intent (written before v3) is rewritten once — it says only that
*something* was synced, so converging it is the only safe move. A row that cannot say
what to undo (`target_key` NULL and no MBID) is left in place with
`Reason.UNRECORDED_TARGET` rather than guessed at, because clearing it could remove a
rating the user made themselves.

The migration backfills only what is inferable: `lastfm` rows were loves, `plex` rows
were the configured rating (`rating:10.0`), and ListenBrainz rows get `target_key` from
`recording_mbid` because feedback is keyed by MBID — their intent stays NULL because
love and hate are indistinguishable after the fact.

Not done in this slice, deliberately: no unique constraint on
`(source_service, target_service, normalized_key)`. Upsert is a delete-then-insert in
`SqlSyncStore`, so a constraint would only add a migration failure mode on existing
databases; add it when a second writer exists.

Also fixed here: revision `c3d4e5f6a7b8` created `ix_trackevent_job_id` twice (once via
`index=True` on the column and once explicitly), so `alembic upgrade head` could not
run on a fresh database — and therefore this migration could not run either.

Exit: `tests/test_engine_reconcile.py` (withdrawn → un-loved, changed rating →
rewritten, matching intent → skipped, legacy row → rewritten, no-record → untouched,
dry run → reported but not undone) and `tests/test_db_store.py` (round trip, upsert,
scoping, forget, mapping precedence).

### S4 — Narrow reset, server-side Plex filter, typed outcomes (done)

Reset iterates `SyncRecord` rows for the target and undoes exactly those, deleting each
record it reverts. Whole-service wipe moves behind an explicit `reset --all`-style
flag that names the risk in its response. Plex reads use `searchTracks(**filters)`
with `userRating>>=`/`userRating<<=`. `TrackEvent` gains `outcome`/`reason`/
`candidates` columns; `/api/relay/unmatched` selects on `reason`, and
`_NO_MATCH_ERRORS` is deleted.

Exit: API tests assert unmatched entries carry reason + candidates; a Plex fake
asserts the rating filter reached the client.

What landed, and where:

| Concern | File | Notes |
| --- | --- | --- |
| Narrow reset | `ratingrelay/core/relay.py` (`_reset_recorded`) | Walks `SqlSyncStore.records_for_target(target)`, calls `Target.undo` per row, forgets each row it reverted. A `ValueError` means the row does not say what we wrote, so it is reported and kept. |
| Whole-service wipe | `_reset_service`, `ResetFromConfig.all` | Only runs when the request says `all=True`; the default is `False` and a test pins that. |
| Reset enumeration | `ratingrelay/db/store.py` (`records_for_target`) | Returns `(source_service, SyncedTrack)` because a reset undoes per record but forgets per source/target pair. |
| Plex filter | `ratingrelay/engine/services/plex.py` (`rated`) | `searchTracks(**{"userRating>>=": n})`, plus a local guard that drops unrated rows. If a server rejects the operator, it logs and filters locally rather than failing the run. |
| Typed outcomes end to end | `ratingrelay/engine/types.py` → `TrackOutcome` → `core/relay.py` `CallbackProgress` → `core/worker.py` `on_track` → `models/jobs.py` | `TrackCallback` gained a sixth argument, the outcome itself; the five existing ones keep their meaning so callers that ignore it still work. |
| Reason-based queue | `ratingrelay/routes/unmatched.py` | Selects on `reason in (no_match, ambiguous_match, no_recording_mbid, missing_metadata)` and returns `candidates`. |
| Schema | `ratingrelay/alembic/versions/d2e3f4a5b6c7_add_typed_outcomes.py` | Three nullable columns; backfills `outcome`/`reason` from the two v2 error strings so pre-existing history stays in the queue. |
| UI | `frontend/src/routes/unmatched/+page.svelte` | Shows the reason per row; also clears that file's long-standing eslint failures (SvelteSet, `resolve()`, keyed `#each`). |

Deliberate choices:

- **`_reset_recorded` reports a revert as an `ok`.** Reset is the inverse of a write, so
  the count means "tracks brought back to the pre-tool state".
- **A reverted row is labelled from its normalized key.** `SyncRecord` stores only the
  key, so a reset event reads `artist a - song`. Storing display names would mean new
  columns to keep in step with every write; the label is cosmetic and the docstring on
  `SyncedTrack.track` says so.
- **`write_to_*` no longer default `source_service` to `"unknown"`.** Same foot-gun S3
  removed from `run_tracks`: a guessed source writes records that can never be
  reconciled.

### S5 — Dry-run parity through the API (done)

S2 already made the pipeline dry-run aware (`run_tracks(dry_run=True)` returns the
same outcomes and skips only the commit), so the remaining work is at the edges: the
router must pass its flag through the worker to the engine rather than each writer
suppressing itself, and the skip events the SPA receives in a dry run must be the
engine's, not a re-derivation.

Exit: a test runs one fixture through `execute_relay` twice (dry, then real) and
asserts the per-service counts match and the writes differ.

What landed, and where:

| Concern | File | Notes |
| --- | --- | --- |
| Request flag | `ratingrelay/schemas/relay.py` (`RelayRequest.dry_run`) | Off by default, so an unmodified caller keeps writing. |
| Adapter | `ratingrelay/routes/relay.py` | Passes the flag to `execute_relay`; the job worker (`routes/jobs.py` → `core/worker.py`) already carried `Job.dry_run`. |
| Response | `RelayResponse.dry_run`, `TargetResult.skipped` | A dry run says so in the response instead of leaving the SPA to infer it from counts. |
| Status | `routes/relay.py` | Judges the run on whether the pipeline got through (`processed or skipped or errors`), because a dry run writes nothing and would otherwise look like a failure. |
| Skip events | `core/relay.py` `CallbackProgress` | Unchanged from S2: it forwards the engine outcome and only suppresses nothing-at-all runs. |

Exit test: `tests/test_relay_parity.py` runs the same fixture through
`execute_relay` dry-then-real and asserts identical per-service counts (including the
second, all-skipped pass) while the fake target's write log differs.

## Scope Boundaries

- No frontend changes in S1–S3; S4 touches the `/unmatched` payload and the Svelte
  types together (frontend rewrite ticket owns the UI work itself).
- No auth work here (API hardening ticket), no CLI (headless ticket), no job
  resumability changes.
- `models/models.py` (v1 `Loves`/`Hates`/`Reset`) stays untouched; deletion needs its
  own migration and is out of scope.

## Risks

- **Key format drift** breaks lookups for existing `sync_records`/`track_mapping` rows.
  Mitigated: S1 keeps `normalize` byte-identical to v2's and pins it with tests.
- **Undo writes the wrong thing** is the destructive failure mode. Mitigated: undo is
  driven only by recorded intents, is per-track, and is tested against a fake target
  that records calls.
- **Fuzzy ladder re-points a record.** Mitigated: rungs above exact never write when
  more than one candidate matches, and holding an existing record on a new ambiguous
  match is a test case.
