"""
RatingRelay
Usage: python ratingrelay.py -m <mode>
"""

from dataclasses import dataclass
from dotenv import load_dotenv
from os import getenv
from pathlib import Path
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

# Ensure the data directory exists
os.makedirs("data", exist_ok=True)

load_dotenv()
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


class Env:
    """
    Contains functions related to interacting with the .env file.

    Mostly wrappers around os.getenv()
    """

    @staticmethod
    def write_var(name: str, value: str) -> None:
        """
        Writes or updates an environment variable
        """
        log.info("Writing new %s to .env file.", name)
        env_file = Env.get_env_file()

        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith(name):
                lines[i] = name + "=" + value + "\n"
                updated = True

        if not updated:
            log.info("No saved %s found. Adding it now.", name)
            # If above did not produce an update,
            # it means no line '<NAME>=' was found; append it
            lines.append("\n" + name + "=" + value + "\n")
            updated = True

        if updated:
            with open(env_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
            log.info("Updated saved %s value.", name)
        else:
            raise IOError(
                f"Unable to write to env file. Cannot continue without {name}.\n"
                f"Please manually add it: {value}"
            )

    @staticmethod
    def get_env_file() -> Path:
        """
        Retrieve the path of the application's .env file
        """
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            log.info("Found .env file at: %s", env_file)
            return env_file
        else:
            raise FileNotFoundError(
                ".env file not found in repository root directory. "
                "Check the usage guide for details on "
                "setting up the environment variables."
            )

    @staticmethod
    def get_required(var_name: str) -> str:
        """
        Wraps os.getenv() - raises an exception if value is not present
        """
        value: Optional[str] = getenv(var_name)
        if not value:
            raise ConfigError(
                f"Environment variable {var_name} is not set. "
                "Please add it and re-run the script."
            )
        return value

    @staticmethod
    def get_required_int(var_name: str) -> int:
        """
        Wraps get_required() - raises an exception if an integer value is not present
        """  # noqa:E501
        value = Env.get_required(var_name)
        return int(value)

    @staticmethod
    def get_required_bool(var_name: str) -> bool:
        """
        Wraps get_required() - raises an exception if a boolean value is not present
        """  # noqa:E501
        value = Env.get_required(var_name)
        return bool(value)

    @staticmethod
    def get(var_name: str) -> Optional[str]:
        """
        Simple wrapper for os.getenv()
        """
        return getenv(var_name)


LFM_USERNAME = Env.get("LASTFM_USERNAME")
LFM_PASSWORD = Env.get("LASTFM_PASSWORD")
LFM_TOKEN = Env.get("LASTFM_API_KEY")
LFM_SECRET = Env.get("LASTFM_SECRET")

LBZ_USERNAME = Env.get("LISTENBRAINZ_USERNAME")
LBZ_TOKEN = Env.get("LISTENBRAINZ_TOKEN")

PLEX_URL = Env.get_required("SERVER_URL")
PLEX_LIBRARY = Env.get_required("MUSIC_LIBRARY")
PLEX_LOVE_THRESHOLD = Env.get_required_int("LOVE_THRESHOLD")
PLEX_HATE_THRESHOLD = Env.get("HATE_THRESHOLD")

DATABASE = Env.get_required("DATABASE")

mbz.set_useragent(
    "RatingRelay", "v1.0", contact="https://github.com/hc-nolan/ratingrelay"
)


class ConfigError(Exception):
    """Raised when required configuration value is missing or invalid"""


class LibraryNotFoundError(Exception):
    """Raised when a matching music library is not found on Plex server"""


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    mbid: Optional[str] = None
    track_mbid: Optional[str] = None


def query_recording_mbid(
    track_mbid: Optional[str], title: str, artist: str
) -> Optional[str]:
    """
    Queries MusicBrainz API for a track's recording MBID.
    """
    log.info("Searching MusicBrainz for recording MBID.")
    if track_mbid is not None:
        log.info("Using track MBID: %s", track_mbid)
        search = mbz.search_recordings(query=title, artist=artist)
    else:
        log.info("track_mbid is empty, using title and artist: %s - %s", title, artist)
        search = mbz.search_recordings(query=f"tid:{track_mbid}")
    recording = search.get("recording-list")

    if recording == []:
        log.warning("No recordings found on MusicBrainz.")
        rec_mbid = None
    else:
        log.info("Recording MBID found from MusicBrainz search.")
        rec_mbid = recording[0].get("id")

    return rec_mbid


class Plex:
    """
    Handles all interaction with Plex server
    :param `url`: URL for the Plex server
    :param `music_library`: Name of the music library
    :param `love_threshold`: Integer representing the rating to consider tracks as 'loved'
    :param `hate_threshold`: (Optional) Integer representing the rating to consider tracks as 'hated'
    """  # noqa

    _RATING_OFFSET = 0.1

    def __init__(
        self, url: str, music_library: str, love_threshold: int, hate_threshold: int = 0
    ):
        self.url = url
        self.love_threshold = float(love_threshold)
        try:
            hate = float(hate_threshold)
        except TypeError:
            hate = None
        self.hate_threshold = hate
        self.token = Env.get("PLEX_TOKEN")
        self._verify_auth()
        self.music_library = self._get_music_library(music_library)

    def _verify_auth(self):
        """
        Checks if self.token is valid for authenticating to Plex. If token is not valid,
        proceeds with interactive authentication to retrieve a new token.
        """  # noqa: E501
        if not self.token:
            log.info(
                "No saved PLEX_TOKEN found. Proceeding with manual authentication."
            )
            self._manual_auth()
        if self._is_token_valid():
            log.info("PLEX_TOKEN is valid. Authenticated with Plex.")
        else:
            log.info("Saved PLEX_TOKEN is no longer valid. Please re-authenticate.")
            self._manual_auth()

    def _manual_auth(self):
        """
        Handles the manual authentication process. Retrieves the valid token after
        authentication for future use.
        """  # noqa: E501
        print(
            "Please enter your Plex authentication details. "
            "This should only be required the first time the program is run."
        )

        plex_server = Prompt.ask("Plex server name")
        plex_username = Prompt.ask("Plex username")
        plex_password = Prompt.ask("Plex password (input hidden)", password=True)
        plex_code = IntPrompt.ask(
            "Plex MFA code (leave blank if not using MFA)", default=0
        )

        account = MyPlexAccount(
            username=plex_username, password=plex_password, code=str(plex_code)
        )
        plex = account.resource(plex_server).connect()
        self.server = plex

        self.token = plex._token
        Env.write_var("PLEX_TOKEN", self.token)

    def _is_token_valid(self) -> bool:
        """
        Checks if self.token is valid for authenticating to the Plex server
        """
        log.info("Checking if PLEX_TOKEN is still valid for authentication.")
        try:
            self.server = PlexServer(self.url, self.token)
            return True
        except Exception as e:
            log.error(e)
            return False

    def _get_music_library(self, library_name: str) -> LibrarySection:
        """
        Returns the LibrarySection matching the given library_name.
        Raises plexapi.exceptions.NotFound if no matching library exists.
        """
        return self.server.library.section(library_name)

    def get_loved_tracks(self) -> list[PlexTrack]:
        """
        Queries a given library for all tracks meeting the `LOVE_THRESHOLD` defined in `.env`
        """  # noqa: E501

        # The Plex >>= filter is "greater than", so we subtract from the defined
        # threshold value to effectively make it "greater than or equal to"
        thresh = float(self.love_threshold) - self._RATING_OFFSET
        return self.music_library.search(
            libtype="track", filters={"userRating>>=": thresh}
        )

    def get_hated_tracks(self) -> list[PlexTrack]:
        """
        Queries a given library for all tracks meeting the `HATE_THRESHOLD` defined in `.env`
        """  # noqa: E501

        # The Plex <<= filter is "less than", so we add too the defined
        # threshold value to effectively make it "less than or equal to"
        thresh = float(self.hate_threshold) + self._RATING_OFFSET
        return self.music_library.search(
            libtype="track", filters={"userRating<<=": thresh}
        )

    def submit_rating(self, track: PlexTrack, rating: int):
        """
        Submit a new track rating to the Plex server.
        """
        return track.rate(rating=rating)

    @staticmethod
    def parse_track_mbid(track: PlexTrack) -> Optional[str]:
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


class ListenBrainz:
    """
    Handles all ListenBrainz operations
    """

    def __init__(self, token: str | None, username: str | None):
        self.token = token
        self.username = username
        if not all(
            val is not None and val != "" for val in (self.token, self.username)
        ):
            raise ConfigError(
                "One or more ListenBrainz variables are missing.\n"
                "If you intended to use ListenBrainz, "
                "make sure all environment variables are set."
            )
        self.client = self._connect()
        self.min_delay: Optional[float] = 0.5
        self.last_req_time: Optional[float] = None

    def __str__(self):
        return "ListenBrainz"

    def _connect(self) -> liblbz.ListenBrainz:
        """
        Creates a connection to ListenBrainz
        :return None if any environment variables are missing,
        the ListenBrainz client otherwise
        """
        client = liblbz.ListenBrainz()
        client.set_auth_token(self.token)
        log.info("Checking ListenBrainz API token validity.")
        client.is_token_valid(self.token)
        log.info(
            "ListenBrainz API token is valid; successfully connected to ListenBrainz."
        )
        return client

    def _handle_feedback(self, feedback: str, track: Track):
        """
        Handler method for `love()` and `hate()`. Submits track feedback to
        Listenbrainz.

        Args:
            `feedback`: One of the following strings: `love`, `hate`
        """
        if feedback == "love":
            log_str = "Loving"
            feedback_value = 1
        elif feedback == "hate":
            log_str = "Hating"
            feedback_value = -1
        else:
            raise ValueError(
                f"Feedback value must be 'love' or 'hate' - got {feedback}"
            )

        if track.mbid is None:
            log.info(
                "%s: %s by %s - Checking for track MBID",
                log_str,
                track.title,
                track.artist,
            )
            mbid = self._get_track_mbid(track)
        else:
            mbid = track.mbid

        if mbid:
            log.info("MBID found. Submitting %s to ListenBrainz.", mbid)
            self._wait_if_needed()
            self.client.submit_user_feedback(feedback_value, mbid)
            self.last_req_time = time.time()
        else:
            log.warning("No MBID found. Unable to submit to ListenBrainz: %s", track)

    def _wait_if_needed(self):
        """
        Used to enforce rate limiting; ensures minimum delay between subsequent
        requests.
        """
        if self.last_req_time is not None:
            last = time.time() - self.last_req_time
            if last < self.min_delay:
                log.info("Waiting...")
                time.sleep(self.min_delay - last)

    def reset(self, track: Track):
        """
        Reset a track's ListenBrainz rating to 0.
        """
        log.info("ListenBrainz - resetting track: %s", track)
        self.client.submit_user_feedback(0, track.mbid)

    def love(self, track: Track):
        """
        Love a track on ListenBrainz.
        """
        self._handle_feedback(feedback="love", track=track)

    def hate(self, track: Track):
        """
        Hate a track on ListenBrainz.
        """
        self._handle_feedback(feedback="hate", track=track)

    def _new(self, rating: str, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved/hated
        ListenBrainz tracks; returns the tracks that have not yet been loved/hated

        Args:
            `rating`: "love" or "hate"
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items not in `self.loves`/`self.hates`
        """
        if rating == "love":
            lbz_tracks = self.loves
        elif rating == "hate":
            lbz_tracks = self.hates
        else:
            raise ValueError(
                f"Invalid rating type '{rating}' - valid types are 'love' and 'hate'"
            )

        old = {t.mbid for t in lbz_tracks}
        new = [track for track in track_list if track.mbid not in old]

        return new

    def _old(self, rating: str, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved/hated
        ListenBrainz tracks; returns the tracks that have been loved/hated
        on ListenBrainz but no longer meet the rating threshold

        Args:
            `rating`: "love" or "hate"
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items in `self.loves`/`self.hates` but not in `track_list`
        """  # noqa
        if rating == "love":
            lbz_tracks = self.loves
        elif rating == "hate":
            lbz_tracks = self.hates
        else:
            raise ValueError(
                f"Invalid rating type '{rating}' - valid types are 'love' and 'hate'"
            )

        plex_current = {t.mbid for t in track_list}
        lbz_outdated = [track for track in lbz_tracks if track.mbid not in plex_current]

        return lbz_outdated

    def new_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved ListenBrainz
        tracks; returns the tracks that have not yet been loved

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items not in `self.loves`
        """
        return self._new(rating="love", track_list=track_list)

    def new_hates(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already hated ListenBrainz
        tracks; returns the tracks that have not yet been hated

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items not in `self.hates`
        """
        return self._new(rating="hate", track_list=track_list)

    def old_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved ListenBrainz
        tracks; returns the tracks that are loved on ListenBrainz but no longer
        meet the rating threshold on Plex.

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `self.loves` containing items not in `track_list`
        """
        return self._old(rating="love", track_list=track_list)

    def old_hates(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already hated ListenBrainz
        tracks; returns the tracks that are hated on ListenBrainz but no longer
        meet the rating threshold on Plex.

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `self.hates` containing items not in `track_list`
        """
        return self._old(rating="hate", track_list=track_list)

    def all_hates(self) -> set[Track]:
        """
        Retrieve all tracks the user has already hated
        """
        self.hates = self._get_all_feedback(score=-1)
        return self.hates

    def all_loves(self) -> set[Track]:
        """
        Retrieve all tracks the user has already loved
        """
        self.loves = self._get_all_feedback(score=1)
        return self.loves

    def _get_all_feedback(self, score: int):
        """
        Retrieve all tracks the user has submitted feedback for

        Args:
            `score`: Integer representing the user feedback; `1` for love, `-1` for hate.

        Returns:
            `set` of all tracks found on ListenBrainz account
        """
        all_loves = set()
        offset = 0
        count = 100

        while True:
            user_loves = self.client.get_user_feedback(
                username=self.username,
                score=score,
                count=count,
                offset=offset,
                metadata=True,
            )
            user_loves = user_loves.get("feedback")
            for track in user_loves:
                try:
                    mbid = track.get("recording_mbid")
                    metadata = track.get("track_metadata")
                    title = metadata.get("track_name")
                    artist = metadata.get("artist_name")
                    track_tuple = Track(title=title, artist=artist, mbid=mbid)
                    all_loves.add(track_tuple)
                except TypeError:
                    log.warning(
                        "Malformed data in response from MusicBrainz; "
                        "track title and/or artist unavailable for %s",
                        track["recording_mbid"],
                    )

            if len(user_loves) < count:
                break  # No more feedback to fetch
            offset += count
        return all_loves

    def _get_track_mbid(self, track: Track) -> Optional[str]:
        """
        Queries MusicBrainz and retrieves matching result
        """
        query = " ".join(val for val in [track.title, track.artist])
        track_search = mbz.search_recordings(
            query=query, artist=track.artist, recording=track.title
        )
        return self._find_mbid_match(track, track_search["recording-list"])

    @staticmethod
    def _find_mbid_match(track: Track, track_search: list[dict]) -> Optional[str]:
        """
        Attempts to find a matching MBID given a track dict
        and MusicBrainz search results
        """
        track_artist = track.artist.lower()
        track_title = track.title.lower()
        for result in track_search:
            # find matching title+artist pair
            try:
                candidate_title = result.get("title").lower()

                candidate_artist = result["artist-credit"][0].get("name").lower()
                if track_title == candidate_title and track_artist == candidate_artist:
                    mbid = result["id"]
                    return mbid
            except (IndexError, KeyError, TypeError):
                # These exceptions mean the MBID is missing
                continue
        return None


class LastFM:
    """
    Class for all LastFM-related operations
    """

    def __init__(
        self,
        username: str | None,
        password: str | None,
        token: str | None,
        secret: str | None,
    ):
        self.username = username
        self.password = password
        self.token = token
        self.secret = secret
        self.client = self._connect()
        self.new_love_count = 0

    def __str__(self):
        return "LastFM"

    def _connect(self) -> pylast.LastFMNetwork:
        """
        Creates a connection to Last.fm using pylast
        """
        if not all(
            val is not None and val != ""
            for val in (self.token, self.secret, self.username, self.password)
        ):
            raise ConfigError(
                "One or more Last.fm environment variables are missing.\n"
                "If you intended to use Last.fm, make sure all environment variables are set."
            )
        return pylast.LastFMNetwork(
            api_key=self.token,
            api_secret=self.secret,
            username=self.username,
            password_hash=pylast.md5(self.password),
        )

    def love(self, track: Track):
        """
        Loves a single track
        """
        # log.info("Loving: %s by %s", track.title, track.artist)
        lastfm_track = self.client.get_track(track.artist, track.title)
        lastfm_track.love()
        self.new_love_count += 1

    def reset(self, track: Track):
        """
        Un-loves a single track
        """
        log.info("Last.FM - resetting track: %s", track)
        lastfm_track = self.client.get_track(track.artist, track.title)
        lastfm_track.unlove()

    def new_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex above the love threshold to
        the user's already loved Last.fm tracks

        Args:
            track_list: List of Tracks from Plex to compare against

        Returns:
            list[Track]: List of Tracks that have not been loved yet
        """
        track_list.sort(key=lambda track: track.title)
        # grab tracks user has already loved
        log.info("Grabbing all currently loved tracks from Last.fm.")
        old_loves = self.all_loves()
        # parse into more usable list to match track_list
        old_loves = {(t.title.lower(), t.artist.lower()) for t in old_loves}
        new = [
            track
            for track in track_list
            if (track.title.lower(), track.artist.lower()) not in old_loves
        ]
        log.info("Found %s new tracks to submit to Last.fm.", len(new))
        return new

    def all_loves(self) -> list[Track]:
        """
        Return all currently loved tracks
        """
        track_generator = self.client.get_user(self.username).get_loved_tracks(
            limit=None
        )
        loves = [
            Track(title=t.track.title, artist=t.track.artist.name)
            for t in track_generator
        ]
        return loves


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DATABASE)
        self.cursor = self.conn.cursor()
        self.create_tables()

    @staticmethod
    def _validate_table_name(table: str) -> Optional[str]:
        """
        Used to validate that a variable contains a valid table name.
        Only strings returned by this function are passed to the database
        as table names.
        """
        match table:
            case "loved":
                tablename = "loved"
            case "hated":
                tablename = "hated"
            case "reset":
                tablename = "reset"
            case _:
                raise ValueError(f"Unrecognized table name: {table}")
        return tablename

    def create_tables(self):
        self.cursor.execute(
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
        self.cursor.execute(
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
        self.cursor.execute(
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
        self.conn.commit()

    def add_track(
        self,
        title: Optional[str],
        artist: Optional[str],
        track_mbid: Optional[str],
        rec_mbid: Optional[str],
        table: str,
    ):
        """
        Add a track to the database, or ignore if one already exists.
        """
        tablename = self._validate_table_name(table)
        self.cursor.execute(
            f"INSERT OR IGNORE INTO {tablename} (title, artist, trackId, recordingId) VALUES(?, ?, ?, ?)",
            (title, artist, track_mbid, rec_mbid),
        )
        self.conn.commit()

    def delete_by_rec_id(
        self,
        rec_mbid: str,
        table: str,
    ):
        """Delete a track by its recording MBID"""
        tablename = self._validate_table_name(table)
        self.cursor.execute(
            f"DELETE FROM {tablename} WHERE recordingId = ?", (rec_mbid,)
        )
        self.conn.commit()

    def delete_by_id(
        self,
        db_id: int,
        table: str,
    ):
        """Delete a track by its ID (primary key)"""
        tablename = self._validate_table_name(table)
        self.cursor.execute(f"DELETE FROM {tablename} WHERE ID = ?", (db_id,))
        self.conn.commit()

    def query_track(
        self, track_mbid: str, title: str, artist: str, table: str
    ) -> Optional[dict]:
        """
        Check for a matching track in the database table provided
        """
        tablename = self._validate_table_name(table)
        query = f"""
            SELECT id, title, artist, trackId, recordingId
            FROM {tablename}
            WHERE trackId = ? OR (title = ? AND artist = ?)
        """
        result = self.cursor.execute(query, (track_mbid, title, artist))
        matching_entry = result.fetchone()
        return self._make_dict(matching_entry) if matching_entry else None

    def _make_dict(self, db_entry: tuple) -> dict:
        """Turn a tuple from the database into a dict where column names are keys"""

        return {
            "id": db_entry[0],
            "title": db_entry[1],
            "artist": db_entry[2],
            "track_mbid": db_entry[3],
            "rec_mbid": db_entry[4],
        }

    def get_all_tracks(self, table: str) -> list[dict]:
        tablename = self._validate_table_name(table)
        result = self.cursor.execute(
            f"SELECT id, title, artist, trackId, recordingId FROM {tablename}"
        )

        entries = result.fetchall()
        formatted = [self._make_dict(t) for t in entries]
        return formatted


def track_from_plex(plex_track: PlexTrack, db: Database, rating: str) -> Track:
    """
    Parses the track MBID from a Plex track and returns a Track with the
    matching recording MBID.

    First, queries the database for a match. If no match is found, a query is
    made to the MusicBrainz API to get the recording MBID.

    Args:
        plex_track: A PlexAPI Track object
        db: Database class instance
        rating: `loved` or `hated`
    """
    title = plex_track.title
    artist = plex_track.artist().title
    track_mbid = Plex.parse_track_mbid(plex_track)

    # The MBID returned by Plex is the track ID. For use with ListenBrainz,
    # we need the recording ID.
    log.info("Checking database for existing track.")
    db_match = db.query_track(
        track_mbid=track_mbid, title=title, artist=artist, table=rating
    )
    if db_match:
        log.info("Existing track found in database.")
        rec_mbid = db_match.get("rec_mbid")
    else:
        rec_mbid = query_recording_mbid(
            track_mbid=track_mbid, title=title, artist=artist
        )
        if rec_mbid is None:
            log.warning(
                "No recording MBID returned by MusicBrainz for: %s",
                (
                    title,
                    artist,
                ),
            )

    return Track(title=title, artist=artist, mbid=rec_mbid, track_mbid=track_mbid)


@dataclass
class Services:
    plex: Plex
    db: Database
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
                plex=services.plex, lbz=services.lbz, db=services.db
            )
        else:
            hate_stats = {"plex_hates": 0, "lbz_added": 0}

        Reset.all(lbz=services.lbz, lfm=services.lfm, db=services.db)
        Relay.print_stats(love=love_stats, hate=hate_stats)

    @staticmethod
    def print_stats(love: dict, hate: dict):
        log.info("STATISTICS:")
        log.info(
            "%-12s\tLoves: %-10s\tHates: %-10s",
            "Plex:",
            love.get("plex_loves"),
            hate.get("plex_hates"),
        )
        log.info("ADDITIONS:")
        log.info(
            "%-12s\tLoves: %-10s\tHates: %-10s\t",
            "ListenBrainz:",
            love.get("lbz_added"),
            hate.get("lbz_added"),
        )
        log.info(
            "%-12s\tLoves: %-10s\tHates: %-10s\t",
            "Last.FM:",
            love.get("lfm_added"),
            "N/A",
        )

    @staticmethod
    def loves(
        plex: Plex,
        lbz: ListenBrainz,
        lfm: LastFM,
        db: Database,
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
            track = track_from_plex(plex_track=plex_track, db=db, rating="loved")
            plex_tracks.add(track)
            db.add_track(
                title=track.title,
                artist=track.artist,
                track_mbid=track.track_mbid,
                rec_mbid=track.mbid,
                table="loved",
            )

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
        Reset.find_tracks(db=db, plex_tracks=plex_tracks, table="loved")
        return {
            "plex_loves": len(plex_loves),
            "lbz_added": lbz_added,
            "lfm_added": lfm_added,
        }

    @staticmethod
    def hates(plex: Plex, lbz: ListenBrainz, db: Database):
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
            track = track_from_plex(plex_track=plex_track, db=db, rating="hated")
            plex_tracks.add(track)
            # insert the track if it's new, or ignore if there is a matching
            # recording MBID in the database
            db.add_track(
                title=track.title,
                artist=track.artist,
                track_mbid=track.track_mbid,
                rec_mbid=track.mbid,
                table="hated",
            )

            if track.mbid not in lbz_hated_mbids:
                log.info("Hating %s by %s", track.title, track.artist)
                lbz.hate(track)
                lbz_added += 1

        log.info("Finished adding hates:   ListenBrainz: %s", lbz_added)

        Reset.find_tracks(db=db, plex_tracks=plex_tracks, table="hated")

        return {"plex_hates": len(plex_hates), "lbz_added": lbz_added}


class Reset:
    @staticmethod
    def find_tracks(
        db: Database,
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
        entries = db.get_all_tracks(table=table)
        plex_ids = [track.mbid for track in plex_tracks]

        for track in entries:
            if track.get("rec_mbid") not in plex_ids:
                log.info(
                    "Track no longer %sd on Plex: %s",
                    table,
                    (track.get("title"), track.get("artist")),
                )
                # move from current table to reset table
                db.delete_by_rec_id(rec_mbid=track.get("rec_mbid"), table=table)
                db.add_track(
                    title=track.get("title"),
                    artist=track.get("artist"),
                    track_mbid=track.get("track_mbid"),
                    rec_mbid=track.get("rec_mbid"),
                    table="reset",
                )

    @staticmethod
    def all(
        lbz: Optional[ListenBrainz],
        lfm: Optional[LastFM],
        db: Database,
    ):
        """
        Reset all tracks that are present in the reset table, meaning they are
        no longer loved.
        """
        log.info("Resetting tracks that are no longer loved/hated on Plex.")
        to_remove = db.get_all_tracks(table="reset")

        if lbz:
            for db_track in to_remove:
                track = Track(
                    title=db_track.get("title"),
                    artist=db_track.get("artist"),
                    mbid=db_track.get("rec_mbid"),
                    track_mbid=db_track.get("track_mbid"),
                )
                log.info("Removing %s", track)
                if db_track.get("rec_mbid") is not None:
                    lbz.reset(track)
                else:
                    log.warning(
                        "No recording MBID returned, unable to reset track on ListenBrainz: %s",
                        track,
                    )

        if lfm:
            for db_track in to_remove:
                lfm.reset(
                    Track(title=db_track.get("title"), artist=db_track.get("artist"))
                )

        # Now that tracks have been reset, remove them from the 'reset' table
        for db_track in to_remove:
            db.delete_by_id(db_id=db_track.get("id"), table="reset")

        log.info("Reset %s tracks", len(to_remove))


class Setup:
    @staticmethod
    def services() -> Services:
        """
        Sets up Plex object, database, and attempts to set up LastFM and ListenBrainz objects.
        """  # noqa
        db = Database()
        plex = Plex(
            music_library=PLEX_LIBRARY,
            love_threshold=PLEX_LOVE_THRESHOLD,
            hate_threshold=PLEX_HATE_THRESHOLD,
            url=PLEX_URL,
        )
        lfm = Setup.lfm()
        lbz = Setup.lbz()
        return Services(plex=plex, db=db, lfm=lfm, lbz=lbz)

    @staticmethod
    def lfm() -> Optional[LastFM]:
        try:
            lfm = LastFM(
                username=LFM_USERNAME,
                password=LFM_PASSWORD,
                token=LFM_TOKEN,
                secret=LFM_SECRET,
            )
        except ConfigError as e:
            log.warning(
                "Got config error when attempting to execute Last.fm - skipping Last.fm"
            )
            log.warning("Error details:")
            log.warning(e)
            log.warning("This can be safely ignored if you do not wish to use Last.fm")
            lfm = None
        return lfm

    @staticmethod
    def lbz() -> Optional[ListenBrainz]:
        try:
            lbz = ListenBrainz(username=LBZ_USERNAME, token=LBZ_TOKEN)
        except ConfigError as e:
            log.error(
                "Got config error when attempting to execute ListenBrainz - "
                "skipping ListenBrainz"
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
