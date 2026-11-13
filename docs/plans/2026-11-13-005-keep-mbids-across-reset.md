# RR-6 — keep the resolved recording MBID across a reset

## The problem, measured

A cold `plex → listenbrainz` run takes **~16 minutes** for 361 tracks. A warm
rerun takes **35 seconds**. The difference is entirely MusicBrainz lookups: the
resolver is rate-limited to roughly one request a second, and every Plex track
starts with no recording MBID.

The only place a resolved MBID is stored today is the `SyncRecord` for the write
that used it — `syncrecord.recording_mbid`. Reset deletes those rows
(`forget_target`), because they are "what we wrote to that service". So the
sequence

```
sync plex -> listenbrainz   (16 min, MBIDs now known)
reset listenbrainz          (records deleted, MBIDs go with them)
sync plex -> listenbrainz   (16 min again)
```

throws away the most expensive thing the tool ever computes, in service of
deleting a *rating*.

## Why not "keep the SyncRecord on reset"

A `SyncRecord` is a claim about a **target**: this track, on this service, holds
this intent, and here is the key to undo it. Reset exists to retract that claim.
An MBID is not part of the claim — it is what the track *is*. The two lifetimes
are different, so they need different homes:

| Question | Belongs to | Lifetime |
| --- | --- | --- |
| "What did we write to Last.fm for this track?" | `SyncRecord` | until reset or withdrawal |
| "Which MB recording is this track?" | the track | as long as the track exists |

## Design

New table `trackmbid`, one row per normalized track key:

```
trackmbid
  id              TEXT PK (uuid4)
  normalized_key  TEXT NOT NULL UNIQUE   -- track_key(artist, title)
  artist          TEXT NOT NULL          -- as resolved, for humans and logs
  title           TEXT NOT NULL
  recording_mbid  TEXT NOT NULL
  resolved_at     DATETIME NOT NULL
```

No foreign key to `syncrecord`, no target column: the answer does not depend on
which service asked, and a `plex → lastfm` run tonight should warm a
`plex → listenbrainz` run tomorrow.

### Where it is consulted — and where it must not be

The resolution ladder in `ListenBrainzTarget.resolution` stays in its current
order, with the cache inserted in exactly one place:

1. `track.recording_mbid` — the source said so (ListenBrainz feedback carries
   the MBID already). Authoritative.
2. the user's mapping — a human decided. **Must stay above the cache**: a
   mapping is a correction, and a cache that outranked it would make an override
   silently stop working once the old answer had been written once.
3. **the cache** — we resolved this before.
4. MusicBrainz — the network, the slow path.

### When it is filled

In the target's `commit()`, immediately after the write succeeds:

- a dry run never calls `commit`, so a dry run writes nothing — including the
  cache, which keeps `dry_run` meaning exactly "no side effects";
- a failed write does not populate it, so a broken run cannot poison the cache;
- a *skipped* (`already synced`) outcome does not populate it either, which is
  fine: that row already had an MBID on the `SyncRecord`.

A dry run reads the cache, so it reports the same plans a real run would. The
only asymmetry is what a run leaves behind for the *next* run, and that is the
promise a dry run has never made.

## Migration and backfill

Revision after `d2e3f4a5b6c7`. The upgrade creates the table, then backfills from
the rows that still have an MBID:

```sql
INSERT INTO trackmbid (id, normalized_key, artist, title, recording_mbid, resolved_at)
SELECT ..., s.normalized_key, s.normalized_key, '', s.recording_mbid, s.synced_at
  FROM syncrecord s
 WHERE s.recording_mbid IS NOT NULL AND s.recording_mbid <> ''
 GROUP BY s.normalized_key
```

The `syncrecord` table stores only the normalized key, so the backfill cannot
recover the display artist and title. It stores the key in `artist` and an empty
`title`, which is what `SyncedTrack.track` already does for display. That is
honest: these rows are a cache keyed by the same string the lookups use, and the
next real run overwrites them with proper names if it ever needs to.

Dedupe is by `normalized_key`: the live database has the same track recorded for
both Last.fm and ListenBrainz.

## Tests

The engine test that matters is the one that fails today:

- **reset then rerun makes zero lookups**: run `plex → lbz` against a counting
  resolver, reset (drop the sync records), run again, assert
  `resolver.calls == 0` and that the outcome is still a write.
- The ladder order: with a mapping *and* a stale cache entry, the mapping's
  MBID wins.
- A dry run does not populate the cache; a failed write does not populate it.
- DB test: `put` then `get` round-trips; `put` for an existing key replaces the
  MBID rather than raising.

## What does not change

- The `SyncRecord` shape, reset semantics, the reconcile rule, and undoability.
  Reset still retracts the write; it just no longer takes the track's identity
  with it.
- `target_key` on ListenBrainz writes stays the recording MBID.
- No API or schema change: the cache is internal to the target.

## Risks

- **A stale cache entry for a track that genuinely changed MBID** (a remaster
  filed as a new recording). The MusicBrainz rung would have found something
  else. Accepted: the entry was written by a lookup that matched, tracks that
  change identity are rare, and the failure mode is a write to the recording
  that was correct when it was learned rather than an unmatched track.
- **Cache poisoning via a bad match** is bounded by only writing after a
  successful submit, and a reseed costs one cold run.

## What landed

- `ratingrelay/models/mbids.py` — the `trackmbid` table, keyed by the same
  `track_key` string the resolver is asked about.
- `ratingrelay/alembic/versions/f8a9b0c1d2e3_add_track_mbid_cache.py` — creates it
  and backfills from `syncrecord`, deduped by key.
- `ratingrelay/engine/store.py` — the `MbidCache` port, plus `MemoryMbidCache` and
  `NullMbidCache` (the default: no cache, so the ladder still works without one).
- `ratingrelay/engine/services/listenbrainz.py` — `ListenBrainzTarget` takes an
  optional `cache`; `resolution()` consults it below the mapping, and `commit()`
  fills it after the submit succeeds.
- `ratingrelay/db/store.py` — `SqlMbidCache`, an upsert on the normalized key.
- `ratingrelay/core/adapters.py` — `mbid_cache(db)` and the wiring in `target_for`.
- `tests/test_engine_mbid_cache.py` — 8 tests, including the one that fails
  without the feature and its inverse (no cache ⇒ a reset is cold).

### The measurement

Run against the real Plex library and the real MusicBrainz resolver, with a fake
ListenBrainz client (nothing written to an account) and a scratch copy of the
live database, 25 tracks:

| Run | Wall time | MusicBrainz lookups | Writes |
| --- | --- | --- | --- |
| cold | 27.8 s | 24 | 25 |
| after `forget_target("listenbrainz")` | **0.0 s** | **0** | 25 |

So the reset is now free, and the run after it still writes — the cache changes
what a run *knows*, not what it does.

### Two honest notes

- **The live database backfills nothing.** Its 216 `syncrecord` rows predate v3
  and carry no `recording_mbid`, so `trackmbid` starts empty there. The first
  real ListenBrainz run after the upgrade is still cold (~1 s per track); every
  run after it is warm. The backfill path itself was verified on a copy: two
  keys from three rows, deduplicated across targets, the NULL-MBID row skipped.
- **The ladder order is load-bearing.** A cache entry must never outrank a user
  mapping, or a correction would silently stop working the once the previous
  answer had been written. `test_a_mapping_outranks_a_cached_mbid` pins it.
