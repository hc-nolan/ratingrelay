# Introduction

**Relay** loved track **ratings** from Plex to ListenBrainz and/or Last.FM.

ListenBrainz also supports hated tracks.

The relay is one-way from Plex to the other services - Plex track ratings will not be modified.

The script will un-love/hate tracks, but only ones that it added. This way, if you have existing loved/hated tracks that you manually set, they won't be removed.

# Usage

To run the script, you must set a number of configuration values in a file named `config.env` in the root of the repository. You can use `config.env.example` as a starting point.


- `PLEX_SERVER_URL`: the URL of your Plex server
- `MUSIC_LIBRARY`: by default, this is set to "Music". If your library is named something else, change this value.
- `LOVE_THRESHOLD`: by default, this is set to 10 (5 stars). Ratings are out of 10, so 1 star in the Plex UI = 2/10.
  - (optional) `HATE_THRESHOLD`: If you want to use hated tracks with ListenBrainz, uncomment this.
- ListenBrainz and/or Last.FM API values
  - Fill out one or both of these sets of values.

## Docker Compose

After setting the required environment variables, you must manually authenticate with Plex. To do so, run:
```bash
sudo docker compose run --rm ratingrelay
```

You should be prompted for your Plex authentication details. After this you can either let the script finish or press `Ctrl+C` to finish it.

Then, you can set the container to execute however often you like. You can change the frequency on this line:
```yml
      ofelia.job-run.ratingrelay.schedule: "@every 24h" # how often to run
```

After everything is set up, run:
```bash
sudo docker compose up -d
```

## Python

- [Install uv](https://docs.astral.sh/uv/#installation)
- In the repository directory, run `uv sync --frozen`
- Run the script with: `uv run /path/to/repo/ratingrelay.py`
  - **Note**: The first time the script runs, you will be prompted to enter your Plex authentication details.
  - If you wish, you can also run with `/path/to/repo/.venv/bin/python /path/to/repo/ratingrelay.py`

To run on a regular basis, set up a cronjob (Linux) or Scheduled Task (Windows).

# Testing

If you want to run the test to make sure everything works properly, you should use separate accounts on Plex and any services you wish to test. These tests rely on accounts that start with no data. Additionally, all data is reset on the accounts at the end of testing - so **if you use your real account to run the tests, your data will be lost.**

- [Install uv](https://docs.astral.sh/uv/#installation)
- In the repository directory, run `uv sync --frozen`
- Create a copy of `config.env.example` named `test.env` and enter the test account credentials
- You should either back up your existing database to another location, or change the value of `DATABASE` in `config.env` to create a new database for the tests
- Run the script normally once and authenticate with Plex
- Open `./ratingrelay/config.py` and change the following:
```python
class Settings(BaseSettings):
    ...
    ...
    env_file="config.env")
```
to:
```python
class Settings(BaseSettings):
    ...
    ...
    env_file="test.env")
```

- Run the tests: `uv run pytest`
