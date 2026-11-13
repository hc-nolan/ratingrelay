# RatingRelay — agent notes

Self-hosted relay that keeps **track ratings** in sync between Plex, Last.fm and
ListenBrainz. One user, one library, no multi-tenancy. It runs standalone: nothing
here may assume another service (including Hyperfix) exists.

`STRATEGY.md` holds the product framing (problem, personas, metrics, workstreams).
Read it before proposing features; read this file before touching code.

## Dev environment

Host-based, no containers. `uv` for Python, `pnpm` for the frontend, `just` to run things.

```shell
just install    # uv sync + pnpm install
just dev        # API on :8100 + Vite on :5173, Ctrl-C stops both
just api        # API only   (uv run app.py, autoreload)
just web        # Vite only  (proxies /api to :8100)
just test       # pytest
just lint       # pylint ratingrelay
just check      # svelte-check + prettier + eslint
just migrate    # alembic upgrade head
just build      # frontend bundle that FastAPI serves
```

- Ports are deliberate: `:8000`/`:3000` belong to other projects on this machine.
  The API port lives in `.config` (`port`, plus a matching `base_url` — Plex OAuth
  redirects to `base_url`, so it must be browser-reachable). The Vite dev server
  proxies `/api` to `BACKEND_URL` (default `http://localhost:8100`).
- Both bind every interface, so the app is reachable from the LAN at
  `http://<host>:5173` (API: `:8100`). Vite only answers Host headers it knows: IP
  addresses always pass, `.local` mDNS names are allowed by default, and anything
  else goes in `DEV_ALLOWED_HOSTS` (comma separated, e.g. `ms01.lan`). The proxy
  keeps the browser's Host header on the hop so the Plex OAuth callback comes back
  to the origin the browser is actually using.
- `.config` is gitignored and holds credentials. `.config.example` documents every
  key. Environment variables override `.config`; both override database-stored
  credentials (that hierarchy is the dev-override pattern — keep it).
- `just check` runs svelte-check + prettier + eslint. Typecheck is clean; eslint still has
  known failures listed in `docs/plans/2026-11-12-v3-baseline-and-workstreams.md`, cleared
  by the UI rewrite — don't silence the rules to get green. `just lint` has a `fail-under`
  in `pyproject.toml`; ratchet it up, never down.

## Layout (current)

```
app.py                 uvicorn entrypoint, reads host/port from settings
ratingrelay/
  main.py              FastAPI app, lifespan, routers, SPA fallback
  settings.py          pydantic-settings; get_settings() + module logger
  core/                relay.py (pipeline), worker.py (job queue), thread.py (run_sync), plex_auth.py
  db/                  engine, session factory, get_async_session(), credential helpers
  models/              SQLModel tables: services, jobs, sync, mappings
  routes/              one module per service + jobs/unmatched/relay
  schemas/             pydantic request/response models, one file per service
frontend/              SvelteKit 5 SPA (see frontend/AGENTS.md)
tests/                 pytest, in-memory SQLite, fake service clients
```

## Layout (v3 target, being migrated toward)

Headless mode is a deliverable, so the relay must not be reachable only through
the web app. Migrate to four layers, in this order of dependency:

```
engine/     pure relay logic: fetch → match → reconcile → write. No FastAPI, no
            request/response schemas, no SSE, no sessions from a web request.
            Takes service clients and a store; returns structured results.
cli/        headless entrypoint (`ratingrelay relay --source plex --target lastfm`).
            Same engine, no server. This is the thing that must work unattended.
api/        FastAPI: thin adapters over engine/ for the UI plus the job worker.
frontend/   SvelteKit SPA. Talks to api/ only, never to Plex/Last.fm/LBZ directly.
```

Layering rules: `engine/` may not import `api/` or FastAPI; `api/` may not contain
relay logic beyond marshalling; the frontend may not encode sync semantics.

## Behaviour contract (do not regress these)

These are the point of the tool. v1 got them right; keep them that way.

1. **Identity is the MusicBrainz recording MBID.** Plex track MBIDs are resolved
   through MusicBrainz and cached. A track without an MBID is unmatched, not guessed.
2. **Reconcile every run, don't append.** Each run compares the source's current
   loves/hates to what we previously wrote to each target, and converges the target.
   A rating that changed re-propagates; a love that was withdrawn gets un-loved.
3. **Only undo our own writes.** Never clear a target rating this tool did not create.
   Reset is an explicit, narrow action — never a whole-service wipe by default.
4. **Dry run must be the real pipeline.** Fetch, match and reconcile run as usual;
   only the write calls are suppressed. Dry-run output is the authority on what a
   real run would do, so divergence between them is a bug, not a caveat.
5. **Filter server-side.** Ask Plex for rated tracks (`userRating>>=`) instead of
   fetching the library and filtering in Python.
6. **Never re-point an existing record on a fuzzy match.** Fuzzy/exact matching may
   propose, but ambiguous candidates go to the unmatched queue with the candidates
   attached, and a human resolves them.
7. **Idempotent and resumable.** Re-running a completed job is a no-op. Interrupted
   jobs resume without duplicating writes.

## Conventions

- **Blocking clients**: plexapi, pylast and liblistenbrainz are synchronous. Wrap
  them: `await run_sync(lambda: client.call())` from `ratingrelay.core.thread`.
- **Sessions**: inject `Depends(get_async_session)`. Never build sessions ad hoc.
- **Credentials**: one `ServiceCredential` row per service, read through
  `ratingrelay.db.credentials`. Settings take precedence over the DB row.
- **Schemas**: request/response models live in `ratingrelay/schemas/`, one file per
  service. Never reuse SQLModel table classes as API schemas.
- **Migrations**: Alembic, always autogenerate then review. Existing databases must
  keep working — add columns and tables freely; rename/drop only with a data
  migration in the same revision.
- **Logging**: module-level `logger` from `ratingrelay.settings`. No `basicConfig`
  in library code. Errors surfaced to the UI must come from typed results, not from
  string-matching log text.
- **Timestamps** are UTC in storage; presentation is local via `settings.timezone`.

## Testing

- `tests/` runs against in-memory SQLite and **fake** service clients. Tests must
  never touch a real Plex server, Last.fm account or ListenBrainz token.
- The engine is the part worth testing hard: fetch, match, reconcile, write, dry run.
  If a change alters sync semantics, it needs a test that fails without it.
- `pnpm check` for the frontend types, `pnpm lint` for lint. There is no browser test suite
  yet; if you add one, keep it runnable from `just test`.

## Frontend

See `frontend/AGENTS.md`. Two hard rules: reach for the shadcn-svelte primitives that
are already installed instead of hand-rolled markup and CSS, and keep pages small —
extract components into `$lib/components/` and types into `$lib/types/` rather than
growing a single-page file.

## Working agreement

- Tickets live in Hyperfix (project `RR`) but the product must stay independent of it.
- Conventional commits, scoped: `feat(engine): …`, `fix(frontend): …`.
- `pnpm check` + `just test` before pushing; no direct pushes to `main` — branch and PR.
- Never commit `.config`, `*.env`, `*.db` or the frontend build output.
- Skill files: `.pi/skills/ratingrelay/SKILL.md` (also symlinked for Claude Code).
