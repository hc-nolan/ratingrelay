"""The Plex ratings that mean love and hate are settings, shared by every relay.

A Plex source reads loves at or above ``love_threshold`` and hates at or below
``hate_threshold``; a Plex target writes the threshold for the relay's
direction. One number per direction keeps a two-way setup from writing back
something other than what it reads.
"""

import pytest
from pydantic import ValidationError

from ratingrelay.core import factory
from ratingrelay.routes import relay as relay_routes
from ratingrelay.settings import Settings


class _Library:
    """Records what a Plex source asks the server for."""

    def __init__(self, *_):
        self.asked: list[tuple[float, str]] = []

    async def rated(self, threshold: float, comparison: str) -> list:
        self.asked.append((threshold, comparison))
        return []


@pytest.fixture
def thresholds(monkeypatch):
    """Love at 6 (3 stars) and hate at 3, so neither is the default."""
    monkeypatch.setattr(
        factory, "get_settings", lambda: Settings(love_threshold=6.0, hate_threshold=3.0)
    )

    async def no_credentials(_db):
        return None

    monkeypatch.setattr(factory, "plex_credentials", no_credentials)
    monkeypatch.setattr(factory, "PlexLibrary", _Library)


@pytest.mark.parametrize(
    "feedback_type, asked", [("love", (6.0, "gte")), ("hate", (3.0, "lte"))]
)
async def test_a_plex_source_reads_the_threshold_for_its_direction(
    thresholds, feedback_type, asked
):
    source = await factory.source_for_service(None, "plex", feedback_type=feedback_type)
    await source.fetch()

    assert source.library.asked == [asked]


@pytest.mark.parametrize(
    "relay, held, not_held", [("love", 6.0, 5.0), ("hate", 3.0, 4.0)]
)
async def test_a_plex_target_writes_the_threshold_for_its_direction(
    thresholds, relay, held, not_held
):
    target = await factory.target_for_service(
        None, "plex", plex_library=_Library(), relay=relay
    )

    assert target.holds(held)
    assert not target.holds(not_held)


def test_plex_direction_is_the_relay_identity():
    assert factory.relay_identity("plex") == "love"
    assert factory.relay_identity("plex", feedback_type="hate") == "hate"


@pytest.mark.parametrize("love, hate", [(6.0, 6.0), (4.0, 6.0)])
def test_the_thresholds_cannot_overlap(love, hate):
    with pytest.raises(ValidationError, match="must be below"):
        Settings(love_threshold=love, hate_threshold=hate)


@pytest.mark.parametrize("field", ["love_threshold", "hate_threshold"])
def test_a_threshold_stays_on_the_plex_scale(field):
    with pytest.raises(ValidationError):
        Settings(**{field: 11.0})


def test_the_defaults_are_four_stars_and_one(monkeypatch):
    monkeypatch.delenv("LOVE_THRESHOLD", raising=False)
    monkeypatch.delenv("HATE_THRESHOLD", raising=False)
    current = Settings(_env_file=None)

    assert (current.love_threshold, current.hate_threshold) == (8.0, 2.0)


async def test_the_ui_can_read_the_thresholds(monkeypatch):
    monkeypatch.setattr(
        relay_routes, "get_settings", lambda: Settings(love_threshold=6.0, hate_threshold=3.0)
    )
    body = await relay_routes.thresholds()

    assert (body.love_threshold, body.hate_threshold) == (6.0, 3.0)
