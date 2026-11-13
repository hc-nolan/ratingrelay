# Gap 7 and 8: a job you can stop, and an API that asks who you are

Two gaps survived the v3 rewrite because they were not on the relay's critical
path. Both are about the *surface* around the engine rather than the engine
itself, which is exactly why they were easy to keep deferring — and both are
things a stranger reaching `:8100` would notice immediately.

## Gap 8 — cancel stopped being a cancel

`DELETE /api/relay/jobs/{id}` refuses anything that is not `queued` or
`scheduled`, so the one case a user actually wants to stop — a run in progress,
burning rate limit against Last.fm and MusicBrainz — answers `409`. The UI has
no cancel affordance for a running job, which is honest at least, but the
behaviour is missing rather than refused.

The related smell is the transport. Live progress went out over an in-process
`dict[str, list[asyncio.Queue]]` in `core/worker.py`, so:

- two API processes (or a `--workers 2` uvicorn) would each hold half the
  listeners and neither would see the other's events;
- a browser that reconnected got only the rows the API process had buffered in
  its `past_events` snapshot — a restart lost the live tail;
- the worker could not be run headlessly at all without dragging FastAPI's
  event loop state along, which is the thing `engine/` exists to avoid.

### Cancellation

A run cannot be interrupted mid-HTTP-request, and it must not be interrupted
mid-write: the point of the pipeline is that every write it made is recorded
before it moves on. So cancellation is **cooperative at track boundaries**:

- `job.cancel_requested` is a new column (migration
  `b1c2d3e4f5a6`), set by `DELETE` on a running job.
- The worker's `on_track` sink records the event for the track it just
  finished, then asks the database whether a cancel was requested, and raises
  `RunCancelled` if so.
- `run_job` catches `RunCancelled` before its generic handler and marks the job
  `cancelled`, keeping the counters it actually reached. Because every
  completed track already wrote its `sync_record`, the next run resumes: no
  duplicate writes, no lost credit.

Cancelling during the fetch phase is observed when the fetch returns, because
the fetch is a single `await` inside the source. That is a documented limit, not
a hidden one.

`DELETE` keeps its old meaning for `queued`/`scheduled` (drop it before it
starts) and gains the running case. Terminal jobs still answer `409`.

### Transport

The SSE stream becomes a **tail of the database** instead of a subscription to
the worker's memory:

- the generator polls the `job` row and `track_event` rows every second, using
  its own short-lived session so a long-lived stream cannot serve a stale
  identity map;
- events are replayed from the start, ordered by `(created_at, id)` with an
  offset cursor — `track_event` is append-only, so an offset is stable;
- the stream ends on a terminal status, exactly as before;
- `_listeners`, `subscribe`, `unsubscribe` and `broadcast` are deleted, and the
  worker no longer imports an event loop's bookkeeping at all.

The frontend keeps `EventSource` and the same four event names, so this is a
transport change with no view-model change.

Trade-off, stated plainly: 1 s polling instead of push. For a job that writes at
roughly one track per second that is invisible; for a burst it can lag by a
second. It buys cross-process correctness and reconnect-replay.

## Gap 7 — the API trusts whoever can reach the socket

`settings.secret` defaults to `"AUTHSECRET"` and is read by nothing. The name
promises authentication that does not exist, on an API that can queue a
destructive reset. Removing the setting alone would be honest but would leave
the footgun loaded, so the gap is closed by doing what the name implied.

### Login is Plex

RatingRelay already trusts exactly one identity: the Plex account whose token it
syncs as. So it does not get a password, a user table, or a second source of
truth — it asks Plex, the way Overseerr and Tautulli do:

- `POST /api/auth/plex/init` mints a Plex PIN session using the existing
  `core/plex_auth` machinery and returns `{pin_id, auth_url}`. The popup's
  forward URL closes the window; nothing is written to the credential.
- `GET /api/auth/plex/poll?pin_id=…` calls `checkLogin()` server-side (the
  Overseerr pattern — the opener polls, there is no callback dependency).
  On success it identifies the account with `MyPlexAccount(token=…)` and
  compares its **id** with the id behind the credential the relay syncs as.
- Owner → `Set-Cookie: relay_session=…`, `HttpOnly`, `SameSite=Lax`,
  30 days. Not the owner → `403`, and no cookie: a shared Plex user is a
  stranger here, which is the whole point of a single-user tool.
- `POST /api/auth/logout` clears it. `GET /api/auth/status` is public and is
  what the SPA gates on.

The cookie is `v1.<expires>.<plex_user_id>.<hmac>`, keyed by
`sha256(settings.secret:plex_token)`:

- a default `secret` is no longer fatal — the token is per-install and already
  secret, so a forged cookie still needs the database;
- rotating the Plex token invalidates every session, which is the correct
  blast radius for "the account changed".

### What is guarded, and the bootstrap hole

A requirement is enforced when there is something to protect: once a Plex
credential exists, every `/api` route needs a session except `/api/auth/*`.
Before that — a fresh install, no token, no library — the app is open so the
setup wizard can run. `settings.auth = false` in `.config` disables enforcement
for a box that sits behind its own authenticating proxy; it is documented as a
deliberate hole, not a default.

The SPA gets a gate rather than eight per-call checks: the root layout asks
`/api/auth/status`, and renders a "Sign in with Plex" card instead of the app
when a session is required and absent. A `401` from any request reloads the
gate, so an expired cookie degrades to a login screen instead of a blank page.

## What must not change

- The engine still knows nothing about HTTP, cookies or jobs.
- `dry_run` semantics, the undoability rule and the reconcile contract are
  untouched by both gaps.
- No new credential storage: the Plex token is still the single identity, read
  through `core/credentials`.
- A cancelled job writes nothing new; the records it already wrote stay, because
  they describe writes that really did happen.

## What landed

Both gaps, in two commits (`a94b949` for cancellation and the stream, this one
for the login).

**Cancellation (gap 8).** `job.cancel_requested` (`b1c2d3e4f5a6`) plus a worker
that reads it at a track boundary:

- `DELETE /api/relay/jobs/{id}` on a running job sets the flag and answers 200
  with `cancel_requested: true`; the job keeps running until the worker reaches
  the boundary, then stops with status `cancelled` and its counters intact.
- The flag is checked *after* the track's event and sync record are committed,
  so a cancel never discards a write that really happened.
- A cancel that arrives while the process is dying is honoured on startup: the
  stale-job reset marks it `cancelled` instead of resuming it.
- Cancelling during the Plex fetch is only observed when the fetch returns,
  because the fetch is one await inside the source. That is a documented limit,
  not a silent one.

**The stream (gap 8).** `GET /api/relay/jobs/{id}/stream` tails the database
instead of an in-process `dict[str, list[asyncio.Queue]]`:

- it polls the job row and `track_event` once a second, replays what it has not
  seen (a count is a stable cursor because `track_event` is append-only), emits
  `status` when the row changes, a keepalive comment every 15 s, and `done` at a
  terminal status;
- `_listeners`, `subscribe`, `unsubscribe` and `broadcast` are gone, so the
  worker no longer needs the API's event loop and a restarted or second API
  process sees the same events;
- track events are committed per track as a consequence, which also means a
  crash mid-run keeps the history of what it wrote. The job row carries its own
  counters, so the stream cannot invent numbers the worker did not produce.

**The login (gap 7).** `core/session.py` + `routes/auth.py`:

| Route | What it does |
| --- | --- |
| `POST /api/auth/plex/init` | opens a Plex PIN, returns `pin_id` + `auth_url` |
| `GET /api/auth/plex/poll?pin_id=` | waits for confirmation, compares the Plex account **id** with the owner's, sets the cookie or 403s |
| `GET /api/auth/plex/done` | the popup's landing page; closes itself |
| `GET /api/auth/status` | `{required, authenticated, username}` |
| `POST /api/auth/logout` | clears the cookie |

The guard is a middleware built by `session_guard()` in `core/session.py`, wired
in `main.py`, tested with the factory injected. Setup also signs you in: the
Plex credential callback sets the cookie, so completing the wizard never lands
on a login screen immediately afterwards.

Frontend: `$lib/session.ts` (the only place that speaks to `/api/auth/*`),
`$lib/components/LoginGate.svelte` (shadcn `Card` + `Button`), and the root
layout, which gates on `/api/auth/status`, re-checks on tab focus, and calls
`invalidateAll()` after signing in so the pages that fetched while unsigned try
again.

**Tests.** 176 passing (was 148): 3 worker-cancel, 5 job-stream, 20 auth.

**Live verification (2026-11-13).** After the restart on the live DB:
`GET /api/auth/status` → `{"required": true, "authenticated": false, ...}`;
`GET /api/relay/unmatched` unsigned → **401**; and with a cookie issued
server-side for the stored Plex token the app loads normally and the job rail,
the flow canvas and `/unmatched` all answer 200. The PIN round trip itself is
covered by the stub tests — the real plex.tv call is the same `checkLogin()` the
setup wizard has been doing live since v2.

**Two honest notes.**

1. Cancelling a fetch-heavy job still means waiting for the fetch. Plex has no
   cancellation token and the engine is deliberately not request-shaped.
2. The login is Plex-only. If plex.tv is unreachable, an already-signed-in
   browser keeps working (the cookie is stateless), but there is no way to sign
   in afresh, and no local password fallback. For a single-user tool that beats
   another password store.

## Fix: the login "did nothing" because the cookie was signed with the wrong token

Symptom: the Plex popup completes, the API logs `Signed in as chunned`, and the SPA
stays on "This relay is private" forever.

Cause: `login_poll` signed the session with `pin_login.token` — the token *this
login just minted* — while the guard verifies against `owner_token(db)`, the token
the relay actually stores. Plex issues a new token per PIN login, so the two almost
never match: the browser receives a cookie that verifies against nothing, and every
later request is a 401 that looks exactly like "no cookie". The unit test passed
because its fake PIN handed back the same token the fixture had stored.

Fix: sign with the configured credential's token (`owner_token(db)`), which is the
only key the guard can check. The PIN token still proves *who* is logging in
(`identify()` + account-id comparison); it just is not the signing key.

Two supporting changes, because the failure mode was invisible:

- `GET /api/auth/status` now reports `cookie: bool` — "did this request carry a
  session cookie at all?". `no-cookie` and `bad-cookie` are different bugs with
  different fixes, and the page can finally tell them apart: `LoginGate` says
  "Plex signed you in, but the browser did not keep the session cookie" or "…the
  browser sent back a session this relay cannot verify".
- `/api/auth/status` is `Cache-Control: no-store` — a cached "no" outliving the
  login is the same bug wearing a different hat.
- The guard logs one warning per kind per process, with the cookie's shape
  (length, part count, expiry) and never its value.

Also: `LoginGate` now believes the poll's answer and lifts the gate immediately;
the confirming round trip no longer decides whether the page updates.

Regression test: `test_a_login_mints_a_new_token_but_signs_with_the_configured_one`
— the fake PIN returns a token nobody stored, then asserts the cookie verifies
against the stored token and that the next guarded request is a 200.
