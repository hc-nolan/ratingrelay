"""A relay whose intent is the track's own rating (``rating`` mode, RR-20).

Love and hate relays classify a track against a threshold and write the threshold.
A ``rating`` relay carries the value: Plex's 0-10 half-steps, hyperfix's 0-100
divided by ten, and equality on the target's own scale instead of a band. These
tests pin the three things that makes true — what is written, what counts as
already-held, and what a run does on the second pass.
"""

import json
from typing import Optional

import pytest

from ratingrelay.engine import MemoryMappingStore, MemorySyncStore, OutcomeStatus, Reason, Track
from ratingrelay.engine.services.hyperfix import HyperfixSource, HyperfixTarget, ZERO_RATING
from ratingrelay.engine.services.plex import PlexSource, PlexTarget
from ratingrelay.engine.types import (
    HYPERFIX_RATING_FLOOR,
    PLEX_RATING_FLOOR,
    Intent,
    IntentKind,
)

from tests.test_hyperfix_target import FakeHyperfix  # pylint: disable=wrong-import-order


class FakePlexLibrary:
    """Just enough PlexLibrary: a candidate list, rate() and current_rating()."""

    def __init__(self, copies: list[tuple[str, Optional[float]]]):
        self.copies = dict(copies)
        self.writes: list[tuple[str, Optional[float]]] = []

    async def candidates(self):
        from ratingrelay.engine import Candidate  # pylint: disable=import-outside-toplevel

        return tuple(
            Candidate("Wire", "Field Day", external_id=key, rating=rating)
            for key, rating in self.copies.items()
        )

    async def rate(self, external_id: str, rating: Optional[float]) -> None:
        self.writes.append((external_id, rating))
        self.copies[external_id] = rating

    async def current_rating(self, external_id: str) -> Optional[float]:
        return self.copies.get(external_id)


def _plex_target(library: FakePlexLibrary, direction: str = "rating") -> PlexTarget:
    return PlexTarget(library, MemoryMappingStore(), direction=direction)


def _hyperfix_target(fake: FakeHyperfix, direction: str = "rating") -> HyperfixTarget:
    async def no_lookup(track_mbid, artist, title):  # noqa: ARG001 - the port's signature
        raise AssertionError("exact mode resolves like any other")

    return HyperfixTarget(
        fake.client(),
        MemoryMappingStore(),
        no_lookup,
        direction=direction,
    )


def _resolve(mbid: str):
    """A MusicBrainz lookup that answers *mbid*."""

    async def resolve(track_mbid, artist, title):  # noqa: ARG001 - the port's signature
        from ratingrelay.engine.services.musicbrainz import (  # pylint: disable=import-outside-toplevel
            Resolution,
        )

        return Resolution(mbid)

    return resolve


# ---------------------------------------------------------------------------
# Plex: the value is the intent, and equality is "already holds it"
# ---------------------------------------------------------------------------


async def test_a_plex_target_writes_the_tracks_own_rating():
    library = FakePlexLibrary([("1", None)])
    target = _plex_target(library)

    plan = await target.plan(Track("Wire", "Field Day", rating=8.5))

    assert plan.status is OutcomeStatus.WRITTEN
    assert plan.intent == Intent(IntentKind.RATING, 8.5)
    await plan.commit()
    assert library.writes == [("1", 8.5)]


async def test_a_copy_already_at_that_rating_holds_it():
    library = FakePlexLibrary([("1", 8.5)])
    target = _plex_target(library)

    plan = await target.plan(Track("Wire", "Field Day", rating=8.5))

    assert plan.existing.holds


async def test_a_copy_at_another_rating_is_written_and_the_user_is_warned():
    """Exact mode converges — it does not respect a band, because there is none.

    The pipeline, not the target, decides whether the difference is the user's own
    edit: with no record the write is skipped as theirs, with one it converges.
    """
    library = FakePlexLibrary([("1", 6.0)])
    target = _plex_target(library)

    plan = await target.plan(Track("Wire", "Field Day", rating=8.5))

    assert not plan.existing.holds
    assert plan.existing.occupied


async def test_a_love_relay_still_writes_its_threshold():
    """The band is unchanged for the other two directions."""
    library = FakePlexLibrary([("1", 4.0)])
    target = _plex_target(library, direction="love")

    plan = await target.plan(Track("Wire", "Field Day", rating=8.5))

    await plan.commit()
    assert library.writes == [("1", 8.0)]


async def test_an_exact_plan_without_a_rating_writes_nothing():
    library = FakePlexLibrary([("1", None)])
    target = _plex_target(library)

    plan = await target.plan(Track("Wire", "Field Day"))

    assert plan.status is OutcomeStatus.SKIPPED
    assert plan.reason is Reason.MISSING_METADATA
    assert not plan.actionable


# ---------------------------------------------------------------------------
# hyperfix: the same value on its own scale
# ---------------------------------------------------------------------------


async def test_a_hyperfix_target_writes_the_rating_as_hundredths():
    fake = FakeHyperfix()
    target = HyperfixTarget(
        fake.client(),
        MemoryMappingStore(),
        _resolve("rec-1"),
        direction="rating",
    )

    plan = await target.plan(Track("Wire", "Field Day", rating=8.5))
    await plan.commit()

    assert fake.ratings == {"rec-1": 85}
    assert json.loads(fake.writes()[0][1])["rating"] == 85


async def test_a_hyperfix_recording_at_that_value_holds_it():
    fake = FakeHyperfix({"rec-1": 85})
    target = HyperfixTarget(
        fake.client(),
        MemoryMappingStore(),
        _resolve("rec-1"),
        direction="rating",
    )

    plan = await target.plan(Track("Wire", "Field Day", rating=8.5))

    assert plan.existing.holds


async def test_hyperfix_compares_on_its_own_scale_so_a_round_trip_is_stable():
    """85 reads as 8.5 and 8.5 writes as 85: neither side drifts on the second run."""
    fake = FakeHyperfix({"rec-1": 85})
    target = HyperfixTarget(
        fake.client(),
        MemoryMappingStore(),
        _resolve("rec-1"),
        direction="rating",
    )

    plan = await target.plan(Track("Wire", "Field Day", rating=8.5))

    assert target.holds(85, 8.5)
    assert target.holds(90, 9.0)
    assert not target.holds(85, 9.0)
    assert plan.to_outcome().reason is None


# ---------------------------------------------------------------------------
# Sources: a rating relay reads every rated track, not a band
# ---------------------------------------------------------------------------


async def test_a_plex_source_at_the_floor_reads_every_rated_track():
    """The floor is half a star, which is what "rated at all" means in Plex.

    Three stars is invisible to a love relay (8) and to a hate relay (2); it is the
    whole reason for this mode. An unrated track never passes, at any threshold.
    """
    from ratingrelay.engine.services.plex import _passes_rating  # pylint: disable=import-outside-toplevel

    assert _passes_rating(3.0, PLEX_RATING_FLOOR, "gte")
    assert _passes_rating(10.0, PLEX_RATING_FLOOR, "gte")
    assert not _passes_rating(None, PLEX_RATING_FLOOR, "gte")
    assert not _passes_rating(0, PLEX_RATING_FLOOR, "gte")
    assert not _passes_rating(3.0, 8.0, "gte")
    assert not _passes_rating(3.0, 2.0, "lte")


async def test_a_hyperfix_source_at_the_floor_reads_every_rated_recording():
    fake = FakeHyperfix({"rec-1": 30})  # three stars: invisible to both love and hate
    feed = HyperfixSource(fake.client(), HYPERFIX_RATING_FLOOR, "gte")

    tracks = await feed.fetch()

    assert [(t.artist, t.title, t.rating) for t in tracks] == [("Wire", "Field Day", 3.0)]
    assert HYPERFIX_RATING_FLOOR == ZERO_RATING


# ---------------------------------------------------------------------------
# A run: converges once, then holds — and never adopts a value it did not write
# ---------------------------------------------------------------------------


def _resolver_by_title():
    """A MusicBrainz lookup whose mbid is derived from the title, so tests can read it."""

    async def resolve(track_mbid, artist, title):  # noqa: ARG001 - the port's signature
        from ratingrelay.engine.services.musicbrainz import (  # pylint: disable=import-outside-toplevel
            Resolution,
        )

        return Resolution(f"rec-{title}")

    return resolve


def _run_target(fake: FakeHyperfix) -> HyperfixTarget:
    return HyperfixTarget(
        fake.client(), MemoryMappingStore(), _resolver_by_title(), direction="rating"
    )


async def test_a_rating_run_writes_then_settles_then_follows_a_change():
    from ratingrelay.engine import pipeline  # pylint: disable=import-outside-toplevel

    fake = FakeHyperfix()
    target = _run_target(fake)
    store = MemorySyncStore()

    first = await pipeline.run_tracks(
        [Track("Wire", "Field Day", rating=8.5)], [target], source_service="plex", sync_store=store
    )
    second = await pipeline.run_tracks(
        [Track("Wire", "Field Day", rating=8.5)], [target], source_service="plex", sync_store=store
    )
    third = await pipeline.run_tracks(
        [Track("Wire", "Field Day", rating=9.0)], [target], source_service="plex", sync_store=store
    )

    assert first.outcomes[0].status is OutcomeStatus.WRITTEN
    assert second.outcomes[0].reason is Reason.ALREADY_SYNCED
    assert third.outcomes[0].status is OutcomeStatus.WRITTEN
    assert fake.ratings == {"rec-Field Day": 90}
    # One record, re-written: the record is the relay's memory of what it wrote.
    assert store.records[("plex", "hyperfix")]["wire||field day"].intent == "rating:9.0"


async def test_a_value_the_user_set_on_the_target_is_never_adopted():
    """No record means the value is theirs, at the same number or any other."""
    from ratingrelay.engine import pipeline  # pylint: disable=import-outside-toplevel

    fake = FakeHyperfix({"rec-Field Day": 85})
    target = _run_target(fake)
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        [Track("Wire", "Field Day", rating=9.0)], [target], source_service="plex", sync_store=store
    )

    assert report.outcomes[0].reason is Reason.ALREADY_ON_TARGET
    assert fake.ratings == {"rec-Field Day": 85}
    assert store.records == {}


# ---------------------------------------------------------------------------
# The API and the config file offer the direction only where it can work
# ---------------------------------------------------------------------------


def test_plex_and_hyperfix_sources_accept_a_rating_relay():
    from ratingrelay.schemas.relay import (  # pylint: disable=import-outside-toplevel
        HyperfixFromConfig,
        PlexFromConfig,
    )

    assert PlexFromConfig(service="plex", feedback_type="rating").feedback_type == "rating"
    assert (
        HyperfixFromConfig(service="hyperfix", feedback_type="rating").feedback_type
        == "rating"
    )


def test_a_service_without_stars_cannot_be_asked_for_a_rating_relay():
    from pydantic import ValidationError  # pylint: disable=import-outside-toplevel

    from ratingrelay.schemas.relay import (  # pylint: disable=import-outside-toplevel
        ListenBrainzFromConfig,
    )

    with pytest.raises(ValidationError):
        ListenBrainzFromConfig(service="listenbrainz", feedback_type="rating")
