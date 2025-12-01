from ratingrelay.relay import lfm_relay, track_from_plex


def test_lfm_relay(services, cleanup):
    """
    Test that tracks loved on LastFM are successfully relayed to Plex
    """
    plex = services.plex
    lfm = services.lfm

    # Search for an arbitrary track, then love it on ListenBrainz
    track_search = plex.music_library.search(libtype="track", limit=1)
    plex_track = track_search[0]

    track = track_from_plex(
        plex_track=plex_track, db=services.db, plex=plex, rating="loved"
    )
    lfm.love(track)

    lfm_relay(services)

    plex_loves = plex.get_loved_tracks()
    assert len(plex_loves) == 1
