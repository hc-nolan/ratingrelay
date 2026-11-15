"""The Plex source asks the server for rated tracks instead of filtering a copy.

Fetching the whole library and filtering in Python is what v2 did, and on a
large library it is the slowest part of a run. These tests pin the request that
replaces it, and the guard that keeps a surprising server from widening it.
"""

from dataclasses import dataclass, field

import pytest

from ratingrelay.engine.credentials import PlexCredentials
from ratingrelay.engine.services.plex import PlexLibrary, PlexSource

CREDS = PlexCredentials(server_url="http://plex.invalid:32400", token="tok", library="Music")


@dataclass
class FakePlexTrack:
    title: str
    userRating: float | None = None
    ratingKey: str = "1"
    grandparentTitle: str = "Artist"
    guids: list = field(default_factory=list)


class FakeSection:
    """Behaves like plexapi's section: filters are applied server-side."""

    def __init__(self, tracks: list[FakePlexTrack], reject_filters: bool = False):
        self._tracks = tracks
        self._reject = reject_filters
        self.calls: list[dict] = []

    def searchTracks(self, **filters):
        self.calls.append(filters)
        if filters and self._reject:
            raise ValueError("bad filter")
        if not filters:
            return list(self._tracks)
        (field_name, value), = filters.items()
        rating = float(value)
        if field_name == "userRating>>=":
            return [t for t in self._tracks if (t.userRating or 0) >= rating]
        if field_name == "userRating<<=":
            return [t for t in self._tracks if 0 < (t.userRating or 0) <= rating]
        raise AssertionError(f"unexpected filter {field_name}")


def _library(section: FakeSection) -> PlexLibrary:
    """A library with its section pre-seeded, so no Plex server is contacted."""
    library = PlexLibrary(CREDS)
    library._section = section  # pylint: disable=protected-access
    return library


async def test_rated_asks_plex_for_the_comparison():
    section = FakeSection(
        [
            FakePlexTrack("Low", 3.0),
            FakePlexTrack("High", 9.5),
            FakePlexTrack("Top", 10.0),
        ]
    )

    rated = await _library(section).rated(8.0, "gte")

    assert [track.title for track in rated] == ["High", "Top"]
    # One server-side query; the whole library was never fetched.
    assert section.calls == [{"userRating>>=": 8.0}]


async def test_rated_uses_the_lower_bound_operator_for_lte():
    section = FakeSection(
        [
            FakePlexTrack("Unrated", None),
            FakePlexTrack("Disliked", 2.0),
            FakePlexTrack("Loved", 9.0),
        ]
    )

    rated = await _library(section).rated(4.0, "lte")

    assert [track.title for track in rated] == ["Disliked"]
    assert section.calls == [{"userRating<<=": 4.0}]


async def test_rated_never_includes_unrated_tracks():
    """Unrated counts as 0, so "rate below 4" must not sweep up the library."""
    section = FakeSection([FakePlexTrack("Unrated", None)])
    # The server returns it anyway, as a server that ignores the operator might.
    assert section.searchTracks(**{"userRating<<=": 4.0}) == []
    section = FakeSection([FakePlexTrack("Unrated", None)])

    rated = await _library(section).rated(4.0, "lte")

    assert rated == []


async def test_rated_falls_back_to_a_local_filter_when_plex_rejects_it(caplog):
    section = FakeSection([FakePlexTrack("Low", 3.0), FakePlexTrack("High", 9.5)], reject_filters=True)

    rated = await _library(section).rated(8.0, "gte")

    assert [track.title for track in rated] == ["High"]
    assert section.calls == [{"userRating>>=": 8.0}, {}]
    assert "falling back to a local filter" in caplog.text


async def test_source_fetch_keeps_the_rating_it_read():
    section = FakeSection([FakePlexTrack("High", 9.5, ratingKey="42")])
    source = PlexSource(_library(section), threshold=8.0, comparison="gte")

    tracks = await source.fetch()

    assert [(track.title, track.rating, track.external_id) for track in tracks] == [
        ("High", 9.5, "42")
    ]
    assert source.service == "plex"


async def test_unknown_comparison_is_rejected_before_a_query():
    section = FakeSection([FakePlexTrack("High", 9.5)])

    with pytest.raises(KeyError):
        await _library(section).rated(8.0, "around")

    assert section.calls == []
