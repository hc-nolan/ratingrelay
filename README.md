# RatingRelay

Rate a track once, and have that rating show up everywhere you listen.

RatingRelay keeps your **track ratings** in sync between **Plex**, **Last.fm** and
**ListenBrainz**. Give a song four stars in Plex and it gets loved on Last.fm and
ListenBrainz. Love something on Last.fm and it gets rated in Plex. Change your mind
later, and the relay changes it back.

It's self-hosted and built for one person and one music library. You run it on your
own machine or home server, and your credentials stay there.

![The relay page: Plex as the source, Last.fm and ListenBrainz as destinations](docs/screenshots/relay.png)

## What it does

- **Rated in Plex → loved elsewhere.** Pick a cutoff, for example "at least 4 stars",
  and every track that meets it is loved on Last.fm and/or ListenBrainz.
- **Loved elsewhere → rated in Plex.** It works the other way too: your Last.fm or
  ListenBrainz loves can become star ratings in Plex.
- **Keeps up with you.** Each run compares your ratings with what it wrote last time.
  Lower a rating below the cutoff and the love is taken back.
- **Leaves your other ratings alone.** RatingRelay only undoes changes it made
  itself. Anything you loved by hand on Last.fm stays loved.
- **Doesn't guess.** Tracks are matched by their MusicBrainz ID. If a track can't be
  matched with confidence, RatingRelay sets it aside for you to check rather than
  loving the wrong song.
- **Try before you write.** A dry run does everything except change anything, so you
  can see what *would* happen first.

## Getting started

RatingRelay runs in Docker. You need [Docker](https://docs.docker.com/get-docker/)
with the Compose plugin.

```shell
git clone https://github.com/hc-nolan/ratingrelay.git
cd ratingrelay
cp .env.example .env
```

Open `.env` and set `RATINGRELAY_SECRET` to a long random string. Running
`openssl rand -hex 32` will make one. You can also set your time zone there. Then:

```shell
docker compose up -d --build
```

Open `http://<your-server>:8100`, or `http://localhost:8100` if Docker runs on the
same computer. To use a different port, change `RATINGRELAY_PORT` in `.env`.

If you reach RatingRelay through a reverse proxy or your own domain, uncomment
`BASE_URL` in `compose.yaml` and set it to the address you type in the browser.
The Plex sign-in sends you back to that address.

### First-time setup

The first time you open the app, it walks you through three steps:

1. **Connect Plex.** A Plex sign-in window opens. The account you sign in with
   becomes the owner, and from then on it's also how you log in to RatingRelay.
   You don't need another password.
2. **Pick your Plex server.** Choose the server whose music library holds your
   ratings.
3. **Connect your music services.** Add Last.fm, ListenBrainz or both:
   - *Last.fm:* an [API key and secret](https://www.last.fm/api/account/create),
     plus your username and password.
   - *ListenBrainz:* your username and the user token from your
     [settings page](https://listenbrainz.org/settings/).

Press **Start relaying** when you're done.

## Using the app

### Relay

The main page reads from top to bottom:

- **From**: where your ratings come from. For Plex, choose the cutoff ("at least 4
  stars"). For ListenBrainz, choose loves or hates.
- **To**: switch on one or more destinations. ListenBrainz can receive a love or a
  hate. Plex receives a star rating.
- **Trigger**: run it **once**, or repeat it every hour, every few hours, daily or
  weekly. Tick **Dry run** to preview the run without changing anything.

Press **Queue run**. Progress appears in the **Activity** panel on the right as
tracks are processed.

**Reset** in the *From* row undoes what RatingRelay has written to a service. Only
its own changes are removed, never ratings you made yourself.

### History

![History: every run, grouped by day](docs/screenshots/history.png)

Every run is listed here, newest first. Filter to runs that are still going, or to
the ones that **need a look** because something failed. Open a run to see what
happened to each track: written, already up to date, unmatched or failed.

![A single run, with a count per destination and a log for each track](docs/screenshots/run.png)

### Unmatched

![Unmatched: an ambiguous track with two candidates to choose from](docs/screenshots/unmatched.png)

Sometimes a track can't be found on the other service. The title might be written
differently, or there might be several possible matches. Those tracks wait here
instead of being guessed at. Open one to:

- pick the right song from the suggested candidates, or
- type the artist and title as the other service spells them.

That saves a **mapping**, and every future run uses it.

An unmatched track isn't an error. Plenty of tracks in a personal library simply
don't exist on Last.fm or ListenBrainz.

### Services

![Services: connection status for Plex, Last.fm and ListenBrainz](docs/screenshots/services.png)

See what's connected, switch Plex servers, replace an expired token or disconnect a
service. Disconnecting removes only the login details. RatingRelay still remembers
what it wrote, so if you reconnect later it picks up where it left off.

Disconnecting **Plex** also signs you out, because your Plex account is how you log
in to RatingRelay. You'll be asked to confirm first.

## Your data

Everything RatingRelay knows is kept in the `ratingrelay-data` Docker volume. That
includes your connected accounts, run history, mappings and a record of every
change it has made. That record is how it can undo its own changes without touching
yours. Back up the volume if you care about it, and don't delete it when updating.

## Updating

```shell
git pull
docker compose up -d --build
```

The database is upgraded automatically when the new version starts.

## Running without the web app

Everything the web app does can also run from the command line. Both use the same
relay code, so a command-line run and a run in the app always agree.

You probably don't need this if you use Docker: the app's **Repeat** option
already runs a relay on a schedule. The command line is for scripting, for cron,
or for running RatingRelay with no web app at all.

With Docker, run the commands inside the container:

```shell
docker compose exec ratingrelay ratingrelay relay --source plex --target lastfm --dry-run
```

To use a config file, uncomment the `relays.toml` line under `volumes:` in
`compose.yaml` and pass `--config /data/relays.toml`. The examples below show the
commands on their own; outside Docker, run them with `uv run` in front.

```shell
# One relay, from flags. This is the shape to try first.
ratingrelay relay --source plex --target lastfm --dry-run

# Relay to several targets at once.
ratingrelay relay --source plex --target lastfm --target listenbrainz

# Everything a config file defines, one after another.
ratingrelay run --config relays.toml
ratingrelay list --config relays.toml   # what would run
```

`--dry-run` runs the real pipeline (fetch, match, reconcile) and skips only the
writes, so its counts are what a real run would do.

| flag | command | what it does |
| ---- | ------- | ------------ |
| `--source SERVICE` | `relay` | where ratings come from: `plex`, `lastfm`, `listenbrainz` (default `plex`) |
| `--target SERVICE` | `relay` | where they go; repeat for more than one (default `lastfm`) |
| `--rating-threshold N` | `relay` | Plex source: the rating a track needs, out of 10 (default `8`) |
| `--comparison gte\|lte` | `relay` | Plex source: at least or at most the threshold |
| `--feedback-type love\|hate` | `relay` | Last.fm/ListenBrainz: which feedback to relay |
| `--plex-rating N` | `relay` | Plex target: the rating to write, out of 10 (default `10`) |
| `--config PATH` | `run`, `list` | relay definitions to read (default `relays.toml`) |
| `--only NAME` | `run` | run just the named relay; repeatable |
| `--dry-run` | `relay`, `run` | fetch, match and reconcile for real; write nothing |
| `--json` | `relay`, `run` | one machine-readable document instead of the human summary |
| `--verbose` | `relay`, `run` | a line per track, on stderr |

Credentials come from environment variables (or `.config` outside Docker), falling
back to the ones you entered in the web app. Outside Docker, run `just migrate`
first on a fresh checkout. The container does this when it starts.

### Config file

`relays.toml.example` is a starting point. Each `[[relay]]` block is one relay:

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

Unknown keys are an error, so a typo can't make a cron job silently do nothing.

### cron

```cron
# Docker
0 4 * * * cd /srv/ratingrelay && docker compose exec -T ratingrelay ratingrelay run --config /data/relays.toml >> /var/log/ratingrelay.log 2>&1
# From source
0 4 * * * cd /srv/ratingrelay && /usr/local/bin/uv run ratingrelay run >> /var/log/ratingrelay.log 2>&1
```

A clean run prints a short summary; failures are loud. Exit codes:

| code | meaning |
| ---- | ------- |
| 0 | ran; nothing failed |
| 1 | ran; some tracks failed on a target |
| 2 | could not complete (bad config, missing database, fetch error) |

Unmatched tracks don't count as failures. They're counted in the output and listed
on the web app's **Unmatched** page. A dry run can only exit 0 or 2.

## Development

To run from source instead of Docker, you'll need [uv](https://docs.astral.sh/uv/),
[pnpm](https://pnpm.io/) and [just](https://just.systems/).

```shell
cp .config.example .config   # settings and, optionally, credentials
just install     # uv sync + pnpm install
just dev         # API on :8100 + Vite dev server on :5173, Ctrl-C stops both
just demo        # the UI against made-up data on :5174; calls no real service
just test        # pytest
just lint        # pylint
just check       # svelte-check + prettier + eslint
just build && uv run app.py   # serve the built app from the API, as the image does
```

The screenshots above come from `just demo`. Contributor notes, including the
layering rules and the behaviour the relay must not regress, are in `AGENTS.md`.
