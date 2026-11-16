"""Plex as the source: what a rating on Plex does to the other two services.

Each test drives the worker's entry point with the real adapters and checks both
what the target service now holds and what the run recorded, because those are
two halves of one promise: the write happened, and a later run knows it did.
"""

from ratingrelay.engine.types import OutcomeStatus, Reason
from ratingrelay.schemas.relay import (
    LastFMToConfig,
    ListenBrainzToConfig,
    PlexFromConfig,
)

from tests.integration.fakes import synced

RECORDING = "5c1f7a9e-8b1d-4c76-9a4e-0f2d3c4b5a61"
TRACK_MBID = "track-mbid-0001"


def blue_monday(world, *, rating=10.0):
    """Plex has Blue Monday, and MusicBrainz knows both the file and the recording."""
    world.brainz.add(
        "New Order", "Blue Monday", RECORDING, track_mbid=TRACK_MBID
    )
    return world.plex.add(
        "Blue Monday", "New Order", rating=rating, track_mbid=TRACK_MBID
    )


async def test_a_loved_track_is_loved_on_listenbrainz(world):
    blue_monday(world)

    run = await world.relay(
        PlexFromConfig(service="plex"), [ListenBrainzToConfig(service="listenbrainz")]
    )

    assert world.listenbrainz.score(RECORDING) == 1
    assert run.results["listenbrainz"]["ok"] == 1
    # Asked of the server (contract §5), not fetched and filtered here.
    assert world.plex.section.calls == [{"userRating>>=": 8.0}]
    # The file's own MBID reached the lookup, so the recording is identified by
    # identity rather than falling back to an artist/title search.
    assert world.brainz.lookups == [(TRACK_MBID, "New Order", "Blue Monday")]


async def test_a_second_run_writes_nothing_new(world):
    blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(source, targets)

    run = await world.relay(source, targets)

    # One submission in total: the second run recognised its own write.
    assert world.listenbrainz.submissions == [(1, RECORDING)]
    assert run.decisions() == [(OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)]
    assert run.results["listenbrainz"]["skipped"] == 1


async def test_a_hated_track_is_hated_on_listenbrainz(world):
    blue_monday(world, rating=1.0)

    run = await world.relay(
        PlexFromConfig(service="plex", feedback_type="hate"),
        [ListenBrainzToConfig(service="listenbrainz")],
    )

    assert world.listenbrainz.score(RECORDING) == -1
    assert run.results["listenbrainz"]["ok"] == 1


async def test_withdrawing_the_love_clears_listenbrainz(world):
    track = blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(source, targets)

    track.userRating = None  # the user unrates it on Plex
    run = await world.relay(source, targets)

    # Feedback withdrawn rather than loved with a different score, and the
    # record forgotten, because there is nothing left for us to own.
    assert world.listenbrainz.submitted(RECORDING) == [1, 0]
    assert world.listenbrainz.score(RECORDING) is None
    assert await synced(world.session) == []
    assert run.decisions() == [(OutcomeStatus.REVERTED, Reason.WITHDRAWN)]


async def test_withdrawing_the_hate_clears_listenbrainz(world):
    track = blue_monday(world, rating=1.0)
    source = PlexFromConfig(service="plex", feedback_type="hate")
    targets = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(source, targets)

    track.userRating = None
    await world.relay(source, targets)

    assert world.listenbrainz.submitted(RECORDING) == [-1, 0]
    assert world.listenbrainz.score(RECORDING) is None


async def test_target_drift_is_not_reapplied_while_the_record_stands(world):
    """RR-12 asks that a track unloved on ListenBrainz be re-loved next sync.

    A relay that did not ask is left alone, and this pins that: once a record
    exists, an unchanged intent is ``already_synced``, so target state decides
    whether a *new* write is needed, never whether a past one survived. See
    ``docs/plans/2026-11-16-013-integration-tests.md`` and 010 for why, and 014
    for the opt-in (``reapply_target_drift``) that re-loves it deliberately.
    Change either side and this test fails on purpose.
    """
    blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(source, targets)

    world.listenbrainz.scores.pop(RECORDING)  # un-loved by hand on ListenBrainz
    run = await world.relay(source, targets)

    assert world.listenbrainz.score(RECORDING) is None
    assert world.listenbrainz.submissions == [(1, RECORDING)]
    assert run.decisions() == [(OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)]


async def test_a_loved_track_is_loved_on_lastfm(world):
    blue_monday(world)

    run = await world.relay(
        PlexFromConfig(service="plex"), [LastFMToConfig(service="lastfm")]
    )

    assert world.lastfm.is_loved("New Order", "Blue Monday")
    assert world.lastfm.loves == [("New Order", "Blue Monday")]
    assert run.results["lastfm"]["ok"] == 1


async def test_withdrawing_the_love_unloves_on_lastfm(world):
    track = blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [LastFMToConfig(service="lastfm")]
    await world.relay(source, targets)

    track.userRating = None
    run = await world.relay(source, targets)

    assert not world.lastfm.is_loved("New Order", "Blue Monday")
    assert world.lastfm.unloves == [("New Order", "Blue Monday")]
    assert await synced(world.session) == []
    assert run.decisions() == [(OutcomeStatus.REVERTED, Reason.WITHDRAWN)]


async def test_a_drifted_lastfm_love_is_not_reapplied(world):
    """The Last.fm twin of the ListenBrainz pin above: same decision, same reason."""
    blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [LastFMToConfig(service="lastfm")]
    await world.relay(source, targets)

    world.lastfm.loved.clear()  # un-loved by hand on Last.fm
    await world.relay(source, targets)

    assert not world.lastfm.is_loved("New Order", "Blue Monday")
    assert world.lastfm.loves == [("New Order", "Blue Monday")]


async def test_a_dropped_love_is_written_again_when_the_relay_asks(world):
    """The same run as above, with ``reapply_target_drift``: the love comes back."""
    blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [LastFMToConfig(service="lastfm")]
    await world.relay(source, targets)

    world.lastfm.loved.clear()  # un-loved by hand on Last.fm
    run = await world.relay(source, targets, reapply_target_drift=True)

    assert world.lastfm.is_loved("New Order", "Blue Monday")
    assert run.decisions() == [(OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)]


async def test_a_dropped_love_is_written_again_on_listenbrainz(world):
    """Plex→ListenBrainz, dropped rather than replaced: the score goes back to 1."""
    blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(source, targets)

    world.listenbrainz.scores.pop(RECORDING)  # un-loved by hand on ListenBrainz
    run = await world.relay(source, targets, reapply_target_drift=True)

    assert world.listenbrainz.score(RECORDING) == 1
    assert run.decisions() == [(OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)]


async def test_a_love_replaced_by_a_hate_on_listenbrainz_is_left_alone(world):
    """A replacement is a person's edit: re-applying over it would fight them."""
    blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(source, targets)

    world.listenbrainz.hate(RECORDING, "New Order", "Blue Monday")
    run = await world.relay(source, targets, reapply_target_drift=True)

    assert world.listenbrainz.score(RECORDING) == -1
    assert run.decisions() == [(OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)]


async def test_a_love_the_user_made_is_left_alone(world):
    """Contract §3: a love we did not write is never recorded, so never undone."""
    blue_monday(world)
    world.lastfm.love("New Order", "Blue Monday")

    run = await world.relay(
        PlexFromConfig(service="plex"), [LastFMToConfig(service="lastfm")]
    )

    assert run.decisions() == [(OutcomeStatus.SKIPPED, Reason.ALREADY_ON_TARGET)]
    assert await synced(world.session) == []
    # Nothing was written, so the user's own love is not ours to keep or drop.
    assert world.lastfm.loves == []

async def test_a_round_trip_love_then_withdrawal_then_love_again(world):
    """Love, withdraw, love again — the target follows every time, from nothing."""
    track = blue_monday(world)
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]

    await world.relay(source, targets)
    assert world.listenbrainz.score(RECORDING) == 1

    track.userRating = None
    await world.relay(source, targets)
    assert world.listenbrainz.score(RECORDING) is None

    track.userRating = 9.0
    await world.relay(source, targets)
    assert world.listenbrainz.score(RECORDING) == 1
    assert world.listenbrainz.submissions == [(1, RECORDING), (0, RECORDING), (1, RECORDING)]
