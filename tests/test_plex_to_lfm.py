from ratingrelay.config import settings
from ratingrelay.relay import plex_relay_loves


def test_plex_relay_loves_to_lastfm(services, cleanup):
    """
    Test that all tracks loved on Plex get synced to LastFM
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    loves = plex.get_loved_tracks()
    assert len(loves) == 0

    # Search for 10 arbitrary tracks, then love them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for track in track_search:
        plex.submit_rating(track, plex.love_threshold)

    services.lbz = None
    plex_relay_loves(services)

    lfm_loves = services.lfm.all_loves()
    assert len(lfm_loves) == settings.test_limit


def test_plex_unlove(services, cleanup):
    """
    Test that when we un-love a track on Plex, it is un-loved on LastFM
    next sync
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    loves = plex.get_loved_tracks()
    assert len(loves) == 0

    # Search for arbitrary tracks and then love them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.love_threshold)

    services.lbz = None
    plex_relay_loves(services)

    lfm_loves = services.lfm.all_loves()
    assert len(lfm_loves) == settings.test_limit

    # Un-love the tracks
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.love_threshold - 1)

    # Sync again
    plex_relay_loves(services)

    # Check that the tracks were unloved from LastFM
    lfm_loves_after = services.lfm.all_loves()
    assert len(lfm_loves_after) == 0


def test_lfm_unlove(services, cleanup):
    """
    If a track is synced from Plex to LastFM, then the user unloves the track
    on LastFM, it should be re-loved next sync
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    assert len(plex.get_loved_tracks()) == 0

    # Search for arbitrary tracks and love them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.love_threshold)

    services.lbz = None
    plex_relay_loves(services)

    lfm_loves = services.lfm.all_loves()
    assert len(lfm_loves) == settings.test_limit

    # Un-love the tracks
    for lfm_love in lfm_loves:
        services.lfm.reset(lfm_love)

    lfm_loves = services.lfm.all_loves()
    assert len(lfm_loves) == 0

    # Sync again
    plex_relay_loves(services)

    # Check that the tracks were loved again on LastFM
    lfm_loves_after = services.lfm.all_loves()
    assert len(lfm_loves_after) == settings.test_limit
