---
date: 2026-11-12
status: active
---

# v3 baseline: what v1/v2 taught us, and the workstreams

Decisions taken after reviewing v1 (`main`) and v2 (this branch's history).
Stack stays (FastAPI + SQLAlchemy/SQLite + SvelteKit); the layering changes so
headless mode is a first-class citizen rather than a flag on the web app.

## What v1 got right (the behaviour we are rebuilding)

- MusicBrainz **recording MBID** as track identity, resolved through MBZ and cached.
- **State reconciliation every run**: fetch the target's live loves/hates, diff against
  what we wrote, and converge. Idempotent; propagates rating *changes* and removals.
- **Only removes what it added.**
- Hates are ListenBrainz-only; Plex is the source of truth by default, `TWO_WAY` flips it.

## What v2 changed (keep the good, fix the rest)

Kept: real auth flows (Plex PIN OAuth, Last.fm md5 session, LBZ token validation);
credentials table with settings-override precedence; any-source→any-target; job model
with per-track events and SSE; manual `TrackMapping` overrides; test suite that does
not need live accounts; MBZ `tid:` bug fixed.

Regressions and gaps, in priority order:

1. **Idempotency became write-once.** `SyncRecord` is keyed on normalized artist+title
   per (source,target) and stores no intent or rating, so a changed rating never
   re-propagates and un-loving does not un-love. This is the single biggest loss
   against v1 — the relay stops being a relay.
2. **Reset is destructive**: it wipes every rating on the target, including ratings the
   tool never wrote.
3. **Whole-library fetch** (`section.searchTracks()` + Python filter) instead of asking
   Plex for `userRating>>=` only.
4. **Exact-normalized matching only** — v1's containment/variant fallback was dropped, so
   everything else piles into `/unmatched`.
5. `/unmatched` is derived by **string-matching error text**, so presentation is coupled
   to log wording.
6. **No authentication** on the API at all (`settings.secret` is declared and unused),
   while the API can queue a destructive reset.
7. **Frontend is a monolith**: `+page.svelte` 2200+ lines, `unmatched` 678, `jobs/[id]`
   601, hand-rolled CSS, shadcn-svelte + Tailwind installed but unused, pasted Inkscape SVG.
8. Worker/SSE are single-process with no mid-job progress persistence; cancel only works
   before the run starts; no retry/backoff.
9. Tests cover dry run and the jobs API only — nothing for fetch/match/write.
10. `.gitignore` had dropped `*.env` and never ignored `.config`; `Loves`/`Hates`/`Reset`
    tables are v1 leftovers imported only so migrations pick them up; `STRATEGY.md` and
    `docs/` were never committed.

## Target layering

```
engine/    fetch → match → reconcile → write. No FastAPI, no request schemas, no SSE.
cli/       headless entrypoint over engine/ (unattended/cron use).
api/       FastAPI adapters + job worker + SSE, over engine/.
frontend/  SvelteKit SPA; talks to api/ only.
```

The engine owns the behaviour contract in `AGENTS.md`; everything above it is plumbing.

## Workstreams

**A. Baseline and env** (done in this session): `.gitignore` fixed, `.config.example`,
`Justfile`, ports `:8100`/`:5173` configurable, `AGENTS.md` rewritten, skill added,
typecheck fixed (0 errors).

**B. Engine extraction + hardening**: pull `core/relay.py` into `engine/`; re-key
`SyncRecord` on intent (source,target,mbid,rating) with reconciliation (1); make dry run
share the real pipeline end-to-end (contract §4); server-side Plex filtering (3);
matching ladder exact→variant→containment→MBID with candidates attached, no silent
re-pointing (4, contract §6); typed outcomes replacing `_NO_MATCH_ERRORS` (5); narrow,
scoped reset (2).

**C. Headless mode**: `cli/` that runs a relay from a config file or flags, prints a
summary and a machine-readable report, exit codes for partial failure, no server needed.
Must consume exactly the same engine path as the web app.

**D. API hardening**: session/secret auth (6), resumable jobs and mid-run cancel (8),
progress persisted rather than in-memory, retry with backoff.

**E. UI rewrite**: break the monolithic pages into `$lib/components/`, use the installed
shadcn-svelte primitives and Tailwind tokens, replace the Inkscape SVG with a plain
asset, clear the outstanding svelte-check warnings and eslint errors. Candidates from
`docs/ideation/2026-05-10-user-control-surface-ideation.md`: threshold histogram with
live preview, one-click mapping proposals, per-job diff, completion notifications.

## Known failing checks at baseline

- `pnpm check` (types): **clean** — 0 errors, 24 warnings (`state_referenced_locally`,
  unused CSS selectors) that the UI rewrite clears.
- `pnpm lint` (prettier + eslint): prettier clean; **18 eslint errors** —
  `require-each-key` ×6, `no-navigation-without-resolve` ×3, unused vars ×3 (dead code
  from the v2→v3 dry-run refactor), `prefer-svelte-reactivity` ×2 (plain `Set` used as
  reactive state), `no-dupe-else-if-blocks` ×1. Cleared by the UI rewrite; do not silence
  the rules.
- `just lint` (pylint): 9.13/10 with `fail-under = 9.0`; ratchet upward as the engine is
  extracted.
