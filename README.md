# Introduction

RatingRelay is a script that syncs loved tracks from Plex to external services like ListenBrainz and Last.FM. 

Since Plex doesn't have a concept of "loved/liked" tracks, you have to define a rating threshold that you want to consider sufficient for "loving" a track. Any tracks with a rating greater than or equal to that threshold will be submitted to the external services. 

For example, setting `RATING_THRESHOLD=6.0` will consider any tracks with 3 star ratings or higher to be Loved Tracks. `RATING_THRESHOLD=10.0` would consider only tracks with 5 star ratings.

Supported services:
- Last.FM
- ListenBrainz

Planned:
- Libre.FM
- RYM/Sonemic (eventually)

# Usage

Start by renaming the file `.env.example` to `.env` and filling out the required values:
- `SERVER_URL`: the URL to reach your Plex server
- `MUSIC_LIBRARY`: the title of your music library
- `RATING_THRESHOLD`: a number between 0.0 and 10.0; any tracks with a rating equal to or greater than this number will be submitted as Loved Tracks

**For Last.fm usage**:
- `LASTFM_API_KEY` and `LASTFM_SECRET`: obtain these at https://www.last.fm/api/account/create
- `LASTFM_USERNAME` and `LASTFM_PASSWORD`: your Last.fm username and password.
  - These are required to automate the authentication process. Without them, you would need to open a web browser and authorize the application every time the script is run.

**For ListenBrainz usage**:
- `LISTENBRAINZ_USERNAME`: your ListenBrainz username
- `LISTENBRAINZ_TOKEN`: obtain at https://listenbrainz.org/settings/


## Setting up dependencies

Create a Python virtual environment and install the required packages:
```bash
# run these commands from the repository directory
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Dry run 

Manually run the script and check your external service accounts to make sure they updated properly:
```bash
python plexsync.py
```

## Run automatically at regular intervals

On Linux, you can set up a cronjob to run the script regularly. A complete explanation of Cron is outside the scope of this project - see [this](https://linuxhandbook.com/crontab/) tutorial instead.

[This tool](https://it-tools.tech/crontab-generator) can be used to help with the syntax of the time interval you desire.

**Note: the crontab must use the virtual environment Python binary, not the system-wide one.** 

Example: if you cloned the repository to `/home/user1/plex_ratings_sync` and want to run the script once every 24 hours at midnight:
```bash
0 0 * * * /home/user1/plex_ratings_sync/venv/bin/python /home/user1/plex_ratings_sync/plexsync.py
```