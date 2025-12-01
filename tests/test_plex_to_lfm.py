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
