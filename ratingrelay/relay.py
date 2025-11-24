from typing import Optional
import logging
import musicbrainzngs as mbz
from plexapi.audio import Track as PlexTrack
from .services import Services
from .config import Settings
from .listenbrainz import ListenBrainz
from .lastfm import LastFM
from .track import Track
from .database import Database
from .plex import Plex

log = logging.getLogger("ratingrelay")


def relay(services: Services, settings: Settings):
    """
    Relays ratings between configured services.

    By default, ratings are only synced from Plex to LastFM and/or ListenBrainz.
    If the environment variable TWO_WAY is set to true, ratings will also be
    synced to Plex from the other services.
    """
    plex_relay(services)


def plex_relay(services: Services):
    """
    Relays ratings from Plex to LastFM and/or ListenBrainz
    """
    plex_love_stats = plex_relay_loves(services)

    if services.plex.hate_threshold is None:
        plex_hate_stats = {"plex_hates": 0, "lbz_added": 0}
    else:
        plex_hate_stats = plex_relay_hates(services)

    print_stats(love=plex_love_stats, hate=plex_hate_stats)


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


def plex_relay_loves(services: Services):
    """
    Relays loved track ratings from Plex to LastFM and/or ListenBrainz
    """
    log.info("Relaying loved tracks from Plex.")
    plex = services.plex
    lbz = services.lbz
    lfm = services.lfm
    db = services.db

    lbz_added = 0
    lfm_added = 0

    if lbz:
        lbz_loves = lbz_get_loves(lbz)
    if lfm:
        lfm_loves = lfm_get_loves(lfm)

    log.info("Querying Plex for loved tracks")
    plex_loves = to_tracks(
        plex_tracks=plex.get_loved_tracks(), services=services, rating="loved"
    )
    log.info("Plex returned %s loved tracks.", len(plex_loves))

    for track in plex_loves:
        db.add_track(
            title=track.title,
            artist=track.artist,
            track_mbid=track.track_mbid,
            rec_mbid=track.mbid,
            table="loved",
        )

        if lbz:
            if track.mbid not in lbz_loves:
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
            if (track.title.lower(), track.artist.lower()) not in lfm_loves:
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

    plex_reset(services=services, tracks=plex_loves, table="loved")

    return {
        "plex_loves": len(plex_loves),
        "lbz_added": lbz_added,
        "lfm_added": lfm_added,
    }


def to_tracks(
    plex_tracks: list[PlexTrack], services: Services, rating: str
) -> set[Track]:
    """
    Convert a list of PlexTracks into a set of Tracks
    """
    tracks = set()
    for track in plex_tracks:
        tracks.add(
            track_from_plex(
                plex_track=track, db=services.db, plex=services.plex, rating=rating
            )
        )

    return tracks


def track_from_plex(
    plex_track: PlexTrack, db: Database, plex: Plex, rating: str
) -> Track:
    """
    Parses the track MBID from a Plex track and returns a Track with the
    matching recording MBID.

    First, queries the database for a match. If no match is found, a query is
    made to the MusicBrainz API to get the recording MBID.

    Args:
        plex_track: A PlexAPI Track object
        db: Database class instance
        plex: Plex class instance
        rating: `loved` or `hated`
    """
    title = plex_track.title
    artist = plex_track.artist().title
    track_mbid = plex.parse_track_mbid(plex_track)

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


def lbz_get_loves(lbz: ListenBrainz) -> set:
    log.info("Grabbing all existing loved tracks from ListenBrainz.")
    lbz_loves = lbz.all_loves()
    log.info("ListenBrainz returned %s loved tracks.", len(lbz_loves))
    lbz_loved_mbids = {t.mbid for t in lbz_loves}
    return lbz_loved_mbids


def lfm_get_loves(lfm: LastFM) -> list:
    log.info("Grabbing all existing loved tracks from LastFM.")
    lfm_loves = lfm.all_loves()
    lfm_loves_tuples = [(t.title.lower(), t.artist.lower()) for t in lfm_loves]
    log.info("Last.FM returned %s loved tracks.", len(lfm_loves))
    return lfm_loves_tuples


def plex_relay_hates(services: Services):
    """
    Relays hated track ratings from Plex to ListenBrainz.
    LastFM does not support hated tracks.
    """
    lbz = services.lbz
    plex = services.plex
    db = services.db

    lbz_added = 0

    log.info("Relaying hated tracks from Plex.")

    if not lbz:
        log.warning("ListenBrainz not configured, skipping relaying hated tracks.")
        return

    log.info("Grabbing existing ListenBrainz hated tracks.")
    lbz_hates = lbz.all_hates()
    log.info("ListenBrainz returned %s existing hated tracks", len(lbz_hates))
    lbz_hated_mbids = {t.mbid for t in lbz_hates}

    plex_hates = to_tracks(
        plex_tracks=plex.get_hated_tracks(), services=services, rating="hated"
    )
    log.info("Plex returned %s hated tracks.", len(plex_hates))

    for track in plex_hates:
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

    plex_reset(services=services, tracks=plex_hates, table="hated")
    return {"plex_hates": len(plex_hates), "lbz_added": lbz_added}


def plex_reset(services: Services, tracks: set[Track], table: str):
    """
    Compare currently loved/hated Plex tracks to the database. If a track in the
    database is not also returned by Plex, it is no longer loved/hated; remove it
    from the loved/hated table and add it to the reset table.
    """
    db = services.db
    lbz = services.lbz
    lfm = services.lfm

    log.info("Checking for tracks to reset.")

    entries = db.get_all_tracks(table=table)

    plex_ids = [track.mbid for track in tracks]

    reset_count = 0
    for track in entries:
        if track.get("rec_mbid") not in plex_ids:
            reset_count += 1
            log.info(
                "Track no longer %s on Plex: %s",
                table,
                (track.get("title"), track.get("artist")),
            )
            # move from current table to reset table
            db.delete_by_rec_id(rec_mbid=track.get("rec_mbid"), table=table)
            if lbz:
                lbz.reset(track)
            if lfm:
                lfm.reset(Track(title=track.get("title"), artist=track.get("artist")))

    log.info(f"Reset {reset_count} tracks.")
