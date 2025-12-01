import pytest
from ratingrelay.relay import plex_relay


def test_plex_love_track(services, cleanup):
    """
    Test that after loving a Plex track, it gets synced to ListenBrainz and
    LastFM by ratingrelay.relay.plex_relay
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    loves = plex.get_loved_tracks()
    assert len(loves) == 0

    # Search for an arbitrary track, then love it
    track_search = plex.music_library.search(libtype="track", limit=1)
    assert len(track_search) != 0
    track = track_search[0]
    plex.submit_rating(track, plex.love_threshold)

    # Make sure it's returned by get_loved_tracks()
    loves = plex.get_loved_tracks()
    assert len(loves) == 1
    assert loves[0].title == track.title

    # Relay
    plex_relay(services)

    lfm_loves = services.lfm.all_loves()
    lbz_loves = services.lbz.all_loves()

    assert lfm_loves[0].title == track.title
    assert lbz_loves[0].title == track.title
