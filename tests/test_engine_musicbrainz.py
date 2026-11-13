"""MusicBrainz resolution: an exact hit or nothing.

A recording MBID is what ListenBrainz feedback is keyed by, so a wrong answer here
does not fail loudly — it loves a different recording. These tests pin that the
lookup only trusts the exact rung of the matching ladder, and never the order
MusicBrainz happened to return.
"""

from typing import Optional

import pytest

from ratingrelay.engine.services import musicbrainz


def _recording(
    mbid: str, artist: str, title: str, *, drop_credit: bool = False
) -> dict:
    recording = {"id": mbid, "title": title}
    if not drop_credit:
        recording["artist-credit"] = [{"name": artist}]
    return recording


@pytest.fixture
def search(monkeypatch):
    """Stub the MusicBrainz search; returns the list of queries it received."""

    def _install(results: list[dict]) -> list[dict]:
        calls: list[dict] = []

        def fake_search(**kwargs):
            calls.append(kwargs)
            return {"recording-list": results}

        monkeypatch.setattr(musicbrainz.mbz, "search_recordings", fake_search)
        return calls

    return _install


def test_exact_match_wins_over_the_first_result(search):
    """Rank 1 is not a match; the exact artist and title is."""
    calls = search(
        [
            _recording("mbid-wrong", "Sigur Ros", "Hoppipolla (Live)"),
            _recording("mbid-right", "Sigur Rós", "Hoppípolla"),
        ]
    )

    resolved = musicbrainz._lookup(None, "Sigur Rós", "Hoppípolla")

    assert resolved == "mbid-right"
    assert all("tid:" not in call.get("query", "") for call in calls)


def test_a_fuzzy_first_result_is_not_guessed_at(search):
    """The bug this replaces: taking recordings[0] wrote a love on a guess."""
    search([_recording("mbid-maybe", "Sigur Ros", "Hoppipolla")])

    assert musicbrainz._lookup(None, "Sigur Rós", "Hoppípolla") is None


def test_no_results_is_a_miss_not_an_error(search):
    search([])

    assert musicbrainz._lookup(None, "Artist A", "Song A") is None


def test_malformed_results_are_skipped(search):
    """A result without artist credit cannot be compared and must not crash."""
    search(
        [
            _recording("mbid-broken", "", "", drop_credit=True),
            _recording("mbid-right", "Artist A", "Song A"),
        ]
    )

    assert musicbrainz._lookup(None, "Artist A", "Song A") == "mbid-right"


def test_a_non_exact_tier_is_not_used(search):
    """Variant and containment normally fold editions away; here they must not."""
    search([_recording("mbid-live", "Artist A", "Song A (Remastered 2011)")])

    assert musicbrainz._lookup(None, "Artist A", "Song A") is None


def test_a_track_mbid_shortcuts_the_search(search):
    calls = search([_recording("mbid-from-track", "Artist A", "Song A")])

    resolved = musicbrainz._lookup("track-mbid", "Artist A", "Song A")

    assert resolved == "mbid-from-track"
    assert calls == [{"query": "tid:track-mbid", "limit": 1}]


def test_a_search_failure_is_a_miss(monkeypatch):
    """A network error must not propagate as a write failure."""

    def boom(**kwargs):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(musicbrainz.mbz, "search_recordings", boom)

    assert musicbrainz._lookup(None, "Artist A", "Song A") is None
    assert musicbrainz._lookup("track-mbid", "Artist A", "Song A") is None
