from ratingrelay.config import settings
from ratingrelay.relay import plex_relay_loves, plex_relay_hates


def test_plex_relay_loves_to_listenbrainz(services, cleanup):
    """
    Test that all tracks loved on Plex get synced to ListenBrainz
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    loves = plex.get_loved_tracks()
    assert len(loves) == 0

    # Search for arbitrary tracks, then love them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for track in track_search:
        plex.submit_rating(track, plex.love_threshold)

    services.lfm = None
    plex_relay_loves(services)

    lbz_loves = services.lbz.all_loves()
    assert len(lbz_loves) == settings.test_limit


def test_plex_relay_hates_to_listenbrainz(services, cleanup):
    """
    Test that all tracks hated on Plex get synced to ListenBrainz
    """
    plex = services.plex

    # Make sure we're starting with no hated tracks
    hates = plex.get_hated_tracks()
    assert len(hates) == 0

    # Search for arbitrary tracks, then hate them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for track in track_search:
        plex.submit_rating(track, plex.hate_threshold)

    services.lfm = None
    plex_relay_hates(services)

    lbz_hates = services.lbz.all_hates()
    assert len(lbz_hates) == settings.test_limit


def test_plex_unlove(services, cleanup):
    """
    Test that when we un-love a track on Plex, it is un-loved on ListenBrainz
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

    services.lfm = None
    plex_relay_loves(services)

    lbz_loves = services.lbz.all_loves()
    assert len(lbz_loves) == settings.test_limit

    # Un-love the tracks
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.love_threshold - 1)

    # Sync again
    plex_relay_loves(services)

    # Check that the track was unloved from ListenBrainz
    lbz_loves_after = services.lbz.all_loves()
    assert len(lbz_loves_after) == 0


def test_plex_unhate(services, cleanup):
    """
    Test that when we un-hate a track on Plex, it is un-hated on ListenBrainz
    next sync
    """
    plex = services.plex

    # Make sure we're starting with no hated tracks
    hates = plex.get_hated_tracks()
    assert len(hates) == 0

    # Search for arbitrary tracks and then hate them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.hate_threshold)

    services.lfm = None
    plex_relay_hates(services)

    lbz_hates = services.lbz.all_hates()
    assert len(lbz_hates) == settings.test_limit

    # Un-hate the tracks
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.hate_threshold + 1)

    # Sync again
    plex_relay_hates(services)

    # Check that the tracks were unhated from ListenBrainz
    lbz_hates_after = services.lbz.all_hates()
    assert len(lbz_hates_after) == 0


def test_lbz_unlove(services, cleanup):
    """
    If a track is synced from Plex to ListenBrainz, then the user unloves the track
    on ListenBrainz, it should be re-loved next sync
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    assert len(plex.get_loved_tracks()) == 0

    # Search for arbitrary tracks and then love them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.love_threshold)

    services.lfm = None
    plex_relay_loves(services)

    lbz_loves = services.lbz.all_loves()
    assert len(lbz_loves) == settings.test_limit

    # Un-love the tracks
    for plex_track in track_search:
        lbz_loves = services.lbz.all_loves()
    assert len(lbz_loves) == 0

    # Sync again
    plex_relay_loves(services)

    # Check that the tracks were synced back to ListenBrainz
    lbz_loves_after = services.lbz.all_loves()
    assert len(lbz_loves_after) == settings.test_limit


def test_lbz_unhate(services, cleanup):
    """
    If a track is synced from Plex to ListenBrainz, then the user unhates the track
    on ListenBrainz, it should be re-hated next sync
    """
    plex = services.plex

    # Make sure we're starting with no hated tracks
    assert len(plex.get_hated_tracks()) == 0

    # Search for arbitrary tracks and then hate them
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for plex_track in track_search:
        plex.submit_rating(plex_track, plex.hate_threshold)

    services.lfm = None
    plex_relay_hates(services)

    lbz_hates = services.lbz.all_hates()
    assert len(lbz_hates) == settings.test_limit

    # Un-hate the tracks
    for lbz_hate in lbz_hates:
        services.lbz.reset(lbz_hate)

    lbz_hates = services.lbz.all_hates()
    assert len(lbz_hates) == 0

    # Sync again
    plex_relay_hates(services)

    # Check that the track was synced back to ListenBrainz
    lbz_hates_after = services.lbz.all_hates()
    assert len(lbz_hates_after) == settings.test_limit
