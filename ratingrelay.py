"""
ratingrelay - a script to sync Plex tracks rated above a certain threshold
to external services like Last.fm and ListenBrainz
"""
import time
import json
import logging
import sys
import time
from os import getenv
from urllib.parse import urlencode
from uuid import uuid4
import musicbrainzngs as mbz
import liblistenbrainz as liblbz
import liblistenbrainz.errors as lbz_errors
import pylast
import requests
import xmltodict
from dotenv import load_dotenv


def generate_uuid():
    """
    Generates a random UUID and writes it to .env CID variable
    Called during startup if no existing CID is found
    """
    uuid = str(uuid4())

    with open(".env", "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        if line.startswith("CID"):
            lines[i] = "CID=" + uuid + "\n"
            updated = True

    if not updated:
        # If above did not produce an update, it means no line 'CID=' was found; append it
        # First, add newline to the last line of env file
        lines[-1] += "\n"
        lines.append("CID=" + uuid)
        updated = True

    if updated:
        with open(".env", "w", encoding="utf-8") as f:
            f.writelines(lines)
        log.info("Saved new CID to .env file.")
    else:
        log.error(
            "Unable to write a new CID to .env file. "
            "Please generate a random uuid4 and add it manually."
        )
        sys.exit(1)


log = logging.getLogger(__name__)

load_dotenv()
CID = getenv("CID")
MISSING = False
if CID is None or CID == "":
    log.warning("No CID found in .env file; generating and writing a new one.")
    generate_uuid()


LBZ_USERNAME = getenv("LISTENBRAINZ_USERNAME")

SERVER_URL = getenv("SERVER_URL")
if SERVER_URL is None or SERVER_URL == "":
    log.error(
        "Server URL not found. Please add it to .env. Format: http(s)://ip.or.domain:port"
    )
    MISSING = True

MUSIC_LIBRARY = getenv("MUSIC_LIBRARY")
if MUSIC_LIBRARY is None or MUSIC_LIBRARY == "":
    log.error("Music library name not found. Please add it to .env")
    MISSING = True

RATING_THRESHOLD = getenv("RATING_THRESHOLD")
if RATING_THRESHOLD is None or RATING_THRESHOLD == "":
    log.error("Rating threshold not found. Please add it to .env")
    MISSING = True

if MISSING:
    sys.exit(1)

# Globals to store count of newly loved tracks
NEW_LOVES_LFM = 0
NEW_LOVES_LBZ = 0

class LibraryNotFoundError(Exception):
    """
    Exception class for cases where no matching
    music library is found on the Plex server
    """


def main():
    start = time.time()

    logging.basicConfig(
        level=logging.INFO,
        # format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    logging.getLogger("pylast").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    plex = Plex()
    token = plex.auth()
    log.info("Querying Plex for tracks meeting the rating threshold.")

    try:
        library = plex.get_music_library(token)
    except LibraryNotFoundError as e:
        log.fatal(str(e))
        sys.exit(1)

    tracks = plex.get_tracks(library, token)
    tracks = trim_tracks(tracks)
    log.info("Found %s tracks meeting rating threshold.", len(tracks))

    lastfm = LastFM.connect()
    new_loves = LastFM.new_loves(track_list=tracks, client=lastfm)
    LastFM.love(network=lastfm, tracks_to_love=new_loves)

    lbz = lbz_connect()
    if lbz:
        lbz_love(lb=lbz, tracks_to_love=tracks)

    exec_time = time.time() - start
    log.info(
        "SUMMARY:\tExecution took %s seconds\n"
        "- Plex tracks meeting rating threshold: %s\n"
        "- Last.fm newly loved tracks: %s\n"
        "- ListenBrainz newly loved tracks: %s",
        exec_time, len(tracks), NEW_LOVES_LFM, NEW_LOVES_LBZ
    )



def trim_tracks(track_list: list) -> list:
    """"
    Filters list of track dictionaries returned by Plex; retains only artist, title, label, and year
    """
    # Convert each item to frozenset for hashability so we can get unique tracks
    unique_tracks = set()
    for track in track_list:
        try:
            label = track["@parentStudio"]
        except KeyError:
            label = None
        try:
            year = track["@parentYear"]
        except KeyError:
            year = None
        track_items = frozenset({
            "title": track["@title"],
            "artist": track["@grandparentTitle"],
            "label": label,
            "year": year
        }.items())
        unique_tracks.add(track_items)
    # Convert back to list
    return [dict(track) for track in unique_tracks]


class Plex:
    """
    Class for all Plex-related operations
    """
    def __init__(self):
        self.app_name = "ratingrelay"

    def auth(self) -> str:
        """
        Handles full authentication process
        """
        log.info("Authenticating with Plex.")
        # Check for existing token
        existing_token = getenv("PLEX_TOKEN")
        if existing_token:
            valid = self.check_token_validity(existing_token)
            if not valid:
                log.warning("Stored Plex API token has expired. Please reauthenticate.")
                return self.new_auth()

            log.info("Stored Plex API token is still valid.\n")
            return existing_token

        log.warning("No stored Plex API token found.")
        return self.new_auth()

    def new_auth(self) -> str:
        """
        Initial auth process
        Ref: https://forums.plex.tv/t/authenticating-with-plex/609370
        """
        # Generate PIN
        resp = requests.post(
            url="https://plex.tv/api/v2/pins",
            data={
                "strong": "true",
                "X-Plex-Product": self.app_name,
                "X-Plex-Client-Identifier": CID,
            },
            headers={"accept": "application/json"},
            timeout=30
        )
        content = json.loads(resp.content)
        # Grab PIN ID and code
        pin_id = content["id"]
        pin_code = content["code"]

        # Construct auth URL; user has to open in browser
        params = {
            "clientID": CID,
            "code": pin_code,
            "context[device][product]": self.app_name,
        }
        url = "https://app.plex.tv/auth#?" + urlencode(params)
        log.info("To sign in, please open the below URL in a web browser:")
        log.info(url)

        # Poll the ID each second to determine if user has authed
        auth = None
        while auth is None:
            resp = requests.get(
                url=f"https://plex.tv/api/v2/pins/{pin_id}",
                headers={"accept": "application/json"},
                data={"code": pin_code, "X-Plex-Client-Identifier": CID},
                timeout=30
            )
            content = json.loads(resp.content)
            if content["authToken"] is not None:
                log.info("Authentication succeeded.")
                auth = content["authToken"]
                self.write_env_token(auth)
            # User has not completed auth flow; Sleep for 1s and retry
            time.sleep(1)
        return auth

    def check_token_validity(self, token_to_check: str) -> bool:
        """
        Check if Plex API token is still valid
        """
        resp = requests.get(
            url="https://plex.tv/api/v2/user",
            headers={"accept": "application/json"},
            data={

                "X-Plex-Product": self.app_name,
                "X-Plex-Client-Identifier": CID,
                "X-Plex-Token": token_to_check,
            },
            timeout=30
        )
        if resp.status_code == 200:
            return True
        return False

    @staticmethod
    def write_env_token(token_to_write: str) -> None:
        """
        Writes a valid Plex API token to .env file's PLEX_TOKEN variable
        """
        with open(".env", "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith("PLEX_TOKEN"):
                lines[i] = "PLEX_TOKEN=" + token_to_write
                updated = True

        if not updated:
            # If above did not produce an update, it means no line 'TOKEN=' was found; append it
            lines.append("PLEX_TOKEN=" + token_to_write)
            updated = True

        if updated:
            with open(".env", "w", encoding="utf-8") as f:
                f.writelines(lines)
            log.info("Token saved to .env file.")
        else:
            log.fatal(
                "Unable to write Plex API token to file. Cannot continue without Plex API token.\n"
                "Please manually add it:\tPLEX_TOKEN=%s", token_to_write
            )
            sys.exit(1)

    @staticmethod
    def get_music_library(auth_token: str) -> str:
        """
        Searches for music library matching value of MUSIC_LIBRARY .env variable
        :param auth_token: Plex API token
        :return: The Plex music library, if found
        """
        libraries_resp = requests.get(
            url=f"{SERVER_URL}/library/sections",
            params={"X-Plex-Token": auth_token},
            timeout=30
        )
        libraries_resp = xmltodict.parse(libraries_resp.content)
        libraries = libraries_resp["MediaContainer"]["Directory"]
        for lib in libraries:
            if lib["@title"] == MUSIC_LIBRARY:
                return lib["@key"]

        libraries_found = [lib["@title"] for lib in libraries]
        raise LibraryNotFoundError(
            f"No library named '{MUSIC_LIBRARY}' found on Plex Server. "
            f"Please ensure this matches the library name exactly. "
            f"Libraries found: {libraries_found}"
        )

    @staticmethod
    def get_tracks(library_key, auth_token) -> list:
        """
        Queries a given library for all tracks meeting the RATING_THRESHOLD defined in .env
        :param library_key: Key for the Music library to query; returned by get_music_library()
        :param auth_token: X-Plex-Token; returned by Plex.auth()
        :return: List of all tracks meeting the rating threshold
        """
        url = f"{SERVER_URL}/library/sections/{library_key}/all"
        params = {
            "X-Plex-Token": auth_token,
            "type": 10,
            "userRating>": RATING_THRESHOLD
        }
        r = requests.get(
            url=url,
            params=params,
            timeout=30
        )
        response_dict = xmltodict.parse(r.content)
        return response_dict["MediaContainer"]["Track"]


class LastFM:
    """
    Class for all LastFM-related operations
    """
    connected = False

    @classmethod
    def connect(cls) -> pylast.LastFMNetwork | None:
        """
        Creates a connection to Last.fm using pylast
        :return: None if any environment variables are missing; the LastFMNetwork instance otherwise
        """
        key = getenv("LASTFM_API_KEY")
        secret = getenv("LASTFM_SECRET")
        username = getenv("LASTFM_USERNAME")
        password = getenv("LASTFM_PASSWORD")

        if not all(val is not None or val != "" for val in (key, secret, username, password)):
            log.warning(
                "SKIPPING LAST.FM: One or more Last.fm environment variables are missing.\n"
                "If you intended to use Last.fm, make sure all environment variables are set."
            )
            return None

        cls.connected = True
        return pylast.LastFMNetwork(
            api_key=key,
            api_secret=secret,
            username=username,
            password_hash=pylast.md5(password)
        )

    @classmethod
    def love(cls, network: pylast.LastFMNetwork, tracks_to_love: list):
        """
        Iterates through tracks returned from Plex and submits them as Loved Tracks to Last.fm
        :param network: LastFMNetwork returned from lastfm_connect()
        :param tracks_to_love: List of tracks meeting the defined
                                RATING_THRESHOLD returned by get_tracks()
        """
        global NEW_LOVES_LFM

        if not cls.connected:
            log.warning("Not connected to Last.fm - no tracks will be loved.")
            return

        for track in tracks_to_love:
            track_title = track["title"]
            track_artist = track["artist"]
            lastfm_track = network.get_track(track_artist, track_title)
            lastfm_track.love()
            log.info("Last.FM -- Loved: %s - %s", track_artist, track_title)
            NEW_LOVES_LFM += 1

    @staticmethod
    def new_loves(track_list: list[dict], client: pylast.LastFMNetwork) -> list[dict]:
        """
        Compares the list of tracks from Plex above the rating threshold to
        the user's already loved Last.fm tracks
        :param track_list: List of tracks returned from Plex that meet the rating threshold
        :param client: LastFMNetwork instance
        :return: List of tracks the user has not yet loved
        """
        log.info("Comparing track list to existing Last.fm loved tracks.")
        track_list.sort(key=lambda track: track["title"])
        # grab tracks user has already loved
        old_loves = client.get_user(client.username).get_loved_tracks(limit=None)
        # parse into more usable list to match track_list
        old_loves = {
            (
                t.track.title.lower(),
                t.track.artist.name.lower()
            )
            for t in old_loves
        }
        new_loves = [
            track for track in track_list
            if (track["title"].lower(), track["artist"].lower())  not in old_loves
        ]
        log.info(
            "Found %s tracks from Plex; Found %s loved tracks from Last.fm; Found %s new tracks to love",
            len(track_list), len(old_loves), len(new_loves)
        )
        return new_loves



def lbz_connect() -> liblbz.ListenBrainz | None:
    """
    Creates a connection to ListenBrainz
    :return None if any environment variables are missing,
    the ListenBrainz client otherwise
    """
    token = getenv("LISTENBRAINZ_TOKEN")
    username = LBZ_USERNAME
    if not all(val is not None and val != "" for val in (token, LBZ_USERNAME)):
        log.warning(
            "SKIPPING LISTENBRAINZ: One or more ListenBrainz variables are missing.\n"
            "If you intended to use ListenBrainz, make sure all environment variables are set."
        )
        return None

    client = liblbz.ListenBrainz()
    client.set_auth_token(token)
    try:
        client.is_token_valid(token)
    except lbz_errors.ListenBrainzAPIException as e:
        log.critical(
            "SKIPPING LISTENBRAINZ: API exception occurred: %s", e
        )
        return None
    log.info("ListenBrainz connection succeeded.")
    return client


def lbz_love(lb: liblbz.ListenBrainz, tracks_to_love: list[dict]):
    """

    :param lb:
    :param tracks_to_love:
    :return:
    """
    global NEW_LOVES_LBZ

    user_loves = lbz_get_loves(lb)

    for track in tracks_to_love:
        mbid = get_mbid(track)
        if not mbid:
            log.warning(
                "No MBID found for %s - %s",
                track['title'], track['artist']
            )
            continue
        if not lbz_already_loved(user_loves, mbid):
            lb.submit_user_feedback(1, mbid)
            log.info(
                "ListenBrainz - Loved %s by %s - MBID: %s",
                track['title'], track['artist'], mbid
            )
            NEW_LOVES_LBZ += 1


def lbz_get_loves(lb: liblbz.ListenBrainz) -> set[str]:
    """
    Retrieve all tracks the user has already loved
    :param lb:
    :return:
    """
    all_loves = []
    offset = 0
    count = 100
    while True:
        user_loves = lb.get_user_feedback(
            username=LBZ_USERNAME,
            score=1,
            count=count,
            offset=offset,
            metadata=None
        )
        mbids = {track['recording_mbid'] for track in user_loves['feedback']}
        all_loves.extend(mbids)
        if len(user_loves['feedback']) < count:
            break   # No more feedback to fetch
        offset += count
    return all_loves


def get_mbid(track: dict) -> str | None:
    """
    Queries MusicBrainz and retrieves the first result with a matching Title and Artist
    :param track: Track dictionary containing keys: title, artist, label, year
    :return: MBID if found; NOne otherwise
    """
    # query = " ".join(str(val) for val in track.values())
    query = track["title"] + " " + track["artist"]
    log.info("Querying MusicBrainz: %s", query)
    mbz.set_useragent('RatingRelay', 'v0.1', contact='https://github.com/chunned/ratingrelay')
    track_search = mbz.search_recordings(
        query=query,
        artist=track["artist"],
        recording=track["title"]
    )
    for result in track_search['recording-list']:
        # find matching title+artist pair
        try:
            track_title = track['title'].lower()
            candidate_title = result['title'].lower()

            track_artist = track['artist'].lower()
            candidate_artist = result['artist-credit'][0]['name'].lower()
            if track_title == candidate_title and track_artist == candidate_artist:
                mbid = track_search['recording-list'][0]['id']
                return mbid
        except (IndexError, KeyError):
            return None
    return None


def lbz_already_loved(loved_mbids: set[str], mbid: str) -> bool:
    """
    Check if a given MBID is already present in the set of MBIDs the user has loved
    :param loved_mbids: Set of the MBIDs that the user has already loved
    :param mbid: Candidate MBID to check
    :return: Bool
    """
    if mbid in loved_mbids:
        log.info(
            "ListenBrainz - %s is already loved.",
            mbid
        )
        return True
    return False

if __name__ == "__main__":
    main()
