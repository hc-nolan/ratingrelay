---
name: RatingRelay
last_updated: 2026-05-10
---

# RatingRelay Strategy

## Target problem

Plex users who also scrobble to Last.fm and/or ListenBrainz must rate tracks on each service independently — the services use incompatible data models (star ratings vs. loved/hated binary), so every rating requires manual double-entry. Losing any one account also means losing that curation history unless it's been replicated elsewhere.

## Our approach

The user stays in control of every variable — which services, which direction, what threshold counts as "loved." RatingRelay carries the user's intent faithfully without imposing its own definition of what's worth syncing.

## Who it's for

**Primary:** Plex users who also scrobble to Last.fm and/or ListenBrainz — they're hiring RatingRelay to automatically propagate their Plex track ratings so they rate once and it flows everywhere.

## Key metrics

- **Track count parity** — after a sync, the number of loved/hated tracks on the target service matches what was fetched from the source (minus already-synced tracks). Visible via job stats (`tracks_ok`, `tracks_err`, `tracks_skipped`).
- **Mapping correctness** — the right tracks are matched with no false positives or erroneous writes. Surfaced via the unmatched tracks list and manual mappings UI.

## Tracks

### Sync reliability

Matching logic, edge cases, incremental sync, and ensuring no erroneous writes or data loss.

_Why it serves the approach:_ The user is trusting RatingRelay to be a faithful relay — correctness is the entire value proposition.

### Service integrations

Depth and correctness of Plex, Last.fm, and ListenBrainz support; potential new services.

_Why it serves the approach:_ User control is meaningless without reliable support for the services they actually use.

### User control surface

Relay configuration, thresholds, manual mappings, and job monitoring.

_Why it serves the approach:_ This is where the user's intent is expressed — if it's confusing or limited, the "user in control" principle breaks down.
