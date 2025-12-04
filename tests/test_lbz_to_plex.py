from tests.conftest import assert_relay_success
from ratingrelay.config import settings
from ratingrelay.relay import lbz_relay_generic, track_from_plex


def test_lbz_relay_loves(services, cleanup):
    """
    Test that tracks loved on ListenBrainz are successfully relayed to Plex
    """
    _test_lbz_relay(
        services=services,
        rating="love",
        lbz_action=services.lbz.love,
        plex_getter=services.plex.get_loved_tracks,
        lbz_getter=services.lbz.all_loves,
    )


def test_lbz_relay_hates(services, cleanup):
    """
    Test that tracks hated on ListenBrainz are successfully relayed to Plex
    """
    _test_lbz_relay(
        services=services,
        rating="hate",
        lbz_action=services.lbz.hate,
        plex_getter=services.plex.get_hated_tracks,
        lbz_getter=services.lbz.all_hates,
    )


def _test_lbz_relay(services, rating: str, lbz_action, plex_getter, lbz_getter):
    """
    Generic test helper for ListenBrainz to Plex relay testing
    """
    plex = services.plex

    # Search for arbitrary tracks and rate them on ListenBrainz
    track_search = plex.music_library.search(libtype="track", limit=settings.test_limit)
    for plex_track in track_search:
        track = track_from_plex(
            plex_track=plex_track,
            db=services.db,
            plex=plex,
            rating=rating + ("d" if rating == "hate" else "d"),
        )
        lbz_action(track)

    lbz_relay_generic(services=services, rating=rating)
    plex_tracks = plex_getter()

    assert_relay_success(
        expected=settings.test_limit,
        actual=plex_tracks,
        lbz_getter=lbz_getter,
        rating=rating,
    )
