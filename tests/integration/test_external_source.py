"""The external services as the source: what a love there does to Plex.

These are the two-way relays — the direction where Plex is the target, and the
one where a clearing write must put back a rating instead of inventing one.
"""

import json

from ratingrelay.engine.types import OutcomeStatus, Reason
from ratingrelay.schemas.relay import (
    LastFMFromConfig,
    ListenBrainzFromConfig,
    PlexToConfig,
)

from tests.integration.fakes import synced

RECORDING = "9b2f4c31-7d05-4a8e-bb17-2c6a8f0d9e42"


def blue_monday(world, *, rating=None):
    """Plex holds an unrated copy, and ListenBrainz knows the recording."""
    world.listenbrainz.love(RECORDING, "New Order", "Blue Monday")
    return world.plex.add("Blue Monday", "New Order", rating=rating)


async def test_a_loved_recording_rates_plex(world):
    track = blue_monday(world)

    run = await world.relay(
        ListenBrainzFromConfig(service="listenbrainz"), [PlexToConfig(service="plex")]
    )

    # The love threshold, not 10: a love says "loved", not "perfect".
    assert track.userRating == 8.0
    assert run.results["plex"]["ok"] == 1

    record, = await synced(world.session, source="listenbrainz", target="plex")
    assert record.intent == "rating:8.0"
    assert json.loads(record.target_key) == [track.ratingKey]
    assert json.loads(record.prior) == {track.ratingKey: None}


async def test_a_second_run_rates_nothing_new(world):
    blue_monday(world)
    source = ListenBrainzFromConfig(service="listenbrainz")
    targets = [PlexToConfig(service="plex")]
    await world.relay(source, targets)

    run = await world.relay(source, targets)

    assert run.decisions() == [(OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)]
    assert len(await synced(world.session)) == 1


async def test_a_hated_recording_rates_plex_low(world):
    world.listenbrainz.hate(RECORDING, "New Order", "Blue Monday")
    track = world.plex.add("Blue Monday", "New Order")

    run = await world.relay(
        ListenBrainzFromConfig(service="listenbrainz", feedback_type="hate"),
        [PlexToConfig(service="plex")],
    )

    assert track.userRating == 2.0
    assert run.results["plex"]["ok"] == 1


async def test_withdrawing_the_love_clears_the_plex_rating(world):
    track = blue_monday(world)
    source = ListenBrainzFromConfig(service="listenbrainz")
    targets = [PlexToConfig(service="plex")]
    await world.relay(source, targets)

    world.listenbrainz.scores.pop(RECORDING)  # feedback withdrawn at the source
    run = await world.relay(source, targets)

    # Back to unrated, which is what the copy held before we wrote: the record's
    # prior is restored, never a rating the user did not choose.
    assert track.userRating is None
    assert await synced(world.session) == []
    assert run.decisions() == [(OutcomeStatus.REVERTED, Reason.WITHDRAWN)]


async def test_a_loved_track_rates_plex(world):
    world.lastfm.love("New Order", "Blue Monday")
    track = world.plex.add("Blue Monday", "New Order")

    run = await world.relay(
        LastFMFromConfig(service="lastfm"), [PlexToConfig(service="plex")]
    )

    assert track.userRating == 8.0
    assert run.results["plex"]["ok"] == 1


async def test_unloving_on_lastfm_clears_the_plex_rating(world):
    world.lastfm.love("New Order", "Blue Monday")
    track = world.plex.add("Blue Monday", "New Order")
    source = LastFMFromConfig(service="lastfm")
    targets = [PlexToConfig(service="plex")]
    await world.relay(source, targets)

    world.lastfm.loved.clear()
    await world.relay(source, targets)

    assert track.userRating is None
    assert await synced(world.session) == []


async def test_a_rating_the_user_chose_is_kept_and_restored(world):
    """A lower rating is the user's own: we write over it, and undo puts it back."""
    track = blue_monday(world, rating=4.0)

    await world.relay(
        ListenBrainzFromConfig(service="listenbrainz"), [PlexToConfig(service="plex")]
    )
    assert track.userRating == 8.0

    world.listenbrainz.scores.pop(RECORDING)
    await world.relay(
        ListenBrainzFromConfig(service="listenbrainz"), [PlexToConfig(service="plex")]
    )

    assert track.userRating == 4.0
