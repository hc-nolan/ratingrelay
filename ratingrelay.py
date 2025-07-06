"""
RatingRelay
Usage: python ratingrelay.py -m <mode>
"""

import argparse
import time
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional
import env
import sqlite3
from dataclasses import dataclass, asdict
from services import Plex, LastFM, ListenBrainz, make_Track, Track


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

DATABASE = env.get_required("DATABASE")


@dataclass
class Services:
    """
    Dataclass for interacting with services.

    plex: Plex
    cursor: sqlite3.Cursor
    lbz: Optional[ListenBrainz]
    lfm: Optional[LastFM]
    """

    plex: Plex
    cursor: sqlite3.Cursor
    conn: sqlite3.Connection
    lbz: Optional[ListenBrainz]
    lfm: Optional[LastFM]


def plex_mode(services: Services):
    """
    Run when the script is executed with `-m plex`; syncs
    loved/hated tracks from Plex to LBZ/LFM.
    """
    log.info("Relaying loved tracks from Plex.")
    love_stats = plex_mode_loves(**services.__dict__)
    if services.plex.hate_threshold is not None:
        hate_stats = plex_mode_hates(
            plex=services.plex,
            lbz=services.lbz,
            cursor=services.cursor,
            conn=services.conn,
        )
    else:
        hate_stats = {"plex_hates": 0, "lbz_added": 0}

    reset_tracks(
        lbz=services.lbz, lfm=services.lfm, cursor=services.cursor, conn=services.conn
    )

    log.info("STATISTICS:")
    log.info(
        "%-12s\tLoves: %-10s\tHates: %-10s",
        "Plex:",
        love_stats.get("plex_loves"),
        hate_stats.get("plex_hates"),
    )
    log.info("ADDITIONS:")
    log.info(
        "%-12s\tLoves: %-10s\tHates: %-10s\t",
        "ListenBrainz:",
        love_stats.get("lbz_added"),
        hate_stats.get("lbz_added"),
    )
    log.info(
        "%-12s\tLoves: %-10s\tHates: %-10s\t",
        "Last.FM:",
        love_stats.get("lfm_added"),
        "N/A",
    )


def plex_mode_loves(
    plex: Plex,
    lbz: ListenBrainz,
    lfm: LastFM,
    cursor: sqlite3.Cursor,
    conn: sqlite3.Connection,
):
    """
    Relays loves from Plex to LBZ/LFM
    """
    lbz_added = 0
    lfm_added = 0
    # Set of all tracks that met the rating threshold
    # Used for determining which tracks need to be un-loved
    plex_tracks = set()

    if lbz:
        log.info("Grabbing all existing loved tracks from ListenBrainz.")
        lbz_loves = lbz.all_loves()
        log.info("ListenBrainz returned %s loved tracks.", len(lbz_loves))
        lbz_loved_mbids = {t.mbid for t in lbz_loves}

    if lfm:
        lfm_loves = lfm.all_loves()
        lfm_loves_tuples = [(t.title.lower(), t.artist.lower()) for t in lfm_loves]
        log.info("Last.FM returned %s loved tracks.", len(lfm_loves))

    log.info("Querying Plex for loved tracks")
    plex_loves = plex.get_loved_tracks()
    log.info("Plex returned %s loved tracks.", len(plex_loves))

    for plex_track in plex_loves:
        log.info("Processing PlexTrack into Track: %s", plex_track.title)
        track = make_Track(plex_track=plex_track, cursor=cursor, rating="loved")
        plex_tracks.add(track)
        # insert the track if it's new, or ignore if there is a matching
        # recording MBID in the database
        cursor.execute(
            "INSERT OR IGNORE INTO loved (title, artist, trackId, recordingId) VALUES(?, ?, ?, ?)",
            (track.title, track.artist, track.track_mbid, track.mbid),
        )
        conn.commit()

        if lbz:
            if track.mbid not in lbz_loved_mbids:
                log.info("ListenBrainz - New love: %s by %s", track.title, track.artist)
                lbz.love(track)
                lbz_added += 1
            else:
                log.info(
                    "ListenBrainz - Track already loved: %s by %s",
                    track.title,
                    track.artist,
                )

        if lfm:
            if (track.title.lower(), track.artist.lower()) not in lfm_loves_tuples:
                log.info("Last.FM - New love: %s by %s", track.title, track.artist)
                lfm.love(track)
                lfm_added += 1
            else:
                log.info(
                    "Last.FM - Track already loved: %s by %s",
                    track.title,
                    track.artist,
                )

    log.info(
        "Finished adding loves:     ListenBrainz: %-10s Last.FM: %-10s",
        lbz_added,
        lfm_added,
    )
    find_tracks_to_reset(
        conn=conn, cursor=cursor, plex_tracks=plex_tracks, table="loved"
    )
    return {
        "plex_loves": len(plex_loves),
        "lbz_added": lbz_added,
        "lfm_added": lfm_added,
    }


def plex_mode_hates(
    plex: Plex, lbz: ListenBrainz, cursor: sqlite3.Cursor, conn: sqlite3.Connection
):
    """
    Relays hates from Plex to LBZ

    Note that LFM does not support Hated tracks
    """
    log.info("Relaying hated tracks from Plex.")
    if not lbz:
        log.warning("ListenBrainz not configured, skipping relaying hated tracks.")
        return

    lbz_added = 0
    plex_tracks = set()

    log.info("Grabbing existing ListenBrainz hated tracks.")
    lbz_hates = lbz.all_hates()
    log.info("ListenBrainz returned %s existing hated tracks", len(lbz_hates))
    lbz_hated_mbids = {t.mbid for t in lbz_hates}

    plex_hates = plex.get_hated_tracks()
    log.info("Plex returned %s hated tracks.", len(plex_hates))

    for plex_track in plex_hates:
        track = make_Track(plex_track=plex_track, cursor=cursor, rating="hated")
        plex_tracks.add(track)
        # insert the track if it's new, or ignore if there is a matching
        # recording MBID in the database
        cursor.execute(
            "INSERT OR IGNORE INTO hated (title, artist, trackId, recordingId) VALUES(?, ?, ?, ?)",
            (track.title, track.artist, track.track_mbid, track.mbid),
        )
        conn.commit()

        if track.mbid not in lbz_hated_mbids:
            log.info("Hating %s by %s", track.title, track.artist)
            lbz.hate(track)
            lbz_added += 1

    log.info("Finished adding hates:   ListenBrainz: %s", lbz_added)

    find_tracks_to_reset(
        conn=conn, cursor=cursor, plex_tracks=plex_tracks, table="hated"
    )

    return {"plex_hates": len(plex_hates), "lbz_added": lbz_added}


def find_tracks_to_reset(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    plex_tracks: list[Track],
    table: str,
):
    """
    Compares tracks in the 'loved' or 'hated' table to tracks that were returned by plex.
    If a track is in the database but not returned by Plex, it is assumed that
    this track is no longer loved, thus we should un-love it.

    This function identifies the tracks that should be unloved, removes them
    from the 'loved' table, and inserts them into the 'reset' table to signify
    to services that their ratings should be reset to 0.
    """
    log.info("Checking for tracks to reset.")
    match table:
        case "loved":
            result = cursor.execute(
                "SELECT title, artist, trackId, recordingId FROM loved"
            )
        case "hated":
            result = cursor.execute(
                "SELECT title, artist, trackId, recordingId FROM hated"
            )
    entries = result.fetchall()
    plex_ids = [track.mbid for track in plex_tracks]

    for title, artist, track_mbid, rec_mbid in entries:
        if rec_mbid not in plex_ids:
            match table:
                case "loved":
                    log.info("Track no longer loved on Plex: %s", (title, artist))
                    cursor.execute(
                        "DELETE FROM loved WHERE recordingId = ?", (rec_mbid,)
                    )
                case "hated":
                    log.info("Track no longer hated on Plex: %s", (title, artist))
                    cursor.execute(
                        "DELETE FROM hated WHERE recordingId = ?", (rec_mbid,)
                    )
            cursor.execute(
                "INSERT INTO reset (title, artist, trackId, recordingId) VALUES(?, ?, ?, ?)",
                (
                    title,
                    artist,
                    track_mbid,
                    rec_mbid,
                ),
            )
            conn.commit()


def reset_tracks(
    lbz: Optional[ListenBrainz],
    lfm: Optional[LastFM],
    cursor: sqlite3.Cursor,
    conn: sqlite3.Connection,
):
    log.info("Resetting tracks that are no longer loved/hated on Plex.")
    result = cursor.execute("SELECT id, title, artist, recordingId, trackId FROM RESET")
    to_remove = result.fetchall()

    if lbz:
        for dbid, title, artist, rec_mbid, track_mbid in to_remove:
            track = Track(
                title=title, artist=artist, mbid=rec_mbid, track_mbid=track_mbid
            )
            log.info("Removing %s", track)
            if rec_mbid is not None:
                lbz.reset(track)
            else:
                log.warning(
                    "No recording MBID returned, unable to reset track on ListenBrainz: %s",
                    track,
                )

    if lfm:
        for dbid, title, artist, mbid, track_mbid in to_remove:
            lfm.reset(Track(title=title, artist=artist))

    # Now that tracks have been reset, remove them from the 'reset' table
    for track in to_remove:
        cursor.execute(
            "DELETE FROM reset WHERE id = ?",
            (track[0],),
        )
    conn.commit()

    log.info("Reset %s tracks", len(to_remove))


# ListenBrainz mode (sync from LBZ -> Plex) is being deprecated.
# def lbz_mode():
#     """
#     This function is run when the script is run with `-m lbz`, and syncs
#     loved/hated tracks FROM ListenBrainz TO Plex and LFM
#     """
#     pass


def reset_mode(services: Services):
    """
    Reset all ratings submitted to ListenBrainz or Last.fm
    """
    if services.lbz:
        loves = services.lbz.all_loves()
        log.info("ListenBrainz: %s tracks to unlove", len(loves))
        i = 0
        for track in loves:
            i += 1
            log.info("%s/%s", i, len(loves))
            services.lbz.client.submit_user_feedback(0, track.mbid)
        hates = services.lbz.all_hates()
        log.info("ListenBrainz: %s tracks to unhate", len(hates))
        i = 0
        for track in hates:
            i += 1
            log.info("%s/%s", i, len(hates))
            services.lbz.client.submit_user_feedback(0, track.mbid)

    if services.lfm:
        loves = services.lfm.all_loves()
        log.info("Last.FM: %s tracks to unlove", len(loves))
        i = 0
        for track in loves:
            i += 1
            log.info("%s/%s", i, len(loves))
            services.lfm.reset(track)


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
        choices=[
            "plex",
            # "lbz",  - ListenBrainz mode is being deprecated.
            "reset",
        ],
        required=True,
        help="Mode to run the script in (plex or lbz)",
    )

    args = parser.parse_args()

    match args.mode:
        case "plex":
            return "plex"
        # ListenBrainz mode is being deprecated.
        # case "lbz":
        # return "lbz"
        case "reset":
            return "reset"
        case _:
            log.fatal(f"Unknown mode: {args.mode}", file=sys.stderr)
            sys.exit(1)


def setup_db() -> (sqlite3.Cursor, sqlite3.Connection):
    """
    Database setup function. Creates the tables used by the script if they don't
    exist, and returns a cursor for the database.
    """
    db_conn = sqlite3.connect(DATABASE)
    db_cur = db_conn.cursor()

    db_cur.execute(
        """
CREATE TABLE IF NOT EXISTS loved(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recordingId TEXT,
    trackId TEXT UNIQUE,
    title TEXT,
    artist TEXT
)
       """
    )
    db_cur.execute(
        """
CREATE TABLE IF NOT EXISTS hated(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recordingId TEXT,
    trackId TEXT UNIQUE,
    title TEXT,
    artist TEXT
)
       """
    )
    db_cur.execute(
        """
CREATE TABLE IF NOT EXISTS reset(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recordingId TEXT,
    trackId TEXT UNIQUE,
    title TEXT,
    artist TEXT
)
       """
    )
    return db_cur, db_conn


def setup_lfm() -> Optional[LastFM]:
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
    return lfm


def setup_lbz() -> Optional[ListenBrainz]:
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

    return lbz


def setup() -> Services:
    """
    Sets up Plex object, database, and attempts to set up LastFM and ListenBrainz objects.
    """  # noqa
    cur, conn = setup_db()
    plex = Plex(
        music_library=PLEX_LIBRARY,
        love_threshold=PLEX_LOVE_THRESHOLD,
        hate_threshold=PLEX_HATE_THRESHOLD,
        url=PLEX_URL,
    )
    lfm = setup_lfm()
    lbz = setup_lbz()
    return Services(plex=plex, cursor=cur, conn=conn, lfm=lfm, lbz=lbz)


def main():
    start_time = time.perf_counter()
    log.info("Starting RatingRelay.")
    mode = read_args()
    services = setup()

    match mode:
        case "plex":
            plex_mode(services)
        # Sync from ListenBrainz is being deprecated.
        # case "lbz":
        #     lbz_mode(services)
        case "reset":
            reset_mode(services)

    exec_time = time.perf_counter() - start_time
    log.info("RatingRelay finished in %.2f seconds.", exec_time)


if __name__ == "__main__":
    main()
