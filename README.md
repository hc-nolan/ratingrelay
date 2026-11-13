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
