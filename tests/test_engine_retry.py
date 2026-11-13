"""Retry on transport failure, and only on transport failure.

A blip in one service should cost a retry, not a track; a decision the engine made
(a refusal, a missing recording) must not be retried at all.
"""

import pytest

from ratingrelay.engine import (
    MemorySyncStore,
    OutcomeStatus,
    Reason,
    RunReport,
    Track,
    WritePlan,
)
from ratingrelay.engine import pipeline, retry


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Backoff is real time; the tests only care about the number of attempts."""
    monkeypatch.setattr(retry, "BACKOFF_SECONDS", 0)


class Flaky:
    """A call that fails a fixed number of times before answering."""

    def __init__(self, failures: int, exception: Exception, result=None):
        self.failures = failures
        self.exception = exception
        self.result = result
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exception
        return self.result


async def test_a_transport_failure_is_retried_until_it_answers():
    call = Flaky(2, ConnectionError("connection reset"), result="ok")
    assert await retry.attempt(call, "somewhere") == "ok"
    assert call.calls == 3


async def test_a_failure_that_outlives_the_attempts_is_raised():
    call = Flaky(99, TimeoutError("read timed out"))
    with pytest.raises(TimeoutError):
        await retry.attempt(call, "somewhere")
    assert call.calls == retry.ATTEMPTS


async def test_a_decision_is_never_retried():
    """``ValueError`` is what ``undoable()`` raises to refuse, not a blip."""
    call = Flaky(1, ValueError("no recorded Last.fm track name"))
    with pytest.raises(ValueError):
        await retry.attempt(call, "somewhere")
    assert call.calls == 1


class FlakyTarget:
    """Writes on the third attempt, as a target with a flaky connection would."""

    service = "lastfm"

    def __init__(self, failures: int = 2):
        self.failures = failures
        self.writes = 0
        self.attempts = 0

    async def plan(self, track: Track) -> WritePlan:
        return WritePlan(
            track=track,
            target=self.service,
            intent=None,
            target_key=f"{track.artist}\x00{track.title}",
            commit=self._commit,
        )

    async def _commit(self) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ConnectionError("connection refused")
        self.writes += 1


class FakeSource:
    service = "plex"

    def __init__(self, tracks, failures: int = 0):
        self._tracks = tracks
        self.failures = failures
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("plex is restarting")
        return self._tracks


async def test_a_flaky_write_is_retried_and_still_recorded():
    track = Track(artist="radiohead", title="karma police")
    target = FlakyTarget(failures=2)
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store
    )

    assert target.writes == 1
    assert report.status == "completed"
    record = (await store.load("plex", "lastfm"))[track.key]
    assert record.intent is None


async def test_a_write_that_keeps_failing_fails_the_track_once():
    track = Track(artist="radiohead", title="karma police")
    target = FlakyTarget(failures=99)
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store
    )

    assert target.attempts == retry.ATTEMPTS
    [outcome] = report.outcomes
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.reason is Reason.WRITE_FAILED
    assert not await store.load("plex", "lastfm"), "a failed write records nothing"


async def test_a_flaky_fetch_is_retried():
    track = Track(artist="radiohead", title="karma police")
    source = FakeSource([track], failures=1)
    target = FlakyTarget(failures=0)

    report = await pipeline.run(
        source, [target], sync_store=MemorySyncStore()
    )

    assert source.calls == 2
    assert isinstance(report, RunReport)
    assert report.status == "completed"


class UnreachableTarget(FlakyTarget):
    """Every call — including the plan — hits a refused connection."""

    async def plan(self, track: Track) -> WritePlan:
        raise ConnectionError("connection refused")


async def test_a_plan_that_cannot_be_made_fails_the_track_not_the_run():
    tracks = [
        Track(artist="radiohead", title="karma police"),
        Track(artist="radiohead", title="let down"),
    ]
    target = UnreachableTarget()
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        tracks, [target], source_service="plex", sync_store=store
    )

    assert [outcome.status for outcome in report.outcomes] == [
        OutcomeStatus.FAILED,
        OutcomeStatus.FAILED,
    ]
    assert report.outcomes[0].reason is Reason.WRITE_FAILED
