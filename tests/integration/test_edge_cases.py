"""The edges RR-12 lists: spelling, missing identity, nothing to do, and the guard.

None of these are network edges — they are the cases where a relay can quietly do
the wrong thing (write a guess, skip a track nobody hears about again, or clear a
whole target because a source answered with an empty list).
"""

from ratingrelay.engine.types import OutcomeStatus, Reason
from ratingrelay.schemas.relay import (
    LastFMFromConfig,
    LastFMToConfig,
    ListenBrainzFromConfig,
    ListenBrainzToConfig,
    PlexFromConfig,
    PlexToConfig,
)

from tests.integration.fakes import synced


async def test_smart_quotes_do_not_stop_a_match(world):
    """Last.fm and Plex disagree about the apostrophe; the normalized form does not."""
    world.lastfm.love("Bananarama", "Don't Stop")
    track = world.plex.add("Don\u2019t Stop", "Bananarama")

    await world.relay(LastFMFromConfig(service="lastfm"), [PlexToConfig(service="plex")])

    assert track.userRating == 8.0


async def test_an_ampersand_is_not_a_mismatch(world):
    """An ampersand is punctuation, so an artist written with and without it lines up."""
    world.lastfm.love("Simon & Garfunkel", "The Boxer")
    track = world.plex.add("The Boxer", "Simon Garfunkel")

    await world.relay(LastFMFromConfig(service="lastfm"), [PlexToConfig(service="plex")])

    assert track.userRating == 8.0


async def test_and_is_not_the_same_as_an_ampersand(world):
    """A known limitation, pinned: "and" and "&" normalize differently.

    So this goes to the unmatched queue — an error a human can resolve — instead
    of being guessed at or dropped. A loosening here would be a matching change,
    and it must move this test with it.
    """
    world.lastfm.love("Simon and Garfunkel", "The Boxer")
    track = world.plex.add("The Boxer", "Simon & Garfunkel")

    run = await world.relay(LastFMFromConfig(service="lastfm"), [PlexToConfig(service="plex")])

    assert track.userRating is None
    assert run.decisions() == [(OutcomeStatus.UNMATCHED, Reason.NO_MATCH)]
    assert run.results["plex"]["err"] == 1
    assert await synced(world.session) == []


async def test_two_plausible_copies_go_to_the_queue_with_their_candidates(world):
    """Contract §6: a rung that cannot decide proposes, and never writes."""
    world.lastfm.love("New Order", "Blue Monday")
    world.plex.add("Blue Monday (Remastered 2011)", "New Order")
    world.plex.add("Blue Monday (Deluxe Edition)", "New Order")

    run = await world.relay(LastFMFromConfig(service="lastfm"), [PlexToConfig(service="plex")])

    assert run.decisions() == [(OutcomeStatus.UNMATCHED, Reason.AMBIGUOUS_MATCH)]
    assert len(run.outcomes[0].matches) == 2
    assert world.plex.rated_titles() == []
    assert await synced(world.session) == []


async def test_a_track_with_no_recording_mbid_is_an_error(world):
    """No identity is a failed track, counted as an error, never a silent skip."""
    world.plex.add("Unknown Song", "Nobody", rating=10.0, track_mbid="track-0009")

    run = await world.relay(
        PlexFromConfig(service="plex"), [ListenBrainzToConfig(service="listenbrainz")]
    )

    assert run.decisions() == [
        (OutcomeStatus.UNMATCHED, Reason.NO_RECORDING_MBID)
    ]
    assert run.results["listenbrainz"]["err"] == 1
    assert run.results["listenbrainz"]["ok"] == 0
    assert world.listenbrainz.submissions == []
    assert await synced(world.session) == []


async def test_a_recording_missing_from_plex_is_an_error(world):
    """A love we cannot place is reported, not swallowed: it needs a human."""
    world.listenbrainz.love("missing", "New Order", "Blue Monday")

    run = await world.relay(
        ListenBrainzFromConfig(service="listenbrainz"), [PlexToConfig(service="plex")]
    )

    assert run.decisions() == [(OutcomeStatus.UNMATCHED, Reason.NO_MATCH)]
    assert run.results["plex"]["err"] == 1
    assert world.plex.rated_titles() == []


async def test_an_unrated_copy_is_not_hated(world):
    """A 0 is not a low rating: the guard keeps an empty `<<=` result from
    turning "rate the dislikes" into "rate the whole library"."""
    world.plex.add("Blue Monday", "New Order")
    world.plex.add("Love Will Tear Us Apart", "Joy Division", rating=1.0)
    world.brainz.add("Joy Division", "Love Will Tear Us Apart", "recording-hate")

    run = await world.relay(
        PlexFromConfig(service="plex", feedback_type="hate"),
        [ListenBrainzToConfig(service="listenbrainz")],
    )

    assert run.tracks_fetched == 1
    assert world.plex.section.calls == [{"userRating<<=": 2.0}]


async def test_a_run_with_nothing_rated_writes_nothing(world):
    world.plex.add("Blue Monday", "New Order", rating=None)

    run = await world.relay(
        PlexFromConfig(service="plex"), [ListenBrainzToConfig(service="listenbrainz")]
    )

    assert run.tracks_fetched == 0
    assert run.results["listenbrainz"] == {"ok": 0, "err": 0, "skipped": 0}
    assert world.listenbrainz.submissions == []


async def test_a_dry_run_takes_the_same_decisions_and_writes_nothing(world):
    """Contract §4: the preview is the pipeline, minus the writes."""
    world.brainz.add("New Order", "Blue Monday", "5c1f7a9e-8b1d-4c76-9a4e-0f2d3c4b5a61")
    world.plex.add("Blue Monday", "New Order", rating=10.0, track_mbid="track-0001")
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]

    preview = await world.relay(source, targets, dry_run=True)

    assert preview.decisions() == [(OutcomeStatus.WRITTEN, None)]
    assert world.listenbrainz.submissions == []
    assert await synced(world.session) == []

    real = await world.relay(source, targets)

    assert real.decisions() == preview.decisions()
    assert world.listenbrainz.submissions == [
        (1, "5c1f7a9e-8b1d-4c76-9a4e-0f2d3c4b5a61")
    ]


async def test_a_mass_undo_is_refused_unless_the_run_allows_it(world):
    """Contract §3: a source answering with nothing must not empty the target."""
    for index in range(12):
        mbid = f"recording-{index:02d}"
        world.brainz.add(f"Artist {index}", f"Song {index}", mbid)
        world.plex.add(
            f"Song {index}", f"Artist {index}", rating=10.0, track_mbid=f"track-{index:02d}"
        )
    source = PlexFromConfig(service="plex")
    targets = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(source, targets)
    assert len(await synced(world.session)) == 12

    for track in world.plex.section.tracks:
        track.userRating = None  # a truncated source answer looks exactly like this

    refused = await world.relay(source, targets)

    assert {decision for decision in refused.decisions()} == {
        (OutcomeStatus.FAILED, Reason.MASS_UNDO_REFUSED)
    }
    assert len(world.listenbrainz.scores) == 12
    assert len(await synced(world.session)) == 12

    allowed = await world.relay(source, targets, allow_mass_undo=True)

    assert {decision for decision in allowed.decisions()} == {
        (OutcomeStatus.REVERTED, Reason.WITHDRAWN)
    }
    assert world.listenbrainz.scores == {}
    assert await synced(world.session) == []


async def test_more_than_one_page_of_loves_is_read(world):
    """The source pages through the account; a second page is not silently dropped."""
    for index in range(150):
        world.listenbrainz.love(
            f"recording-{index:03d}", f"Artist {index}", f"Song {index}"
        )
    track = world.plex.add("Song 0", "Artist 0")

    run = await world.relay(
        ListenBrainzFromConfig(service="listenbrainz"), [PlexToConfig(service="plex")]
    )

    assert run.tracks_fetched == 150
    assert world.listenbrainz.page_requests == [(100, 0), (100, 100)]
    assert track.userRating == 8.0
    assert run.results["plex"] == {"ok": 1, "err": 149, "skipped": 0}


async def test_a_love_relay_and_a_hate_relay_do_not_reconcile_against_each_other(world):
    """One pair, two directions: each keeps its own records (contract §3)."""
    world.brainz.add("New Order", "Blue Monday", "recording-love")
    world.brainz.add("Joy Division", "Love Will Tear Us Apart", "recording-hate")
    world.plex.add("Blue Monday", "New Order", rating=10.0, track_mbid="track-0001")
    hated = world.plex.add(
        "Love Will Tear Us Apart", "Joy Division", rating=1.0, track_mbid="track-0002"
    )
    target = [ListenBrainzToConfig(service="listenbrainz")]
    await world.relay(PlexFromConfig(service="plex"), target)
    await world.relay(PlexFromConfig(service="plex", feedback_type="hate"), target)

    hated.userRating = None  # only the hate relay loses a track
    run = await world.relay(PlexFromConfig(service="plex", feedback_type="hate"), target)

    # The hate relay undid its own write and left the love relay's alone.
    assert world.listenbrainz.score("recording-hate") is None
    assert world.listenbrainz.score("recording-love") == 1
    assert run.decisions() == [(OutcomeStatus.REVERTED, Reason.WITHDRAWN)]
    assert len(await synced(world.session)) == 1


async def test_the_run_uses_the_connected_service_not_the_operators_config(world):
    """A tracked ``.config`` on the developer's machine must not change the suite."""
    world.plex.add("Blue Monday", "New Order", rating=10.0, track_mbid="track-0001")

    await world.relay(
        PlexFromConfig(service="plex"), [ListenBrainzToConfig(service="listenbrainz")]
    )

    assert world.plex.connections == [("http://plex.invalid:32400", "plex-token")]
    assert world.plex.requested_library == "Music"


async def test_a_hate_relay_cannot_target_lastfm(world):
    """A service that only records loves must fail the run, not love the hatred."""
    world.plex.add(
        "Love Will Tear Us Apart", "Joy Division", rating=1.0, track_mbid="track-0002"
    )

    run = await world.relay(
        PlexFromConfig(service="plex", feedback_type="hate"),
        [LastFMToConfig(service="lastfm")],
    )

    assert run.results["lastfm"] == {"ok": 0, "err": 1, "skipped": 0}
    assert world.lastfm.loved == {}
    assert await synced(world.session) == []
