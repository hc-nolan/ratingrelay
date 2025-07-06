from dataclasses import dataclass
from typing import Optional
import logging
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


log = logging.getLogger(__name__)
mbz.set_useragent(
    "RatingRelay", "v0.5", contact="https://github.com/hc-nolan/ratingrelay"
)


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    mbid: Optional[str] = None
    track_mbid: Optional[str] = None


class LibraryNotFoundError(Exception):
    """
    Exception class for cases where no matching
    music library is found on the Plex server
    """


class Plex:
    """
    Handles all interaction with Plex server
    :param `url`: URL for the Plex server
    :param `music_library`: Name of the music library
    :param `love_threshold`: Integer representing the rating to consider tracks as 'loved'
    :param `hate_threshold`: (Optional) Integer representing the rating to consider tracks as 'hated'
    """  # noqa

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
        self.token = env.get("PLEX_TOKEN")
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
        env.write_var("PLEX_TOKEN", self.token)

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
        thresh = float(self.love_threshold) - 0.1
        return self.music_library.search(
            libtype="track", filters={"userRating>>=": thresh}
        )

    def get_hated_tracks(self) -> list[PlexTrack]:
        """
        Queries a given library for all tracks meeting the `HATE_THRESHOLD` defined in `.env`
        """  # noqa: E501

        # The Plex <<= filter is "less than", so we add too the defined
        # threshold value to effectively make it "less than or equal to"
        thresh = float(self.hate_threshold) + 0.1
        return self.music_library.search(
            libtype="track", filters={"userRating<<=": thresh}
        )

    def submit_rating(self, track: PlexTrack, rating: int):
        """
        Submit a new track rating to the Plex server.
        """
        return track.rate(rating=rating)


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
            raise RuntimeError(
                "One or more ListenBrainz variables are missing.\n"
                "If you intended to use ListenBrainz, "
                "make sure all environment variables are set."
            )
        self.client = self._connect()

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
            self.client.submit_user_feedback(feedback_value, mbid)
        else:
            log.info("No MBID found. Unable to submit to ListenBrainz.")

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
        """
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

    def _get_track_mbid(self, track: Track) -> str | None:
        """
        Queries MusicBrainz and retrieves matching result
        """
        query = " ".join(str(val) for val in [track.title, track.artist])
        track_search = mbz.search_recordings(
            query=query, artist=track.artist, recording=track.title
        )
        return self._find_mbid_match(track, track_search["recording-list"])

    @staticmethod
    def _find_mbid_match(track: Track, track_search: list[dict]) -> str | None:
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
                return None
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
            val is not None or val != ""
            for val in (self.token, self.secret, self.username, self.password)
        ):
            raise RuntimeError(
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


def to_Track_list(t: list[PlexTrack]) -> list[Track]:
    """
    Converts a list of tracks from Plex into a list of `Track` types

    Args:
        `t`: List containing one or more `plexapi.audio.Track` aka `PlexTrack`
        `lbz`: ListenBrainz class instance

    Returns:
        List of `Track` items corresponding to the `PlexTrack` items
    """  # noqa
    return [try_to_make_Track(tr) for tr in t]


def get_plex_track_mbid(track: PlexTrack) -> Optional[str]:
    """Parses track MBID from a Plex track object"""
    log.info("Trying to grab MBID for %s from PlexTrack.", track.title)
    try:
        mbid = track.guids[0].id
        mbid = mbid.removeprefix("mbid://")  # remove prefix string
        log.info("Found track ID from PlexTrack: %s.", mbid)
    except IndexError:
        mbid = None
        log.warning("No track MBID found in PlexTrack.")

    return mbid


def check_db_for_track(
    cursor: sqlite3.Cursor, track_mbid: str, title: str, artist: str
) -> Optional[str]:
    result = cursor.execute(
        "SELECT title, artist, trackId, recordingId FROM loved WHERE trackId = ?",
        (track_mbid,),
    )
    match = result.fetchone()
    if match:
        return match
    result = cursor.execute(
        "SELECT title, artist, trackId, recordingId FROM loved WHERE title = ? AND artist = ?",
        (
            title,
            artist,
        ),
    )
    match = result.fetchone()
    if match:
        return match

    return None


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
        log.info("No track MBID found. Using title and artist: %s - %s", title, artist)
        search = mbz.search_recordings(query=f"tid:{track_mbid}")
    recording = search.get("recording-list")

    if recording == []:
        log.warning("No recordings found on MusicBrainz.")
        rec_mbid = None
    else:
        log.info("Recording MBID found.")
        rec_mbid = recording[0].get("id")

    return rec_mbid


def make_Track(plex_track: PlexTrack, cursor: sqlite3.Cursor) -> Track:
    """
    Parses the track MBID from a Plex track and returns a Track with the
    matching recording MBID.

    First, queries the database for a match. If no match is found, a query is
    made to the MusicBrainz API to get the recording MBID.
    """
    title = plex_track.title
    artist = plex_track.artist().title
    track_mbid = get_plex_track_mbid(plex_track)

    # The MBID returned by Plex is the track ID. For use with ListenBrainz,
    # we need the recording ID.
    log.info("Checking database for existing track.")
    db_match = check_db_for_track(cursor, track_mbid, title, artist)
    if db_match:
        log.info("Existing track found in database.")
        rec_mbid = db_match[3]
    else:
        rec_mbid = get_recording_mbid(track_mbid=track_mbid, title=title, artist=artist)

    return Track(title=title, artist=artist, mbid=rec_mbid, track_mbid=track_mbid)
