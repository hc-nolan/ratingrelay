from ratingrelay.config import settings
from ratingrelay.relay import lfm_relay, track_from_plex


def test_lfm_relay(services, cleanup):
    """
    Test that tracks loved on LastFM are successfully relayed to Plex
    """
    plex = services.plex
    lfm = services.lfm

    # Search for 10 arbitrary tracks, then love them on ListenBrainz
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for plex_track in track_search:
        track = track_from_plex(
            plex_track=plex_track, db=services.db, plex=plex, rating="loved"
        )
        lfm.love(track)

    lfm_relay(services)

    plex_loves = plex.get_loved_tracks()
    assert len(plex_loves) == settings.test_limit
