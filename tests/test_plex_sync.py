import pytest
from ratingrelay.relay import plex_relay_loves


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
    track = track_search[0]
    plex.submit_rating(track, plex.love_threshold)

    plex_relay_loves(services)

    lfm_loves = services.lfm.all_loves()
    lbz_loves = services.lbz.all_loves()

    assert lfm_loves[0].title == track.title
    assert lbz_loves[0].title == track.title


def test_plex_love_multiple_tracks(services, cleanup):
    """
    Test that all tracks loved on Plex get synced to the services
    """
    plex = services.plex

    # Make sure we're starting with no loved tracks
    loves = plex.get_loved_tracks()
    assert len(loves) == 0

    # Search for 10 arbitrary tracks, then love them
    track_search = plex.music_library.search(libtype="track", limit=10)
    for track in track_search:
        plex.submit_rating(track, plex.love_threshold)

    plex_relay_loves(services)

    lfm_loves = services.lfm.all_loves()
    lbz_loves = services.lbz.all_loves()

    assert len(lfm_loves) == 10
    assert len(lbz_loves) == 10
