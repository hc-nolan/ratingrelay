# Introduction

This is a script that "syncs" your Plex track ratings to external services like Last.fm and ListenBrainz. You define a certain rating threshold in the configuration, and any tracks with a rating equal to or greater than that threshold will be submitted as Loved Tracks to the external services. 

For example, setting `RATING_THRESHOLD=6.0` will consider any tracks with 3 star ratings or higher to be Loved Tracks. `RATING_THRESHOLD=10.0` would consider only tracks with 5 star ratings.

As of v0.1, Last.fm is supported. Other services such as ListenBrainz and Libre.fm will be added in future updates. If there is a service you would like to be added, please submit a new issue with the details.

# Usage

Start by renaming the file `.env.example` to `.env` and filling out the required values:
- `CID`: leave blank
- `SERVER_URL`: the URL to reach your Plex server
- `TOKEN`: leave blank
- `MUSIC_LIBRARY`: the title of your music library
- `RATING_THRESHOLD`: a number between 0.0 and 10.0; any tracks with a rating equal to or greater than this number will be submitted as Loved Tracks

For Last.fm usage:
- `LASTFM_API_KEY` and `LASTFM_SECRET`: obtain these at https://www.last.fm/api/account/create
- `LASTFM_USERNAME` and `LASTFM_PASSWORD`: your Last.fm username and password.
  - These are required to automate the authentication process. Without them, you would need to open a web browser and authorize the application every time the script is run.

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