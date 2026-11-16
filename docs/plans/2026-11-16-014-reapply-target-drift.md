# 014 — Re-applying a write the target dropped

Closes the open question in [013](2026-11-16-013-integration-tests.md) ("Divergence:
target drift is not re-applied") and RR-10 / github#21, whose outcome A was "the
track is re-loved on the target" without a way to ask for it.

## Problem

A record says "we loved this on Last.fm". Next run the record's intent still
matches the source, so `pipeline._settled` answers `SKIPPED / already_synced`
without ever comparing the target's current state to what we wrote. If a person
un-loved the track on Last.fm since, the relay never notices: the target keeps
the edit and the source keeps the love, and the two stay apart for good.

Plan 010 chose that deliberately — policing a write already made means rewriting
what someone changed by hand on three services — and 013 pinned it in two tests.
What was missing is a way to say "on this relay, the target is not authoritative".

## Decision

A per-relay option, `reapply_target_drift`, default **off** (today's behaviour).

When it is on, a run re-writes a record whose intent is unchanged **and whose
write has vanished from the target**. Not more:

| Target state now | Run with the option on |
| --- | --- |
| Still holds what we wrote | `skipped / already_synced` (unchanged) |
| Holds nothing (un-loved on LFM/LBZ, rating cleared on Plex) | **written again**, `written / target_drift` |
| Holds something else (love → hate on LBZ, Plex 8 re-rated to 6) | `skipped / already_synced` — a replacement is a person's edit, and stays |

The third row is the line this draws. `undo` already refuses to clear a Plex copy
somebody re-rated after us (contract §3, "only undo our own writes"); re-applying
over a replacement is the same act in the other direction, so it is refused the
same way. A *removal* carries no value of its own, so re-writing it overrides
nobody.

Two consequences worth naming:

- The write-once promise is unchanged. A run still writes a track only when the
  source's intent changed, a mapping moved it, or — with this option — the target
  lost it. No new network calls: planning already reads the target's state for
  every track in the fetch set (`existing.holds`/`prior`), which is why this is
  cheap where 013 feared it was not.
- The record's `prior` is not replaced. A drift re-write keeps the original
  prior, so withdrawing the source love afterwards still restores what the user
  had before us, not the state the drift left behind.

## Shape

- `engine/plan.py`: `Existing.occupied` — whether the target holds *anything* for
  the track (a rating, a score, a love). `not holds and not occupied` is the
  "write vanished" case. Each target computes it from state it already reads, and
  it defaults to `None` ("the target did not say") which is read as occupied: a
  future target that forgets it can never cause a write on every run.
- `engine/types.py`: `Reason.TARGET_DRIFT`.
- `engine/pipeline.py`: `_drifted()` and a `reapply_drift` keyword on
  `run`/`run_tracks`, threaded into `_settled`. The dry-run path needs no change:
  it takes the same decision and reports `written / target_drift` without writing.
- `core/relay.py`, `core/worker.py`, `routes/relay.py`: pass the flag through.
- `models/jobs.py`: a `reapply_target_drift` column (migration), carried to a
  recurring job's successor like `dry_run` — unlike `allow_mass_undo`, this is a
  policy, not a one-run override.
- `cli/config.py`: `reapply_target_drift` key in `relays.toml`, plus a
  `--reapply-target-drift` flag for an ad-hoc run (the flag turns it on for every
  relay in the file, like `--dry-run`).
- Frontend: a checkbox in the trigger node, next to the dry-run toggle.

## Tests

- Unit (`tests/test_engine_drift.py`): drifted + option off → skipped; drifted +
  option on → written once, record kept, prior kept; a replacement is never
  re-applied (ListenBrainz love→hate, Plex re-rate); a Plex copy rated `0` counts
  as unrated; a legacy record with no intent heals as an ordinary rewrite, never
  as drift; a second run is a no-op again; dry run reports the same decision.
- Integration, per direction: Plex→LFM un-love, Plex→LBZ un-love, LBZ→Plex rating
  cleared (each re-applied with the option on), Plex→LFM replacement and
  LBZ love→hate left alone.
- API/CLI: the option round-trips through `POST /api/relay/jobs`, may repeat on a
  recurring relay, and parses from a `relays.toml` table (`true`/`false` only).

## Out of scope

- Propagating a *target* edit back to the source (outcome B in github#21). That
  makes the target a source and needs the relay to read two directions in one
  run; it earns its own plan if it is ever wanted.
- Any change to the default. A relay that opts in is the only one whose target
  stops being authoritative.
