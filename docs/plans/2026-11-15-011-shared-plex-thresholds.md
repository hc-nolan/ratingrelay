# 011 — Plex ratings follow shared love/hate thresholds

## Problem

Each relay carried its own Plex numbers: a source had `rating_threshold` and
`comparison`, a target had `plex_rating`. They only shared a default. A two-way
setup could read loves at 4 stars and write them back at 5, or a user could change
one relay's cutoff and not the other's, so what Plex counted as a love depended on
which relay you looked at.

## Decision

- Two settings, `love_threshold` (default 8) and `hate_threshold` (default 2), env
  `LOVE_THRESHOLD` / `HATE_THRESHOLD` (`RATINGRELAY_*` in `.env`). They are on the
  Plex 1–10 scale and the hate one must be below the love one.
- A Plex source reads loves as rated ≥ love and hates as rated ≤ hate. A Plex target
  writes the threshold for the relay's direction.
- The direction of a Plex relay is `feedback_type` (love/hate), like ListenBrainz.
  `relay_identity("plex", feedback_type=…)` keys the sync records.
- Removed: per-relay `rating_threshold`, `comparison`, `plex_rating`, the matching
  CLI flags, `PlexToConfig.rating` and the UI selects. The UI shows the thresholds
  read-only (`GET /api/relay/thresholds`).
- `engine/` stays parameterized; only `core/factory.py` reads settings.
  `settings.py` holds literal defaults because importing `engine.types` from it is
  a cycle (the engine imports `settings.logger`).

## Legacy data

- Stored jobs: `PlexFromConfig` maps `comparison: lte` to `feedback_type: hate`
  and drops `rating_threshold`; `routes/jobs.py` returns `source_config` through
  the same model so the UI sees the new shape.
- `relays.toml`: the removed keys fail with an "is gone (…)" error naming the
  replacement, rather than the generic unknown-key error.

## Tests

`tests/test_plex_thresholds.py` (source reads by direction, target holds by
direction, relay identity, overlap and range validation, defaults, endpoint);
CLI tests for the removed keys and flags; a jobs-API test for an older stored
comparison.

## Known gap (not in scope)

A Plex *hate* relay to Last.fm loves the hated tracks, because Last.fm has no hate.
It should be refused at config time.
