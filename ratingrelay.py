"""
RatingRelay
Usage: python ratingrelay.py -m <mode>
"""

import argparse
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional
import env
from services import Plex, LastFM, ListenBrainz, try_to_make_Track, Track


log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(module)s:%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        TimedRotatingFileHandler(
            "ratingrelay.log", when="midnight", interval=30, backupCount=6
        ),
    ],
)
logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
logging.getLogger("pylast").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


LFM_USERNAME = env.get("LASTFM_USERNAME")
LFM_PASSWORD = env.get("LASTFM_PASSWORD")
LFM_TOKEN = env.get("LASTFM_API_KEY")
LFM_SECRET = env.get("LASTFM_SECRET")


LBZ_USERNAME = env.get("LISTENBRAINZ_USERNAME")
LBZ_TOKEN = env.get("LISTENBRAINZ_TOKEN")

PLEX_URL = env.get_required("SERVER_URL")
PLEX_LIBRARY = env.get_required("MUSIC_LIBRARY")
PLEX_LOVE_THRESHOLD = env.get_required_int("LOVE_THRESHOLD")
PLEX_HATE_THRESHOLD = env.get("HATE_THRESHOLD")


def plex_mode(plex: Plex, lbz: ListenBrainz, lfm: LastFM):
    """
    This function is run when the script is run with `-m plex`, and syncs
    loved/hated tracks FROM Plex TO LBZ/LFM
    """
    plex_mode_loves(plex=plex, lbz=lbz, lfm=lfm)
    plex_mode_hates(plex=plex, lbz=lbz)


def plex_mode_loves(plex: Plex, lbz: ListenBrainz, lfm: LastFM):
    """
    Relays loves from Plex to LBZ/LFM
    """
    lbz_added = 0
    lbz_removed = 0
    lfm_added = 0
    lfm_removed = 0
    converted_tracks = set()

    if lbz:
        lbz_loves = lbz.all_loves()
        lbz_loved_mbids = {t.mbid for t in lbz_loves}

    if lfm:
        lfm_loves = lfm.all_loves()

    log.info("Relaying tracks from Plex.")
    # Relay loved tracks first
    plex_loves = plex.get_loved_tracks()
    log.info("Plex returned %s loved tracks.", len(plex_loves))

    for plex_track in plex_loves:
        track = try_to_make_Track(plex_track)
        converted_tracks.add(track)

        if lbz:
            if track.mbid not in lbz_loved_mbids:
                log.info("Loving %s by %s on ListenBrainz", track.title, track.artist)
                lbz.love(track)
                lbz_added += 1
            else:
                log.info(
                    "Track: %s by %s - already loved on ListenBrainz",
                    track.title,
                    track.artist,
                )

        if lfm:
            if (track.title, track.artist) not in lfm_loves:
                log.info("Loving %s by %s on Last.FM", track.title, track.artist)
                lfm.love(track)
                lfm_added += 1
            else:
                log.info(
                    "Track: %s by %s - already loved on Last.FM",
                    track.title,
                    track.artist,
                )

    log.info(
        "Added loves:     ListenBrainz: %-10s Last.FM: %-10s", lbz_added, lfm_added
    )

    if lbz:
        for track in lbz_loves:
            if track not in converted_tracks:
                lbz.reset(track)
                lbz_removed += 1

    if lfm:
        converted_track_tuples = [
            Track(t.title, t.artist, mbid=None) for t in converted_tracks
        ]
        for track in lfm_loves:
            if track not in converted_track_tuples:
                lfm.reset(track)
                lfm_removed += 1

    log.info(
        "Removed loves:   ListenBrainz: %-10s Last.FM: %-10s", lbz_removed, lfm_removed
    )


def plex_mode_hates(plex: Plex, lbz: ListenBrainz):
    """
    Relays hates from Plex to LBZ

    Note that LFM does not support Hated tracks
    """
    if not lbz:
        log.warning("ListenBrainz not configured, skipping relaying hated tracks.")
        return

    lbz_added = 0
    lbz_removed = 0
    converted_tracks = set()

    lbz_hates = lbz.all_hates()
    lbz_hated_mbids = {t.mbid for t in lbz_hates}

    log.info("Relaying tracks from Plex.")
    # Relay hated tracks first
    plex_hates = plex.get_hated_tracks()
    log.info("Plex returned %s hated tracks.", len(plex_hates))

    for plex_track in plex_hates:
        track = try_to_make_Track(plex_track)
        converted_tracks.add(track)

        if track.mbid not in lbz_hated_mbids:
            log.info("Hating %s by %s", track.title, track.artist)
            lbz.hate(track)
            lbz_added += 1

    log.info("Added hates:   %s", lbz_added)

    for track in lbz_hates:
        if track not in converted_tracks:
            lbz.reset(track)
            lbz_removed += 1

    log.info("Removed hates: %s", lbz_removed)


def lbz_mode():
    """
    This function is run when the script is run with `-m lbz`, and syncs
    loved/hated tracks FROM ListenBrainz TO Plex and LFM
    """
    pass


def reset(plex: Plex, lbz: ListenBrainz, lfm: LastFM):
    """
    Reset all ratings submitted to ListenBrainz or Last.fm
    """
    if lbz:
        loves = lbz.all_loves()
        for track in loves:
            lbz.client.submit_user_feedback(0, track.mbid)
        hates = lbz.all_hates()
        for track in hates:
            lbz.client.submit_user_feedback(0, track.mbid)

    if lfm:
        loves = lfm.all_loves()
        for track in loves:
            lfm.reset(track)


def read_args() -> str:
    """
    Read CLI arguments passed to the script
    """
    parser = argparse.ArgumentParser(
        description="RatingRelay - relay track ratings between Plex and ListenBrainz/Last.fm"
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["plex", "lbz", "reset"],
        required=True,
        help="Mode to run the script in (plex or lbz)",
    )

    args = parser.parse_args()

    if args.mode == "plex":
        return "plex"
    elif args.mode == "lbz":
        return "lbz"
    elif args.mode == "reset":
        return "reset"
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)


def setup() -> tuple[Plex, Optional[LastFM], Optional[ListenBrainz]]:
    """
    Set up Plex object, attempt to set up LastFM and ListenBrainz objects.

    If the relevant variables are not set in `.env` for LastFM or ListenBrainz
    usage, that object will be None.
    """
    plex = Plex(
        music_library=PLEX_LIBRARY,
        love_threshold=PLEX_LOVE_THRESHOLD,
        hate_threshold=PLEX_HATE_THRESHOLD,
        url=PLEX_URL,
    )
    try:
        lfm = LastFM(
            username=LFM_USERNAME,
            password=LFM_PASSWORD,
            token=LFM_TOKEN,
            secret=LFM_SECRET,
        )
    except RuntimeError as e:
        log.error(
            "Got a runtime error when attempting to execute Last.fm - skipping Last.fm"
        )
        log.error("Error details:")
        log.error(e)
        log.error("This can be safely ignored if you do not wish to use Last.fm")
        lfm = None

    try:
        lbz = ListenBrainz(username=LBZ_USERNAME, token=LBZ_TOKEN)
    except RuntimeError as e:
        log.error(
            "Got a runtime error when attempting to execute ListenBrainz - skipping ListenBrainz"
        )
        log.error("Error details:")
        log.error(e)
        log.error("This can be safely ignored if you do not wish to use ListenBrainz")
        lbz = None

    return plex, lfm, lbz


def main():
    mode = read_args()
    plex, lfm, lbz = setup()
    if mode == "plex":
        plex_mode(plex=plex, lbz=lbz, lfm=lfm)
    if mode == "lbz":
        lbz_mode(plex=plex, lbz=lbz, lfm=lfm)
    if mode == "reset":
        reset(plex=plex, lbz=lbz, lfm=lfm)


if __name__ == "__main__":
    main()
