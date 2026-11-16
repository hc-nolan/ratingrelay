# 010 — Don't adopt what the target already holds; undo restores it

## Problem

Behaviour contract §3 says the relay only undoes its own writes. It didn't keep that
promise, because no target looked at its own state before planning a write:

- **Last.fm / ListenBrainz.** A track the user had already loved by hand was "loved"
  again (an idempotent no-op), recorded as ours, and un-loved when the source rating
  was withdrawn. v1 had the same flaw.
- **Plex as a target** (v1's `TWO_WAY`, now a `lastfm|listenbrainz → plex` relay).
  The user's existing rating was overwritten with the relay's rating (an 8 became a 10),
  and a withdrawal cleared it to unrated instead of putting the 8 back. This is a
  regression: v1 skipped tracks already at or above the love threshold and never
  cleared anything in Plex.

## Decisions

1. **"Already on the target" is a skip, and nothing gets recorded.** A target's plan
   says whether the target already holds the intent (`WritePlan.existing.holds`). If no
   record exists, the pipeline reports `SKIPPED / already_on_target` and records
   nothing, so a later withdrawal has nothing to undo. If a record exists, the state
   is ours: the plan goes through the usual intent comparison, so legacy rows still
   heal and changed intents still re-write. Dry run takes the same branch (it is
   decided before the dry-run shortcut).
2. **Every write records what it replaced** (`WritePlan.existing.prior`, stored as `SyncedTrack.prior`, a new nullable
   `sync_record.prior` column, opaque per target like `target_key`). Undo restores
   `prior` instead of clearing. A re-write of a record we already own keeps the
   *original* prior, not our own earlier write.
   - Plex: `{"<ratingKey>": <rating or null>}` for every matched copy.
   - ListenBrainz: the score the recording held before (`-1` when we loved over a
     hate). Undo submits that score; with no prior it submits `0` as before.
   - Last.fm: nothing. A love is only written when the track wasn't loved, so the
     prior state is always "not loved".
3. **Plex holds-or-better is direction-aware.** A love relay wants *at least* the
   rating and a hate relay wants *at most* it (and above 0). A copy already on the
   right side is left untouched, so a 4-star track is never lowered or raised by a
   love relay that writes 8. The plan writes only the copies that don't already
   hold it. `target_key` still lists every matched copy, so identity and
   re-pointing are unchanged. The direction is the relay identity
   (`relay_identity`), passed through `target_for_service(relay=…)`.
4. **Plex undo leaves a copy alone if its rating no longer matches what we wrote.**
   The user changed it after us, so it's theirs. Missing copies are still a no-op.

## Out of scope

- Importing v1's tables (v3 is a fresh start).
- Reporting a per-copy "left alone" outcome on undo. The track is reported as
  reverted, and the log names the copy that was skipped.

## Tests

The probes that showed the bug become regression tests in
`tests/test_engine_ownership.py`: manual Last.fm love not adopted, ListenBrainz
same-score not adopted and opposite score restored, Plex rating kept or restored,
a user-changed Plex rating left alone on undo, direction-aware holds-or-better,
dry-run parity, and a record we own still heals.

## Notes

- The Plex rating a target writes is the love threshold, not 10. With 10, a two-way
  setup raised every 4-star track loved on Last.fm to 5 stars; now a track at the
  threshold already holds the love and is left alone. Superseded in detail by
  `2026-11-15-011-shared-plex-thresholds.md`: the threshold is the `LOVE_THRESHOLD`
  setting, not a per-relay default.
- A resumed job journals an `already_on_target` skip again, as it does `no_match`;
  only `already_synced` is treated as an echo of the first attempt.
