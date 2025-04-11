This page covers how to get up and running with RatingRelay.

The general steps are:
- Clone the repository: `git clone https://codeberg.org/hnolan/ratingrelay.git`
- Set environment variables
- Set up the project environment
- Run once to make sure everything works
- Set to run automatically at regular intervals

# 1. Environment variables

Start by renaming the file `.env.example` to `.env` and filling out the required values:
- `SERVER_URL`: the URL to reach your Plex server
- `MUSIC_LIBRARY`: the title of your music library
- `RATING_THRESHOLD`: a number between 0.0 and 10.0; any tracks with a rating equal to or greater than this number will be submitted as Loved Tracks

**Important note:** you do not need to modify `PLEX_CID` or `PLEX_TOKEN`. They are set automatically during the authentication process. If you don't see these values in your `.env` file, don't worry, they are added during the authentication process. 

For Last.fm usage:
- `LASTFM_API_KEY` and `LASTFM_SECRET`: obtain these at https://www.last.fm/api/account/create
- `LASTFM_USERNAME` and `LASTFM_PASSWORD`: your Last.fm username and password.
  - These are required to automate the authentication process. Without them, you would need to open a web browser and authorize the application every time the script is run.

For ListenBrainz usage:
- `LISTENBRAINZ_TOKEN`: obtain at https://listenbrainz.org/settings/
- `LISTENBRAINZ_USERNAME`: your ListenBrainz username

# 2. Set up project environment

## Option 1: Docker (preferred)

Run the following commands to 1) build the image and 2) run the script.

```bash
docker build . -t ratingrelay:latest
docker run -v $(pwd)/.env:/app/.env --rm ratingrelay:latest
```

The `run.sh` script is included to conveniently launch such containers via cron.

## Option 2: Local python

This method will run the script directly on your host machine using a Python virtual environment. 

### With uv

- [Install uv](https://docs.astral.sh/uv/#installation)
- Clone the repository: `git clone https://codeberg.org/hnolan/ratingrelay`
- Enter the directory and install dependencies: `cd ratingrelay && uv sync --frozen`

You can now run the script with either of the below:
- `uv run ratingrelay.py` 
- `/path/to/repo/.venv/bin/python /path/to/repo/ratingrelay.py`


# 3. First run

The first time you run the script, you will need to check the logs for a Plex authentication URL. This should only be required the first time the script is run, or if you don't run the script for a few days. You will see something similar to the below in the output:

```bash
2025-02-09 14:38:27,708:INFO:ratingrelay:Starting RatingRelay
2025-02-09 14:38:27,708:INFO:plex:/tmp/ratingrelay/.env
2025-02-09 14:38:28,026:INFO:plex:Please open the below URL in a web browser to authenticate to Plex.
2025-02-09 14:38:28,026:INFO:plex:Plex auth URL: https://app.plex.tv/auth#?clientID=...&code=...&context%5Bdevice%5D%5Bproduct%5D=ratingrelay
```

Opening this URL will ask you to sign into your Plex account and authorize RatingRelay to access your server.

# 4. Run at regular intervals
 
## Linux - cron

On Linux, you can set up a cronjob to run the script regularly. A complete explanation of Cron is outside the scope of this project - see [this](https://linuxhandbook.com/crontab/) tutorial instead.

[This tool](https://it-tools.tech/crontab-generator) can be used to help with the syntax of the time interval you desire.

Example: if you cloned the repository to `/home/user1/ratingrelay` and want to run the script once every 24 hours at midnight:
```bash
0 0 * * * /home/user1/ratingrelay/run.sh
```

## Windows - scheduled task

TBD
