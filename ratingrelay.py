"""
RatingRelay
Usage: python ratingrelay.py -m <mode>
"""

from dataclasses import dataclass
import env
import sqlite3
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from plexapi.library import LibrarySection
from plexapi.audio import Track as PlexTrack
from rich import print
from rich.prompt import IntPrompt, Prompt
import musicbrainzngs as mbz
import liblistenbrainz as liblbz
import pylast
import argparse
import os
import time
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(module)s:%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        TimedRotatingFileHandler(
            "data/ratingrelay.log", when="midnight", interval=30, backupCount=6
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

# Ensure the data directory exists
os.makedirs("data", exist_ok=True)

mbz.set_useragent(
    "RatingRelay", "v0.4", contact="https://github.com/hc-nolan/ratingrelay"
)


class LibraryNotFoundError(Exception):
    """
    Exception class for cases where no matching
    music library is found on the Plex server
    """


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    mbid: Optional[str] = None
    track_mbid: Optional[str] = None

    @staticmethod
    def from_plex(plex_track: PlexTrack, cursor: sqlite3.Cursor, rating: str):
        """
        Parses the track MBID from a Plex track and returns a Track with the
        matching recording MBID.

        First, queries the database for a match. If no match is found, a query is
        made to the MusicBrainz API to get the recording MBID.

        Args:
            plex_track: A PlexAPI Track object
            cursor: Database cursor
            rating: `loved` or `hated`
        """
        title = plex_track.title
        artist = plex_track.artist().title
        track_mbid = Track.get_plex_track_mbid(plex_track)

        # The MBID returned by Plex is the track ID. For use with ListenBrainz,
        # we need the recording ID.
        log.info("Checking database for existing track.")
        db_match = Track.check_db(cursor, track_mbid, title, artist, rating)
        if db_match:
            log.info("Existing track found in database.")
            rec_mbid = db_match[3]
        else:
            rec_mbid = Track.get_recording_mbid(
                track_mbid=track_mbid, title=title, artist=artist
            )

        return Track(title=title, artist=artist, mbid=rec_mbid, track_mbid=track_mbid)

    def get_recording_mbid(
        track_mbid: Optional[str], title: str, artist: str
    ) -> Optional[str]:
        """
        Queries MusicBrainz API for a track's recording MBID.
        """
        log.info("Searching MusicBrainz for recording MBID.")
        if track_mbid is None:
            log.info("Using track MBID: %s", track_mbid)
            search = mbz.search_recordings(query=title, artist=artist)
        else:
            log.info(
                "track_mbid is empty, using title and artist: %s - %s", title, artist
            )
            search = mbz.search_recordings(query=f"tid:{track_mbid}")
        recording = search.get("recording-list")

        if recording == []:
            log.warning("No recordings found on MusicBrainz.")
            rec_mbid = None
        else:
            log.info("Recording MBID found from MusicBrainz search.")
            rec_mbid = recording[0].get("id")

        return rec_mbid

    def get_plex_track_mbid(track: PlexTrack) -> Optional[str]:
        """Parses track MBID from a Plex track object"""
        log.info("Trying to grab MBID from PlexTrack: %s", track.title)
        try:
            mbid = track.guids[0].id
            mbid = mbid.removeprefix("mbid://")
            log.info("Found track ID from PlexTrack: %s.", mbid)
        except IndexError:
            mbid = None
            log.warning("No track MBID found in PlexTrack.")

        return mbid

    def check_db(
        cursor: sqlite3.Cursor, track_mbid: str, title: str, artist: str, table: str
    ) -> Optional[str]:
        """
        Check for a matching track in the database table provided
        """
        match table:
            case "loved":
                tablename = "loved"
            case "hated":
                tablename = "hated"
            case _:
                log.fatal("Unrecognized table name: %s", table)
                raise ValueError(f"Unrecognized table name: {table}")
        result = cursor.execute(
            f"SELECT title, artist, trackId, recordingId FROM {tablename} WHERE trackId = ?",
            (track_mbid,),
        )
        matching_entry = result.fetchone()
        if matching_entry:
            return matching_entry
        result = cursor.execute(
            f"SELECT title, artist, trackId, recordingId FROM {tablename} WHERE title = ? AND artist = ?",
            (
                title,
                artist,
            ),
        )
        matching_entry = result.fetchone()
        if matching_entry:
            return matching_entry
            return None


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


class Relay:
    @staticmethod
    def run(services: Services):
        """
        Runs when the script is executed with `-m plex`; syncs
        loved/hated tracks from Plex to LBZ/LFM.
        """
        log.info("Relaying loved tracks from Plex.")
        love_stats = Relay.loves(**services.__dict__)
        if services.plex.hate_threshold is not None:
            hate_stats = Relay.hates(
                plex=services.plex,
                lbz=services.lbz,
                cursor=services.cursor,
                conn=services.conn,
            )
        else:
            hate_stats = {"plex_hates": 0, "lbz_added": 0}

        Reset.all(
            lbz=services.lbz,
            lfm=services.lfm,
            cursor=services.cursor,
            conn=services.conn,
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

    @staticmethod
    def loves(
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
            track = Track.from_plex(
                plex_track=plex_track, cursor=cursor, rating="loved"
            )
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
                    log.info(
                        "ListenBrainz - New love: %s by %s", track.title, track.artist
                    )
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
        Reset.find_tracks(
            conn=conn, cursor=cursor, plex_tracks=plex_tracks, table="loved"
        )
        return {
            "plex_loves": len(plex_loves),
            "lbz_added": lbz_added,
            "lfm_added": lfm_added,
        }

    @staticmethod
    def hates(
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
            track = Track.from_plex(
                plex_track=plex_track, cursor=cursor, rating="hated"
            )
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

        Reset.find_tracks(
            conn=conn, cursor=cursor, plex_tracks=plex_tracks, table="hated"
        )

        return {"plex_hates": len(plex_hates), "lbz_added": lbz_added}


class Reset:
    @staticmethod
    def find_tracks(
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

    @staticmethod
    def all(
        lbz: Optional[ListenBrainz],
        lfm: Optional[LastFM],
        cursor: sqlite3.Cursor,
        conn: sqlite3.Connection,
    ):
        """
        Reset all tracks that are present in the reset table, meaning they are
        no longer loved.
        """
        log.info("Resetting tracks that are no longer loved/hated on Plex.")
        result = cursor.execute(
            "SELECT id, title, artist, recordingId, trackId FROM RESET"
        )
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


class Setup:
    @staticmethod
    def services() -> Services:
        """
        Sets up Plex object, database, and attempts to set up LastFM and ListenBrainz objects.
        """  # noqa
        cur, conn = Setup.db()
        plex = Plex(
            music_library=PLEX_LIBRARY,
            love_threshold=PLEX_LOVE_THRESHOLD,
            hate_threshold=PLEX_HATE_THRESHOLD,
            url=PLEX_URL,
        )
        lfm = Setup.lfm()
        lbz = Setup.lbz()
        return Services(plex=plex, cursor=cur, conn=conn, lfm=lfm, lbz=lbz)

    @staticmethod
    def db() -> (sqlite3.Cursor, sqlite3.Connection):
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

    @staticmethod
    def lfm() -> Optional[LastFM]:
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

    @staticmethod
    def lbz() -> Optional[ListenBrainz]:
        try:
            lbz = ListenBrainz(username=LBZ_USERNAME, token=LBZ_TOKEN)
        except RuntimeError as e:
            log.error(
                "Got a runtime error when attempting to execute ListenBrainz - skipping ListenBrainz"
            )
            log.error("Error details:")
            log.error(e)
            log.error(
                "This can be safely ignored if you do not wish to use ListenBrainz"
            )
            lbz = None

        return lbz


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
            "relay",
            "reset",
        ],
        default="relay",
        help="Mode to run the script in (plex or lbz)",
    )

    args = parser.parse_args()

    match args.mode:
        case "relay":
            return "relay"
        case "reset":
            return "reset"
        case _:
            log.fatal(f"Unknown mode: {args.mode}", file=sys.stderr)
            sys.exit(1)


def main():
    start_time = time.perf_counter()
    log.info("Starting RatingRelay.")
    mode = read_args()
    services = Setup.services()

    match mode:
        case "relay":
            Relay.run(services)
        case "reset":
            reset_mode(services)

    exec_time = time.perf_counter() - start_time
    log.info("RatingRelay finished in %.2f seconds.", exec_time)


if __name__ == "__main__":
    main()
