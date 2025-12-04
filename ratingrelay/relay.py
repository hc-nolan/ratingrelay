from typing import Optional
import logging

from plexapi.audio import Track as PlexTrack

from .services import Services
from .config import Settings
from .listenbrainz import ListenBrainz
from .lastfm import LastFM
from .track import Track
from .database import Database
from .plex import Plex
from .musicbrainz import query_recording_mbid

log = logging.getLogger("ratingrelay")


def relay(services: Services, settings: Settings):
    """
    Relays ratings between configured services.

    By default, ratings are only synced from Plex to LastFM and/or ListenBrainz.
    If the environment variable TWO_WAY is set to true, ratings will also be
    synced to Plex from the other services.
    """
    plex_relay(services)
    if settings.two_way:
        lbz_relay(services)
        lfm_relay(services)


def plex_relay(services: Services):
    """
    Relays ratings from Plex to LastFM and/or ListenBrainz
    """
    log.info("Starting sync from Plex -> LBZ/LFM.")

    plex_love_stats = plex_relay_loves(services)

    if services.plex.hate_threshold is None:
        plex_hate_stats = {"plex_hates": 0, "lbz_added": 0}
    else:
        plex_hate_stats = plex_relay_hates(services)

    print_stats(love=plex_love_stats, hate=plex_hate_stats)


def plex_relay_loves(services: Services) -> dict:
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
    log.info(f"Plex returned {len(plex_loves)} loved tracks.")

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
                log.info(f"ListenBrainz - New love: {track.title} by {track.artist}")
                lbz.love(track)
                lbz_added += 1
            else:
                log.info(
                    f"ListenBrainz - Track already loved: {track.title} by {track.artist}"
                )

        if lfm:
            if not check_list_match(track=track, target_list=lfm_loves):
                log.info(f"Last.FM - New love: {track.title} by {track.artist}")
                lfm.love(track)
                lfm_added += 1
            else:
                log.info(
                    f"Last.FM - Track already loved: {track.title} by {track.artist}"
                )
    log.info(
        f"Finished adding loves:     ListenBrainz: {lbz_added:<10} Last.FM: {lfm_added:<10}"
    )

    plex_reset(services=services, tracks=plex_loves, table="loved")

    return {
        "plex_loves": len(plex_loves),
        "lbz_added": lbz_added,
        "lfm_added": lfm_added,
    }


def check_list_match(track: Track, target_list: list) -> any:
    """
    Check if there is a match for the provided `track` in the `target_list`.
    Returns the matching item.
    """
    title = comparison_format(track.title)
    artist = comparison_format(track.artist)

    matched_title = False

    for list_track in target_list:
        match list_track:
            case tuple():
                list_title = comparison_format(list_track[0])
                list_artist = comparison_format(list_track[1])
            case Track():
                list_title = comparison_format(list_track.title)
                list_artist = list_track.artist
            case PlexTrack():
                list_artist = comparison_format(list_track.artist().title)
            case _:
                log.warning(
                    f"Unrecognized type for comparison track: {list_track} - "
                    f"Type: {type(list_track)}"
                )
                continue
        if (title in list_title) or (list_title in title):
            matched_title = True

            if (artist in list_artist) or (list_artist in artist):
                return list_track

    if matched_title:
        log.warning(
            f"Found matching title in target list, but artist(s) did not match "
            f"for track: {track}"
        )

    return False


def comparison_format(item: str) -> str:
    """
    Apply processing to the input string for comparison purposes between
    services which may have the strings in different formats.

    Removes any quote/apostrophe characters, converts to lowercase
    """
    return item.lower().replace("'", "").replace("’", "").replace("&", "and")


def plex_relay_hates(services: Services) -> dict:
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
        return {}

    log.info("Grabbing existing ListenBrainz hated tracks.")
    lbz_hates = lbz.all_hates()
    log.info(f"ListenBrainz returned {len(lbz_hates)} existing hated tracks")
    lbz_hated_mbids = {t.mbid for t in lbz_hates}

    plex_hates = to_tracks(
        plex_tracks=plex.get_hated_tracks(), services=services, rating="hated"
    )
    log.info(f"Plex returned {len(plex_hates)} hated tracks.")

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
            log.info(f"Hating {track.title}, {track.artist}")
            lbz.hate(track)
            lbz_added += 1

    log.info(f"Finished adding hates:   ListenBrainz: {lbz_added}")

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
                f"Track no longer {table} on Plex: {(track.get('title'), track.get('artist'))}"
            )
            # move from current table to reset table
            db.delete_by_rec_id(rec_mbid=track.get("rec_mbid"), table=table)
            if lbz:
                lbz.reset(track)
            if lfm:
                lfm.reset(Track(title=track.get("title"), artist=track.get("artist")))

    log.info(f"Reset {reset_count} tracks.")


def print_stats(love: dict, hate: Optional[dict]):
    """Prints statistics"""
    log.info("STATISTICS:")
    log.info(
        f"{'Plex:':<12}\tLoves: {love.get('plex_loves'):<10}\tHates: {hate.get('plex_hates'):<10}"
    )
    log.info("ADDITIONS:")
    log.info(
        f"{'ListenBrainz:':<12}\tLoves: {love.get('lbz_added'):<10}\t"
        f"Hates: {hate.get('lbz_added'):<10}\t"
    )
    log.info(
        f"{'Last.FM:':<12}\tLoves: {love.get('lfm_added'):<10}\tHates: {'N/A':<10}\t"
    )


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
                f"No recording MBID returned by MusicBrainz for: {(title, artist)}"
            )
            return None

    return Track(title=title, artist=artist, mbid=rec_mbid, track_mbid=track_mbid)


def lbz_get_loves(lbz: ListenBrainz) -> set[str]:
    """
    Queries ListenBrainz for loved tracks and returns a set of the loved
    track MBIDs
    """
    log.info("Grabbing all existing loved tracks from ListenBrainz.")
    lbz_loves = lbz.all_loves()
    log.info(f"ListenBrainz returned {len(lbz_loves)} loved tracks.")
    lbz_loved_mbids = {t.mbid for t in lbz_loves}
    return lbz_loved_mbids


def lbz_get_hates(lbz: ListenBrainz) -> set[str]:
    """
    Queries ListenBrainz for hated tracks and returns a set of the hated
    track MBIDs
    """
    log.info("Grabbing all existing hated tracks from ListenBrainz.")
    lbz_hates = lbz.all_hates()
    log.info(f"ListenBrainz returned {len(lbz_hates)} hated tracks.")
    lbz_hated_mbids = {t.mbid for t in lbz_hates}
    return lbz_hated_mbids


def lfm_get_loves(lfm: LastFM) -> list[tuple]:
    """
    Queries LastFM for loved tracks and returns a list of the loved
    track title+artist tuples
    """
    log.info("Grabbing all existing loved tracks from LastFM.")
    lfm_loves = lfm.all_loves()
    lfm_loves_tuples = [(t.title.lower(), t.artist.lower()) for t in lfm_loves]
    log.info(f"Last.FM returned {len(lfm_loves)} loved tracks.")
    return lfm_loves_tuples


def lbz_relay(services: Services):
    """
    Relays ratings from ListenBrainz to Plex
    """
    log.info("Starting sync from ListenBrainz -> Plex.")
    if not services.lbz:
        log.warning(
            "ListenBrainz service not configured. Skipping sync from ListenBrainz -> Plex."
        )
        return

    lbz_love_stats = lbz_relay_generic(services=services, rating="love")
    if services.plex.hate_threshold is None:
        lbz_hate_stats = {"lbz_hates": 0, "plex_added": 0}
    else:
        lbz_hate_stats = lbz_relay_generic(services=services, rating="hate")

    log.info("Finished relaying tracks from ListenBrainz to Plex")
    log.info(f"Added:\tLoves: {lbz_love_stats}\tHates: {lbz_hate_stats}")


def lbz_relay_generic(services: Services, rating: str) -> int:
    """
    Relay loved or hated tracks from ListenBrainz to Plex
    """
    log.info(f"Relaying {rating}d tracks from ListenBrainz.")
    lbz = services.lbz

    plex_added = 0

    if rating == "love":
        log.info("Grabbing all existing loved tracks from ListenBrainz.")
        lbz_items = lbz.all_loves()
        log.info(f"ListenBrainz returned {len(lbz_items)} loved tracks.")

        plex_added = sync_list_with_plex(
            tracks=lbz_items, services=services, rating="loved"
        )

    elif rating == "hate":
        log.info("Grabbing all existing hated tracks from ListenBrainz.")
        lbz_items = lbz.all_hates()
        log.info(f"ListenBrainz returned {len(lbz_items)} hated tracks.")

        plex_added = sync_list_with_plex(
            tracks=lbz_items, services=services, rating="hated"
        )

    return plex_added


def lfm_relay(services: Services):
    """
    Relays ratings from LastFM to Plex
    """
    log.info("Starting sync from LastFM -> Plex.")
    if not services.lfm:
        log.warning("LastFM service not configured. Skipping sync from LastFM -> Plex.")
        return

    lfm = services.lfm

    log.info("Grabbing all existing loved tracks from LastFM.")
    lfm_items = lfm.all_loves()
    log.info(f"LastFM returned {len(lfm_items)} loved tracks.")

    plex_added = sync_list_with_plex(
        tracks=lfm_items, services=services, rating="loved"
    )
    log.info("Finished relaying tracks from LastFM to Plex")
    log.info(f"Added:\tLoves: {plex_added}")


def sync_list_with_plex(tracks: set[Track], services: Services, rating: str) -> int:
    """
    Pass a list of Tracks and a Plex object; ensure the ratings are synced to
    Plex. Rating should be either `loved` or `hated`. Returns the number of
    tracks changed on Plex.
    """
    plex = services.plex

    log.info(f"Querying Plex for {rating} tracks")
    plex_items = to_tracks(
        plex_tracks=plex.get_loved_tracks(), services=services, rating=rating
    )
    log.info(f"Plex returned {len(plex_items)} {rating} tracks.")

    plex_added = 0
    for track in tracks:
        if not check_list_match(track=track, target_list=plex_items):
            log.info(f"Track not {rating} on Plex: {track}")

            plex_track_search = plex.music_library.search(
                libtype="track", title=track.title.lower()
            )
            if len(plex_track_search) == 0:
                # if no results were returned, try a search again with smart quotes
                plex_track_search = plex.music_library.search(
                    libtype="track", title=track.title.lower().replace("'", "’")
                )

            match = check_list_match(track=track, target_list=plex_track_search)
            if match:
                if rating == "loved":
                    log.info(f"Loving track on Plex: {match}")
                    plex.submit_rating(match, plex.love_threshold)
                elif rating == "hated":
                    log.info(f"Hating track on Plex: {match}")
                    plex.submit_rating(match, plex.hate_threshold)

                plex_added += 1
        else:
            log.info(f"Track already {rating} on Plex: {track}")

    return plex_added
