# RatingRelay — agent notes

Self-hosted relay that keeps **track ratings** in sync between Plex, Last.fm and
ListenBrainz. One user, one library, no multi-tenancy. It runs standalone: nothing
here may assume another service (including Hyperfix) exists.

`STRATEGY.md` holds the product framing (problem, personas, metrics, workstreams).
Read it before proposing features; read this file before touching code.

**Last.fm is frozen (from 2026-12-01).** Last.fm API keys now require a paid Pro
subscription, and the maintainer does not have one, so the integration can no longer
be tested against the real service. Keep the existing Last.fm code and its fake-client
tests green, and keep it working through shared refactors, but do not add Last.fm
features, do not "fix" Last.fm behaviour that can't be reproduced, and do not remove
it without being asked. New capabilities target Plex and ListenBrainz.

## Dev environment

Host-based, no containers. `uv` for Python, `pnpm` for the frontend, `just` to run things.
Users deploy with Docker (`Dockerfile`, `compose.yaml`, `.env.example`), so the user-facing
README assumes Docker; a new setting must work as an environment variable, and anything
the app keeps must live under `/data` in the container.

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
- `just check` runs svelte-check + prettier + eslint. All three are clean and CI
  (`.github/workflows/ci.yml`) runs them with `just test` and `just lint` — keep them
  green by fixing code, never by silencing a rule. `just lint` has a `fail-under`
  in `pyproject.toml`; ratchet it up, never down.

## Layout (current)

```
app.py                 uvicorn entrypoint, reads host/port from settings
ratingrelay/
  main.py              FastAPI app, lifespan, routers, SPA fallback
  settings.py          pydantic-settings; get_settings() + module logger
  __main__.py          `python -m ratingrelay` → the CLI
  engine/              pure relay logic: fetch → match → reconcile → write
  cli/                 headless entrypoint: config.py, report.py, main.py
  core/                relay.py (API pipeline), worker.py (job queue), factory.py
                       (service construction both entry points share), thread.py
                       (run_sync), plex_auth.py, session.py (login + guard)
  db/                  engine, session factory, get_async_session(), credential helpers
  models/              SQLModel tables: services, jobs, sync, mappings, mbids
  routes/              one module per service + jobs/unmatched/relay/auth
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
`cli/` may import `engine/`, `db/` and the service construction in `core/factory.py`,
but **not** `fastapi`, `starlette`, `ratingrelay.routes` or `ratingrelay.schemas` —
a request-config pydantic model in the CLI would make the headless path depend on
the web path's wire format. Two AST tests enforce this (`tests/test_engine_matching.py`,
`tests/test_cli.py`).

Headless usage, exit codes and the `relays.toml` shape are documented in
`README.md`; `relays.toml.example` is the template. The CLI opens the SQLite file
directly, so nothing on that path needs the API, the SPA bundle or a browser.

## Behaviour contract (do not regress these)

These are the point of the tool. v1 got them right; keep them that way.

1. **Identity is the MusicBrainz recording MBID.** Plex track MBIDs are resolved
   through MusicBrainz and cached. A track without an MBID is unmatched, not guessed.
2. **Reconcile every run, don't append.** Each run compares the source's current
   loves/hates to what we previously wrote to each target, and converges the target.
   A rating that changed re-propagates; a love that was withdrawn gets un-loved.
   A target's *own* edits are not policed: an unchanged intent is `already_synced`,
   so a track someone un-loved on the target stays that way. A relay that names
   `reapply_target_drift` opts out of that, and only for a write the target has
   *dropped* — a replacement the user chose is never overwritten (same rule as
   undo, below).
3. **Only undo our own writes.** Never clear a target rating this tool did not create.
   Reset is an explicit, narrow action — never a whole-service wipe by default.
   Sync records are keyed by source, target **and relay** (the direction the source
   reads, `relay_identity` in `core/factory.py`), so a love relay and a hate relay
   on one pair never reconcile against each other. A reconcile that would undo most
   of a target (`pipeline.is_mass_undo`) is refused unless that one run allows it
   (`--allow-mass-undo`, `allow_mass_undo` on a non-recurring job); never make the
   override persistent.
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
8. **A service is not an account.** An account is addressed by a **ref**
   (`engine/refs.py`): the bare kind for the account that already exists
   (`lastfm`), `kind:name` for a second one (`lastfm:old`). Very little here is
   negotiable:
   - The bare kind is the first account's ref **permanently**. That is why adding a
     second account moves no history, and why a ref is produced by `account_ref`
     and never assembled at a call site — a wrong ref is a silent cross-account
     reconcile, and the mass-undo guard would be the only thing between that and a
     wipe.
   - `lastfm` → `lastfm:new` is a **migration** between two accounts and is legal;
     `lastfm` → `lastfm` is one account relaying into itself and is refused
     (`factory.pairing_refusal`, judged on refs). Capabilities that belong to the
     service are still judged on the *kind*: a hate relay cannot reach any Last.fm
     account.
   - The bare kind is a **slot** — disconnecting and reconnecting refills it, and
     the relay keeps reconciling against the history written under it (that is what
     switching Plex servers already meant). A **named** ref is an identity: once
     removed, its ref is never reissued, so a different account cannot inherit its
     records. Removal is always a soft delete that keeps no secret material.
   - Settings (`PLEX_TOKEN`, `LASTFM_TOKEN`, …) describe **one** account: the one
     whose ref is the bare kind. An environment variable cannot name a second one.
   - Mappings resolve ref → kind → `all`, so a mapping written before a second
     account existed still applies to it.

## Conventions

- **Blocking clients**: plexapi, pylast and liblistenbrainz are synchronous. Wrap
  them: `await run_sync(lambda: client.call())` from `ratingrelay.core.thread`.
- **Sessions**: inject `Depends(get_async_session)`. Never build sessions ad hoc.
  The CLI is the one exception: it opens `session_maker` itself, because there is
  no request to depend on.
- **Service construction lives in `core/factory.py`**, never in a route or the CLI.
  `source_for_service` / `target_for_service` / `stores` / `mbid_cache` are the only
  place a client is paired with a store and the MBID cache, so the headless path
  cannot drift from the web path on which store a target reconciles against.
- **The CLI is quiet by default and loud on failure**: cron mails whatever it
  prints, so a clean run prints a short block, `--verbose` adds per-track lines on
  stderr, `--json` prints one document. Never exit 0 on a partial run — 0/1/2 are
  the only signal a scheduler reads.
- **Credentials**: one `ServiceAccount` row per connected account, addressed by
  its **ref** (`lastfm`, `lastfm:old` — see `engine/refs.py`) and read through
  `ratingrelay.db.credentials`. Settings take precedence over the DB row, and only
  for the account whose ref is the bare kind: an env var cannot name a second
  account. The ref is what sync records, mappings and job configs key on, so it is
  immutable and a removed account is soft-deleted rather than losing its row.
  A service configured **only** from the environment has no row at all (a Docker
  deploy fed by Key Vault), so `/api/services` synthesizes its entry from settings
  and marks it `managed: "settings"`: list what the relay can actually resolve,
  never only what the database happens to hold.
- **Auth is the Plex account, not a password store**: the API needs a session
  (`core/session.py`, signed with `settings.secret` + the Plex token) once a Plex
  credential exists. Sign with the **stored** token (`owner_token(db)`), never the
  one a login just minted — Plex issues a fresh token per PIN login, so that cookie
  verifies against nothing and the login 401s forever. `/api/auth/*` is always open;
  a fresh install is open so setup can run; `auth = false` in `.config` turns
  enforcement off deliberately. Never add a second identity — one user, one library.
  A Plex PIN is redeemable only by the browser that opened it (the `relay_pin`
  cookie, `core/plex_auth.py`), and the setup callback refuses a Plex account other
  than the stored owner — keep both, or a guessed PIN id hands over the relay.
- **A Plex server is chosen by name *or* by address.** `PLEX_TOKEN` +
  `PLEX_SERVER_URL` is a complete Plex side (how a Key Vault deploy is wired), and
  there is no row to hold a server name — so `plex_auth_status` asks the server for
  its own `friendlyName` rather than leaving the wizard on "pick a server" forever.
  `plex-login` (`cli/`, `core/plex_login.py`) is the headless way in: it prints the
  plex.tv link, polls the PIN, and stores the same row the browser callback does.
  Beware the precedence trap it warns about: with `PLEX_TOKEN` set, a token it just
  stored is invisible until the variable goes.
- **Jobs are data, not events**: progress lives in the `job` and `track_event` rows.
  The worker commits per track so the SSE stream can tail the database; there is no
  in-process registry to keep in sync. A run is stopped by `job.cancel_requested` at
  a track boundary, never by killing the task.
- **An interrupted job resumes, it does not repeat.** A `running` job found at
  startup goes back to `queued` with `started_at` intact, and the worker treats a
  job that already has a `started_at` as a second attempt: the counters continue
  from the row, and an `already_synced` outcome is an echo of work the first
  attempt reported, so it is neither journalled nor counted. Skips that are *news*
  (`unrecorded_target` refusals) are still recorded. Job status is computed from
  those accumulated counters, so a resumed job reports one run, not two halves.
- **Network calls go through `engine/retry.attempt`**: three tries, doubling
  backoff, `OSError` only — connection trouble and timeouts, which is what
  `requests` raises, are retried; a `ValueError` refusal is a decision and never is.
  Retrying is safe because every call the engine makes is idempotent. A failed
  fetch or a target that cannot be planned fails the run or the track; it never
  silently becomes a skip.
- **Every network call has a deadline.** plexapi and pylast set their own;
  liblistenbrainz and musicbrainzngs set none and would wait on a silent server
  forever (RR-11), so `engine/http_timeouts.install()` gives them one. A new
  client needs a timeout before it is used, and a test against a silent socket
  (`tests/test_http_timeouts.py`).
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
