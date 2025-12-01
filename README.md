# RatingRelay

Syncs track ratings from Plex to ListenBrainz and/or Last.fm. ListenBrainz supports both loved and hated tracks.

By default, syncing is one-way (Plex → other services). Plex ratings are not modified unless you enable the `TWO_WAY` environment variable.

The script will only remove loved/hated tracks from Last.fm/ListenBrainz that it previously added, preserving any manually set ratings.

## Configuration

Create a `config.env` file in the repository root. Use `config.env.example` as a template.

Required settings:
- `PLEX_SERVER_URL`: Your Plex server URL
- `MUSIC_LIBRARY`: Name of your music library (default: "Music")
- `LOVE_THRESHOLD`: Rating threshold for loving tracks (default: 10, which equals 5 stars). Plex uses a 0-10 scale where 1 star = 2 points.
- `HATE_THRESHOLD`: (Optional) Rating threshold for hating tracks on ListenBrainz

API credentials:
- Configure ListenBrainz and/or Last.fm credentials (at least one required)

## Installation

### Docker Compose

1. Set the required environment variables in `config.env`
2. Authenticate with Plex:
   ```bash
   sudo docker compose run --rm ratingrelay
   ```
   Follow the authentication prompts. You can exit with `Ctrl+C` after authenticating.

3. Adjust the sync frequency by modifying this line in `docker-compose.yml`:
   ```yml
   ofelia.job-run.ratingrelay.schedule: "@every 24h"
   ```

4. Start the container:
   ```bash
   sudo docker compose up -d
   ```

### Python

1. [Install uv](https://docs.astral.sh/uv/#installation)
2. Install dependencies:
   ```bash
   uv sync --frozen
   ```
3. Run the script:
   ```bash
   uv run /path/to/repo/ratingrelay.py
   ```
   On first run, you'll be prompted for Plex authentication.

   Alternatively: `/path/to/repo/.venv/bin/python /path/to/repo/ratingrelay.py`

4. Schedule regular execution using cron (Linux) or Task Scheduler (Windows)

## Testing

**Warning**: Tests reset all data on the configured accounts. Use separate test accounts to avoid data loss.

1. [Install uv](https://docs.astral.sh/uv/#installation)
2. Install dependencies:
   ```bash
   uv sync --frozen
   ```
3. Create `test.env` from `config.env.example` with test account credentials
4. Either back up your existing database or change the `DATABASE` value in `config.env` to use a separate test database
5. Edit `./ratingrelay/config.py`:
   ```python
   class Settings(BaseSettings):
       ...
       env_file="test.env")
   ```
6. Run the script once to authenticate with Plex
7. Run tests:
   ```bash
   uv run pytest
   ```
