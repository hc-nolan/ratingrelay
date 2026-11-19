"""The Plex ratings that mean love and hate are shared by every relay.

They come from settings or the UI: a threshold set explicitly in settings pins
it, otherwise the value saved from the UI applies, otherwise the default.

A Plex source reads loves at or above ``love_threshold`` and hates at or below
``hate_threshold``; a Plex target writes the threshold for the relay's
direction. One number per direction keeps a two-way setup from writing back
something other than what it reads.
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ratingrelay.core import factory
from ratingrelay.routes import relay as relay_routes
from ratingrelay.db.thresholds import save_thresholds
from ratingrelay.engine.types import Track
from ratingrelay.schemas.relay import ThresholdsUpdate
from ratingrelay.settings import Settings


@pytest.fixture
async def session(db_engine):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        yield db


def _settings(monkeypatch, module, **pinned):
    """Settings with only *pinned* set explicitly, as env or .config would."""
    monkeypatch.delenv("LOVE_THRESHOLD", raising=False)
    monkeypatch.delenv("HATE_THRESHOLD", raising=False)
    monkeypatch.setattr(module, "get_settings", lambda: Settings(_env_file=None, **pinned))


async def _no_credentials(_db, _service="plex"):
    return None


class _Library:
    """Records what a Plex source asks the server for."""

    def __init__(self, *_):
        self.asked: list[tuple[float, str]] = []

    async def rated(self, threshold: float, comparison: str) -> list:
        self.asked.append((threshold, comparison))
        return []


@pytest.fixture
def thresholds(monkeypatch, session):
    """Love at 6 (3 stars) and hate at 3, so neither is the default."""
    monkeypatch.setattr(
        factory, "get_settings", lambda: Settings(love_threshold=6.0, hate_threshold=3.0)
    )

    async def no_credentials(_db, _service="plex"):
        return None

    monkeypatch.setattr(factory, "plex_credentials", no_credentials)
    monkeypatch.setattr(factory, "PlexLibrary", _Library)


@pytest.mark.parametrize(
    "feedback_type, asked", [("love", (6.0, "gte")), ("hate", (3.0, "lte"))]
)
async def test_a_plex_source_reads_the_threshold_for_its_direction(
    thresholds, session, feedback_type, asked
):
    source = await factory.source_for_service(session, "plex", feedback_type=feedback_type)
    await source.fetch()

    assert source.library.asked == [asked]


@pytest.mark.parametrize(
    "relay, held, not_held", [("love", 6.0, 5.0), ("hate", 3.0, 4.0)]
)
async def test_a_plex_target_writes_the_threshold_for_its_direction(
    thresholds, session, relay, held, not_held
):
    target = await factory.target_for_service(
        session, "plex", plex_library=_Library(), relay=relay
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


async def test_the_ui_can_read_the_thresholds(monkeypatch, session):
    _settings(monkeypatch, relay_routes, love_threshold=6.0, hate_threshold=3.0)
    body = await relay_routes.thresholds(session)

    assert (body.love_threshold, body.hate_threshold) == (6.0, 3.0)
    assert body.love_locked and body.hate_locked


@pytest.fixture
def unpinned(monkeypatch):
    """Neither threshold in settings, so the UI may set both."""
    for module in (relay_routes, factory):
        _settings(monkeypatch, module)


async def test_a_saved_threshold_is_what_a_relay_uses(unpinned, session, monkeypatch):
    monkeypatch.setattr(factory, "plex_credentials", _no_credentials)
    monkeypatch.setattr(factory, "PlexLibrary", _Library)
    body = await relay_routes.update_thresholds(
        ThresholdsUpdate(love_threshold=10, hate_threshold=4), session
    )
    source = await factory.source_for_service(session, "plex", feedback_type="hate")
    await source.fetch()
    target = await factory.target_for_service(
        session, "plex", plex_library=_Library(), relay="love"
    )

    assert (body.love_threshold, body.hate_threshold) == (10.0, 4.0)
    assert source.library.asked == [(4.0, "lte")]
    assert target.holds(10.0) and not target.holds(9.0)
    assert (await relay_routes.thresholds(session)).love_threshold == 10.0


async def test_without_a_saved_threshold_the_default_applies(unpinned, session):
    assert await factory.plex_threshold(session, "love") == 8.0
    assert await factory.plex_threshold(session, "hate") == 2.0


async def test_a_threshold_in_settings_beats_the_saved_one(monkeypatch, session):
    _settings(monkeypatch, factory, love_threshold=6.0)
    await save_thresholds(session, love=10.0, hate=4.0)

    assert await factory.plex_threshold(session, "love") == 6.0
    assert await factory.plex_threshold(session, "hate") == 4.0


async def test_a_threshold_in_settings_cannot_be_changed_from_the_ui(monkeypatch, session):
    _settings(monkeypatch, relay_routes, love_threshold=6.0)
    with pytest.raises(HTTPException) as refused:
        await relay_routes.update_thresholds(ThresholdsUpdate(love_threshold=10), session)
    body = await relay_routes.update_thresholds(
        ThresholdsUpdate(love_threshold=6, hate_threshold=1), session
    )

    assert refused.value.status_code == 409
    assert (body.love_threshold, body.hate_threshold) == (6.0, 1.0)
    assert body.love_locked and not body.hate_locked


@pytest.mark.parametrize(
    "update", [{"love_threshold": 4, "hate_threshold": 4}, {"hate_threshold": 8}]
)
async def test_the_ui_cannot_overlap_the_thresholds(unpinned, session, update):
    # The second case overlaps only against the love threshold already in force.
    with pytest.raises(HTTPException) as refused:
        await relay_routes.update_thresholds(ThresholdsUpdate(**update), session)

    assert refused.value.status_code == 422
    assert (await relay_routes.thresholds(session)).hate_threshold == 2.0


@pytest.mark.parametrize("value", [0, 11])
def test_a_ui_threshold_stays_on_the_plex_scale(value):
    with pytest.raises(ValidationError):
        ThresholdsUpdate(love_threshold=value)


async def test_a_run_refuses_a_pinned_threshold_the_saved_one_overlaps(monkeypatch, session):
    # Saved from the UI first, then settings pinned the other side past it.
    await save_thresholds(session, love=None, hate=5.0)
    _settings(monkeypatch, factory, love_threshold=4.0)

    with pytest.raises(ValueError, match="must be below"):
        await factory.plex_threshold(session, "love")


def test_settings_only_check_a_pair_they_set():
    # One side alone may sit past the other's default: the UI's value may move it.
    assert Settings(_env_file=None, hate_threshold=9.0).hate_threshold == 9.0


@pytest.mark.parametrize("source", ["plex", "listenbrainz"])
def test_a_hate_relay_cannot_target_lastfm(source):
    relay = factory.relay_identity(source, feedback_type="hate")

    assert factory.unsupported_target(relay, "lastfm")
    assert factory.unsupported_target(relay, "listenbrainz") is None
    assert factory.unsupported_target("love", "lastfm") is None


async def test_a_stored_hate_job_cannot_build_a_lastfm_target(thresholds):
    # A job queued before the pairing was refused reaches the factory directly;
    # it must fail there instead of loving what the user hates.
    with pytest.raises(ValueError, match="hate relay"):
        await factory.target_for_service(None, "lastfm", relay="hate")


@pytest.mark.parametrize("relay, score", [("love", 1), ("hate", -1)])
async def test_a_listenbrainz_target_writes_the_relay_direction(monkeypatch, relay, score):
    async def credentials(_db, _service="listenbrainz"):
        return None

    monkeypatch.setattr(factory, "listenbrainz_credentials", credentials)
    monkeypatch.setattr(factory, "ListenBrainzClient", lambda _creds: object())
    monkeypatch.setattr(factory, "mbid_cache", lambda _db: None)
    target = await factory.target_for_service(None, "listenbrainz", relay=relay)

    assert target._score == score


async def test_an_older_listenbrainz_target_direction_is_ignored(client):
    # Before targets followed the source, a ListenBrainz target had its own
    # direction, so a hate relay could be stored with a "love" target. It must
    # read back (and run) as the relay's direction, not its own.
    hates = {"service": "plex", "feedback_type": "hate"}
    old_target = {"service": "listenbrainz", "feedback_type": "love"}
    resp = await client.post(
        "/api/relay/jobs", json={"source": hates, "targets": [old_target]}
    )

    assert resp.status_code == 201
    assert resp.json()["targets_config"] == [{"service": "listenbrainz"}]


# ---------------------------------------------------------------------------
# ``rating`` mode: the track's own value, not a threshold (RR-20)
# ---------------------------------------------------------------------------


async def test_a_rating_source_reads_every_rated_track(monkeypatch, session):
    monkeypatch.setattr(factory, "plex_credentials", _no_credentials)
    monkeypatch.setattr(factory, "PlexLibrary", _Library)
    source = await factory.source_for_service(session, "plex", feedback_type="rating")
    await source.fetch()

    # Half a star is Plex's floor, so the server-side filter is "rated at all".
    assert source.library.asked == [(1.0, "gte")]


async def test_a_rating_target_carries_the_tracks_own_value(thresholds, session):
    target = await factory.target_for_service(
        session, "plex", plex_library=_Library(), relay="rating"
    )

    assert target.wanted(Track("Wire", "Field Day", rating=8.5)) == 8.5
    assert target.holds(8.5, 8.5)
    assert not target.holds(8.5, 9.0)
    # A love relay on the same pair still writes its threshold.
    love = await factory.target_for_service(
        session, "plex", plex_library=_Library(), relay="love"
    )
    assert love.wanted(Track("Wire", "Field Day", rating=8.5)) == love._rating


def test_a_rating_relay_is_its_own_identity():
    assert factory.relay_identity("plex", feedback_type="rating") == "rating"
    assert factory.relay_identity("hyperfix", feedback_type="rating") == "rating"


@pytest.mark.parametrize("target", ["lastfm", "listenbrainz"])
def test_a_rating_relay_cannot_target_services_without_stars(target):
    assert factory.unsupported_target("rating", target)
    assert factory.unsupported_target("love", target) is None
    assert factory.unsupported_target("rating", "plex") is None
    assert factory.unsupported_target("rating", "hyperfix") is None


@pytest.mark.parametrize("target", ["lastfm", "listenbrainz"])
async def test_a_rating_target_is_refused_before_anything_is_fetched(target):
    with pytest.raises(ValueError, match="no star rating"):
        await factory.target_for_service(None, target, relay="rating")


@pytest.mark.parametrize("source", ["lastfm", "listenbrainz"])
async def test_services_without_stars_cannot_be_read_as_ratings(source):
    with pytest.raises(ValueError, match="no star rating"):
        await factory.source_for_service(None, source, feedback_type="rating")
