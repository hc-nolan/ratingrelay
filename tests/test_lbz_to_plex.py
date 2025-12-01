from ratingrelay.relay import lbz_relay_generic, track_from_plex


def test_lbz_relay_loves(services, cleanup):
    """
    Test that tracks loved on ListenBrainz are successfully relayed to Plex
    """

    plex = services.plex
    lbz = services.lbz

    # Search for an arbitrary track, then love it on ListenBrainz
    track_search = plex.music_library.search(libtype="track", limit=1)
    plex_track = track_search[0]

    track = track_from_plex(
        plex_track=plex_track, db=services.db, plex=plex, rating="loved"
    )
    lbz.love(track)

    lbz_relay_generic(services=services, rating="love")

    plex_loves = plex.get_loved_tracks()
    assert len(plex_loves) == 1


def test_lbz_relay_hates(services, cleanup):
    """
    Test that tracks hated on ListenBrainz are successfully relayed to Plex
    """

    plex = services.plex
    lbz = services.lbz

    # Search for an arbitrary track, then hate it on ListenBrainz
    track_search = plex.music_library.search(libtype="track", limit=1)
    plex_track = track_search[0]

    track = track_from_plex(
        plex_track=plex_track, db=services.db, plex=plex, rating="hated"
    )
    lbz.hate(track)

    lbz_relay_generic(services=services, rating="hate")

    plex_hates = plex.get_hated_tracks()
    assert len(plex_hates) == 1
