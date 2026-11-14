# RatingRelay

Keeps **track ratings** in sync between Plex, Last.fm and ListenBrainz. One user,
one library. Runs as a small web app, or headless from cron.

## Development

- Dependencies
    - [pnpm](https://pnpm.io/)
    - [uv](https://docs.astral.sh/uv/)

- Backend
```shell
uv sync
uv run alembic upgrade head  # apply database migrations (required before first run and after pulling new migrations)
uv run app.py
```
- Frontend
```shell
cd frontend
pnpm install
pnpm run dev
```

## Headless (no API required)

The relay is the same code either way: the CLI and the web app both drive
`ratingrelay/engine/`, so a headless run and a job in the app cannot disagree.

```shell
# One relay, from flags. This is the shape to try first.
uv run ratingrelay relay --source plex --target lastfm --dry-run

# Relay to several targets at once.
uv run ratingrelay relay --source plex --target lastfm --target listenbrainz

# Everything a config file defines, sequentially.
uv run ratingrelay run --config relays.toml
uv run ratingrelay list --config relays.toml   # what would run
```

`--dry-run` runs the real pipeline — fetch, match, reconcile — and suppresses
only the writes, so its counts are what a real run would do.

| flag | command | what it does |
| ---- | ------- | ------------ |
| `--source SERVICE` | `relay` | where ratings come from: `plex`, `lastfm`, `listenbrainz` (default `plex`) |
| `--target SERVICE` | `relay` | where they go; repeat for more than one (default `lastfm`) |
| `--rating-threshold N` | `relay` | Plex source: the rating a track needs (default `8`) |
| `--comparison gte\|lte` | `relay` | Plex source: at least or at most the threshold |
| `--feedback-type love\|hate` | `relay` | Last.fm/ListenBrainz: which feedback to relay |
| `--plex-rating N` | `relay` | Plex target: the rating to write (default `10`) |
| `--config PATH` | `run`, `list` | relay definitions to read (default `relays.toml`) |
| `--only NAME` | `run` | run just the named relay; repeatable |
| `--dry-run` | `relay`, `run` | fetch, match and reconcile for real; write nothing |
| `--json` | `relay`, `run` | one machine-readable document instead of the human block |
| `--verbose` | `relay`, `run` | a line per track, on stderr |

Credentials come from `.config` (or the environment), falling back to the
credentials stored in the database. The API does not need to be running; the CLI
opens `ratingrelay.db` directly and never touches `frontend/`. Run
`uv run alembic upgrade head` first on a fresh checkout.

### Exit codes

| code | meaning |
| ---- | ------- |
| 0 | ran; nothing failed |
| 1 | ran; some tracks failed on a target |
| 2 | could not complete (bad config, missing database, fetch error) |

Tracks that could not be matched are *not* failures — a rating whose track the
target does not have is the normal case. They are counted in the output and
listed in the web app's unmatched queue. A dry run can only exit 0 or 2.

### cron

```cron
0 4 * * * cd /srv/ratingrelay && /usr/local/bin/uv run ratingrelay run >> /var/log/ratingrelay.log 2>&1
```

`--json` prints one machine-readable document instead of the human block (one
object for a single relay, `{"relays": [...]}` for a batch); `--verbose` adds a
line per track on stderr. A config file like this defines the batch:

```toml
[[relay]]
name = "nightly"
source = "plex"
targets = ["lastfm", "listenbrainz"]
rating_threshold = 8.0

[[relay]]
name = "back-to-plex"
source = "lastfm"
targets = ["plex"]
plex_rating = 10.0
```

Unknown keys are an error, so a typo in a cron file cannot silently do nothing.

## The web app

```shell
just install     # uv sync + pnpm install
just migrate     # alembic upgrade head
just dev         # API on :8100, Vite on :5173
```

Open it and the setup wizard walks you through the three things it needs:

1. **Plex** — "Connect Plex" opens a Plex login window; the account you sign in
   with becomes the relay's owner, and the app uses it as its login from then on.
2. **Plex server** — pick which server to read ratings from.
3. **Music services** — connect Last.fm (API key, secret, username, password) and
   ListenBrainz (username and user token). Connect one or both; the wizard waits
   for you to press **Start relaying** rather than moving on by itself.

Once set up, `/` is the relay: pick a source, switch on the destinations, choose
the trigger, and queue a run. **Dry run** is the same pipeline with the writes
suppressed. `/history` lists every run, `/unmatched` holds the tracks no target
could be matched to, and `/services` manages credentials — replace a token or
disconnect a service without touching the database.

Disconnecting a service forgets its credential and nothing else: the records of
what the relay has already written stay, so a reconnected account can still be
reconciled and reset. Plex is the exception the UI warns about — the session
cookie is signed with its token, so disconnecting it also removes the app's
login until you sign in again.

The API is private once a Plex account is configured: `/api/*` answers `401` to
anyone without a session cookie. `/api/auth/*` is always open so a login is
possible, and a fresh install is open until it has an owner.

### API, briefly

| route | what it does |
| ----- | ------------ |
| `GET /api/auth/status`, `POST /api/auth/plex/init`, `GET /api/auth/plex/poll?pin_id=`, `POST /api/auth/logout` | the Plex login the app uses |
| `POST /api/relay/run` | run one relay synchronously and return its result |
| `POST /api/relay/jobs` | queue a run for the worker (what the UI does) |
| `GET /api/relay/jobs`, `DELETE /api/relay/jobs/{id}` | list runs, stop one at the next track boundary |
| `GET /api/relay/jobs/{id}` | one run with its events and a per-destination tally |
| `GET /api/relay/jobs/{id}/stream` | server-sent progress, tailed from the database |
| `GET /api/relay/unmatched`, `GET /api/relay/unrevertable`, `GET/POST/DELETE /api/relay/mappings` | the queue and the mappings that fix it |
| `GET/POST /api/{plex,lastfm,listenbrainz}/auth` | connection status and credential entry |
| `GET /api/plex/servers`, `POST /api/plex/server` | list the account's servers, choose one |
| `DELETE /api/{plex,lastfm,listenbrainz}/auth` | forget a service's credential |
