# Introduction

Relay ratings from Plex to ListenBrainz or Last.fm based on a defined Plex rating threshold.

For example, setting `RATING_THRESHOLD=6.0` will consider any tracks with 3 star ratings or higher to be Loved Tracks. `RATING_THRESHOLD=10.0` would consider only tracks with 5 star ratings.

As of v0.2.0, Last.fm and ListenBrainz are supported. Other services such as Libre.fm will be added in future versions. If there is a service you would like to be added, please submit a new issue with the details.

# Usage

TL;DR:
- Set environment variables
- Build the Docker image: `docker build . -t ratingrelay:latest`
- Run the script container: `docker run -v $(pwd)/.env:/app/.env --rm ratingrelay:latest`
- If everything works, set up a cron job to run `run.sh` however often you like

**Note:** the first time you run the script you will need to check the logs for the Plex URL to authenticate with. 

Start by renaming the file `.env.example` to `.env` and filling out the required values:
- `SERVER_URL`: the URL to reach your Plex server
- `MUSIC_LIBRARY`: the title of your music library
- `RATING_THRESHOLD`: a number between 0.0 and 10.0; any tracks with a rating equal to or greater than this number will be submitted as Loved Tracks

For Last.fm usage:
- `LASTFM_API_KEY` and `LASTFM_SECRET`: obtain these at https://www.last.fm/api/account/create
- `LASTFM_USERNAME` and `LASTFM_PASSWORD`: your Last.fm username and password.
  - These are required to automate the authentication process. Without them, you would need to open a web browser and authorize the application every time the script is run.

For ListenBrainz usage:
- `LISTENBRAINZ_TOKEN`: obtain at https://listenbrainz.org/settings/
- `LISTENBRAINZ_USERNAME`: your ListenBrainz username

## Building and running

Run the following commands to 1) build the image and 2) run the script.

```bash
docker build . -t ratingrelay:latest
docker run -v $(pwd)/.env:/app/.env --rm ratingrelay:latest
```
The `run.sh` script is included to conveniently launch such containers via cron.

## Run automatically at regular intervals

On Linux, you can set up a cronjob to run the script regularly. A complete explanation of Cron is outside the scope of this project - see [this](https://linuxhandbook.com/crontab/) tutorial instead.

[This tool](https://it-tools.tech/crontab-generator) can be used to help with the syntax of the time interval you desire.

Example: if you cloned the repository to `/home/user1/ratingrelay` and want to run the script once every 24 hours at midnight:
```bash
0 0 * * * /home/user1/ratingrelay/run.sh
```
# Running locally

If you would like to contribute to the project it's easiest to run the script locally. This is not recommended otherwise.

## Setting up dependencies

- [Install uv](https://docs.astral.sh/uv/#installation)
- Clone the repository: `git clone https://codeberg.org/hnolan/ratingrelay`
- Enter the directory and install dependencies: `cd ratingrelay && uv sync`

If you don't want to use uv, a `requirements.txt` file is included for usage with pip.

## Dry run 

Manually run the script and check your external service accounts to make sure they updated properly:
```bash
uv run ratingrelay.py
```
