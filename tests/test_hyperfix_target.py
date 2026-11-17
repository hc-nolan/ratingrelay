"""hyperfix as a *target*: writing a rating into its music module (HF-198).

The source half lives in ``test_hyperfix_source.py``. Here the question is what a
run writes, what it leaves alone, and what undo puts back — against a fake
hyperfix that records every request, so nothing touches the network.
"""

from typing import Any, Optional

import json

import httpx
import pytest

from ratingrelay.engine import MemoryMappingStore, OutcomeStatus, Reason, Track
from ratingrelay.engine.credentials import HyperfixCredentials
from ratingrelay.engine.services.hyperfix import HyperfixTarget
from ratingrelay.engine.services.musicbrainz import Resolution
from ratingrelay.engine.store import SyncedTrack

CREDS = HyperfixCredentials(base_url="https://hfx.test", token="hfx_key")


class FakeHyperfix:
    """A hyperfix instance holding ``{mbid: rating}``, remembering every request."""

    def __init__(self, ratings: Optional[dict[str, int]] = None):
        self.ratings = dict(ratings or {})
        self.requests: list[tuple[str, str, Any]] = []

    def client(self, page_size: int = 500):
        from ratingrelay.engine.services.hyperfix import (  # pylint: disable=import-outside-toplevel
            HyperfixClient,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            self.requests.append((request.method, path, request.content or None))
            if path == "/api/music/ratings":
                items = [
                    {
                        "recording_mbid": mbid,
                        "rating": rating,
                        "artist": "Wire",
                        "title": "Field Day",
                        "release_mbid": "release-1",
                    }
                    for mbid, rating in self.ratings.items()
                ]
                return httpx.Response(200, json={"items": items, "next_cursor": None})
            mbid = path.rsplit("/", 1)[-1]
            if request.method == "PATCH":
                rating = json.loads(request.content)["rating"]
                if rating is None:
                    self.ratings.pop(mbid, None)
                else:
                    self.ratings[mbid] = rating
                return httpx.Response(200, json={"mbid": mbid, "rating": rating})
            if mbid not in self.ratings:
                return httpx.Response(404, json={"detail": "no such recording"})
            return httpx.Response(200, json={"mbid": mbid, "rating": self.ratings[mbid]})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return HyperfixClient(CREDS, page_size=page_size, http=http)

    def writes(self) -> list[Any]:
        return [
            (path, body)
            for method, path, body in self.requests
            if method == "PATCH"
        ]


def _resolver(mbid: Optional[str]):
    """A MusicBrainz lookup that answers with *mbid*, or with nothing."""

    async def resolve(track_mbid, artist, title):  # noqa: ARG001 - signature is the port
        return Resolution(mbid) if mbid else Resolution()

    return resolve


async def _no_lookup(track_mbid, artist, title):  # noqa: ARG001 - signature is the port
    raise AssertionError("this test expected no MusicBrainz lookup")


def _target(
    fake: FakeHyperfix,
    *,
    rating: float = 8.0,
    direction: str = "love",
    resolve=_no_lookup,
) -> HyperfixTarget:
    return HyperfixTarget(fake.client(), MemoryMappingStore(), resolve, rating, direction)


@pytest.mark.anyio
async def test_a_love_is_written_as_hyperfix_hundredths():
    fake = FakeHyperfix()
    target = _target(fake, resolve=_resolver("rec-1"))

    plan = await target.plan(Track("Wire", "Field Day"))
    assert plan.status is OutcomeStatus.WRITTEN
    assert plan.intent.rating == 8.0
    assert (plan.target_key, plan.recording_mbid) == ("rec-1", "rec-1")

    await plan.commit()
    assert fake.ratings == {"rec-1": 80}
    assert fake.writes() == [("/api/music/recordings/rec-1", b'{"rating":80}')]


@pytest.mark.anyio
async def test_a_hate_is_written_as_the_hate_threshold():
    fake = FakeHyperfix()
    target = _target(fake, rating=2.0, direction="hate", resolve=_resolver("rec-1"))

    plan = await target.plan(Track("Wire", "Field Day"))
    await plan.commit()
    assert fake.ratings == {"rec-1": 20}


@pytest.mark.anyio
async def test_a_rating_the_user_already_chose_is_left_alone():
    """A 95 under a love relay is the user's, not something to bump down to 80."""
    fake = FakeHyperfix({"rec-1": 95})
    target = _target(fake, resolve=_resolver("rec-1"))

    plan = await target.plan(Track("Wire", "Field Day"))
    assert plan.existing.holds is True
    assert plan.existing.prior == "95"
    assert plan.existing.occupied is True


@pytest.mark.anyio
async def test_a_hate_leaves_a_deeper_hate_alone():
    fake = FakeHyperfix({"rec-1": 5})
    target = _target(fake, rating=2.0, direction="hate", resolve=_resolver("rec-1"))

    plan = await target.plan(Track("Wire", "Field Day"))
    assert plan.existing.holds is True


@pytest.mark.anyio
async def test_a_hyperfix_zero_counts_as_rated_not_as_missing():
    """hyperfix's 0 is a rating; this engine reads 0.0 as unrated, so 0 arrives as 0.1."""
    fake = FakeHyperfix({"rec-1": 0})
    target = _target(fake, rating=2.0, direction="hate", resolve=_resolver("rec-1"))

    plan = await target.plan(Track("Wire", "Field Day"))
    assert plan.existing.holds is True
    assert plan.existing.prior == "1"
    assert plan.existing.occupied is True


@pytest.mark.anyio
async def test_an_unrated_track_is_not_occupied():
    """Nothing there at all: a write that was removed, which drift may re-write."""
    fake = FakeHyperfix()
    target = _target(fake, resolve=_resolver("rec-1"))

    plan = await target.plan(Track("Wire", "Field Day"))
    assert plan.existing.holds is False
    assert plan.existing.prior is None
    assert plan.existing.occupied is False


@pytest.mark.anyio
async def test_a_source_mbid_is_written_without_a_lookup():
    fake = FakeHyperfix()
    target = _target(fake)  # a resolver that fails the test if it is asked

    plan = await target.plan(Track("Wire", "Field Day", recording_mbid="from-plex"))
    assert plan.target_key == "from-plex"


@pytest.mark.anyio
async def test_a_track_with_no_recording_is_unmatched():
    fake = FakeHyperfix()
    target = _target(fake, resolve=_resolver(None))  # MusicBrainz finds nothing

    plan = await target.plan(Track("Wire", "Field Day"))
    assert plan.status is OutcomeStatus.UNMATCHED
    assert plan.reason is Reason.NO_RECORDING_MBID
    assert plan.actionable is False


@pytest.mark.anyio
async def test_undo_puts_back_the_rating_the_recording_held():
    fake = FakeHyperfix({"rec-1": 80})
    target = _target(fake)
    record = SyncedTrack(
        key="wire||field day", recording_mbid="rec-1", rating=8.0, target_key="rec-1", prior="20"
    )

    await target.undo(record)
    assert fake.ratings == {"rec-1": 20}


@pytest.mark.anyio
async def test_undo_clears_a_rating_that_was_not_there_before():
    fake = FakeHyperfix({"rec-1": 80})
    target = _target(fake)
    record = SyncedTrack(key="wire||field day", rating=8.0, target_key="rec-1")

    await target.undo(record)
    assert not fake.ratings


@pytest.mark.anyio
async def test_undo_leaves_a_rating_the_user_changed_after_us():
    fake = FakeHyperfix({"rec-1": 60})
    target = _target(fake)
    record = SyncedTrack(key="wire||field day", rating=8.0, target_key="rec-1", prior="20")

    await target.undo(record)
    assert fake.ratings == {"rec-1": 60}
    assert not fake.writes()


@pytest.mark.anyio
async def test_undo_does_nothing_when_the_write_never_happened():
    """We planned 80 but the recording already held our prior: undo leaves it."""
    fake = FakeHyperfix({"rec-1": 20})
    target = _target(fake)
    record = SyncedTrack(key="wire||field day", rating=8.0, target_key="rec-1", prior="20")

    await target.undo(record)
    assert fake.ratings == {"rec-1": 20}
    assert not fake.writes()


def test_a_record_without_a_target_key_cannot_be_undone():
    target = _target(FakeHyperfix())
    with pytest.raises(ValueError, match="no recorded recording MBID"):
        target.undoable(SyncedTrack(key="wire||field day"))


def test_the_api_protocol_dispatches_a_hyperfix_source(monkeypatch):
    """A hyperfix *source* has to reach the factory, not fall off the dispatch table."""
    from ratingrelay.core import adapters  # pylint: disable=import-outside-toplevel
    from ratingrelay.schemas.relay import HyperfixFromConfig  # pylint: disable=import-outside-toplevel

    seen: list[tuple[str, str]] = []

    async def fake_source_for_service(db, service, *, feedback_type="love"):  # noqa: ARG001
        seen.append((service, feedback_type))
        return object()

    monkeypatch.setattr(adapters, "source_for_service", fake_source_for_service)
    config = HyperfixFromConfig(service="hyperfix", feedback_type="hate")
    assert adapters.relay_for(config) == "hate"
    monkeypatch.setattr(adapters, "relay_identity", lambda ref, **kw: kw["feedback_type"])

    import anyio  # pylint: disable=import-outside-toplevel

    anyio.run(lambda: adapters.source_for(None, config))
    assert seen == [("hyperfix", "hate")]


def test_reset_undoes_recorded_hyperfix_writes():
    """The recorded reset must reach hyperfix, like it reaches the other targets."""
    from ratingrelay.core.relay import _reset_target_config  # pylint: disable=import-outside-toplevel
    from ratingrelay.schemas.relay import HyperfixToConfig  # pylint: disable=import-outside-toplevel

    config = _reset_target_config("hyperfix:attic")
    assert isinstance(config, HyperfixToConfig)
    assert (config.service, config.account) == ("hyperfix", "attic")
