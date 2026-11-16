# 012 — Rating box and editable thresholds (RR-14)

## Problem

The relay page says "From"/"To", and the From box carries the love/hate toggle plus
a line telling the user to edit `HATE_THRESHOLD` in the server settings. Changing a
threshold means editing `.env` and restarting the container.

## Decision

### Wording and layout

- "From" → "Source", "To" → "Destination" (node labels, eyebrow, the "as From
  reads" hint and any other copy that names them).
- The Source box holds only the service picker (and, for Reset, its service
  choice — reset has no rating, so it stays there).
- A new **Rating** box sits between Source and Destination
  (`$lib/components/flow/RatingNode.svelte`), hidden for Reset:
  - The love/hate toggle, bound to the selected source's `feedbackType`
    (Plex, ListenBrainz). Last.fm shows "loves — Last.fm has no hates".
  - The threshold for the current direction, when Plex is the source or an
    allocated destination: five Plex-style stars (`$lib/components/StarRating.svelte`),
    half-star steps (1–10), hover previews, a click saves. Ratings that would
    overlap the other threshold can't be picked. No explanatory copy; a pinned
    threshold shows read-only stars and the setting name.
- Destination gets the same `FlowNode` box as Source/Rating/Trigger; the target
  cards keep their dashed/dimmed off state inside it.
- Destination cards stop naming the setting; a Plex destination says
  "Rate N stars", ListenBrainz "Love/Hate every track".

### Where thresholds live

- New single-row table `rating_thresholds` (`love`, `hate`, `updated_at`),
  Alembic migration, read/written through `ratingrelay/db/thresholds.py`.
- Precedence keeps the dev-override pattern: a threshold **explicitly** set in env
  or `.config` (`model_fields_set`) wins over the DB row; the DB row wins over the
  default (8/2). The API reports which thresholds are locked by settings, and the UI
  shows a locked one read-only with the setting name.
- `compose.yaml` stops passing `LOVE_THRESHOLD: ${…:-8}` — that would mark both as
  explicitly set on every Docker install and lock the UI. `.env.example` and
  `.config.example` keep them commented, as overrides. `env_ignore_empty` makes
  compose's empty pass-through count as unset.
- `factory.plex_threshold(relay)` becomes `async plex_threshold(db, relay)`; it is
  only called inside `source_for_service` / `target_for_service`, which already have
  a session, so the CLI and the worker read the same value with no new wiring.
  The engine stays parameterized.

### API

- `GET /api/relay/thresholds` → `{love_threshold, hate_threshold, love_locked, hate_locked}`.
- `PUT /api/relay/thresholds` → validates 1–10, whole numbers (half stars), hate <
  love against the *effective* pair; 409 if the field is locked by settings.

## Sync semantics

Unchanged: the relay identity is the direction, not the number, so moving a
threshold is a withdrawal/re-write inside one relay, exactly as an env change was.
A move that would undo most of a target is still refused by `is_mass_undo` per run.

## Tests

- DB value used by source and target when settings don't set it; explicit setting
  beats the DB row; default with neither.
- PUT: validation (range, overlap against the effective other side), 409 on locked,
  persists; GET reports locks.
- Migration upgrade on an existing DB.
- `just check` for the new component and wording.
