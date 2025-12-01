from ratingrelay.relay import plex_relay_loves


def test_plex_relay_loves_to_listenbrainz(services, cleanup):
    """
    Test that all tracks loved on Plex get synced to ListenBrainz
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    loves = plex.get_loved_tracks()
    assert len(loves) == 0

    # Search for 10 arbitrary tracks, then love them
    track_search = plex.music_library.search(libtype="track", limit=10)
    for track in track_search:
        plex.submit_rating(track, plex.love_threshold)

    services.lfm = None
    plex_relay_loves(services)

    lbz_loves = services.lbz.all_loves()
    assert len(lbz_loves) == 10
