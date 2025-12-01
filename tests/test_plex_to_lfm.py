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
    track_search = plex.music_library.search(libtype="track", limit=10)
    for track in track_search:
        plex.submit_rating(track, plex.love_threshold)

    services.lbz = None
    plex_relay_loves(services)

    lfm_loves = services.lfm.all_loves()
    assert len(lfm_loves) == 10


def test_plex_unlove(services, cleanup):
    """
    Test that when we un-love a track on Plex, it is un-loved on LastFM
    next sync
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    loves = plex.get_loved_tracks()
    assert len(loves) == 0

    # Search for an arbitrary track, then love it
    track_search = plex.music_library.search(libtype="track", limit=1)
    plex_track = track_search[0]
    plex.submit_rating(plex_track, plex.love_threshold)

    services.lbz = None
    plex_relay_loves(services)

    lfm_loves = services.lfm.all_loves()
    assert len(lfm_loves) == 1

    # Un-love the track
    plex.submit_rating(plex_track, plex.love_threshold - 1)

    # Sync again
    plex_relay_loves(services)

    # Check that the track was unloved from LastFM
    lfm_loves_after = services.lfm.all_loves()
    assert len(lfm_loves_after) == 0
