"""Reconciliation: every run compares the source to what we wrote, and converges.

These are the tests for the behaviour contract's second and third items — a
changed rating re-propagates, a withdrawn love is un-loved, and nothing we did
not write is ever cleared. All fakes; no service, no database.
"""

from typing import Optional

from ratingrelay.engine import (
    Candidate,
    MemoryMappingStore,
    MemorySyncStore,
    OutcomeStatus,
    Reason,
    SyncedTrack,
    Track,
    pipeline,
)
from ratingrelay.engine.normalize import track_key
from ratingrelay.engine.services.lastfm import LastFmTarget
from ratingrelay.engine.services.listenbrainz import ListenBrainzTarget
from ratingrelay.engine.services.plex import PlexTarget


class FakePlexLibrary:
    def __init__(self, candidates: list[Candidate], fail_with: Optional[str] = None):
        self._candidates = candidates
        self._fail_with = fail_with
        self.writes: list[tuple[str, Optional[float]]] = []

    async def candidates(self) -> tuple[Candidate, ...]:
        return tuple(self._candidates)

    async def rate(self, external_id: str, rating: Optional[float]) -> None:
        if self._fail_with is not None:
            raise RuntimeError(self._fail_with)
        self.writes.append((external_id, rating))


class FakeLastFmClient:
    def __init__(self, fail_with: Optional[str] = None):
        self._fail_with = fail_with
        self.loves: list[tuple[str, str]] = []
        self.unloves: list[tuple[str, str]] = []

    async def love(self, artist: str, title: str) -> None:
        self.loves.append((artist, title))

    async def unlove(self, artist: str, title: str) -> None:
        if self._fail_with is not None:
            raise RuntimeError(self._fail_with)
        self.unloves.append((artist, title))


class FakeListenBrainzClient:
    def __init__(self, fail_with: Optional[str] = None):
        self._fail_with = fail_with
        self.submissions: list[tuple[int, str]] = []

    async def submit(self, score: int, mbid: str) -> None:
        if self._fail_with is not None:
            raise RuntimeError(self._fail_with)
        self.submissions.append((score, mbid))


class CountingResolver:
    def __init__(self, mbid: Optional[str]):
        self.mbid = mbid
        self.calls = 0

    async def __call__(self, track_mbid, artist, title):  # noqa: ARG002 - the resolver port
        self.calls += 1
        return self.mbid


def _plex_target(candidates, **kwargs) -> tuple[PlexTarget, FakePlexLibrary]:
    library = FakePlexLibrary(candidates, fail_with=kwargs.pop("fail_with", None))
    return PlexTarget(library, MemoryMappingStore(), kwargs.pop("rating", 10.0)), library


def _lastfm_target(**kwargs) -> tuple[LastFmTarget, FakeLastFmClient]:
    client = FakeLastFmClient(fail_with=kwargs.pop("fail_with", None))
    return LastFmTarget(client, MemoryMappingStore()), client


# ---------------------------------------------------------------------------
# Same intent: skip. Different intent: write again.
# ---------------------------------------------------------------------------


async def test_unchanged_intent_is_not_written_twice():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await store.remember(
        "plex", "lastfm", SyncedTrack(track.key, intent="love", target_key='["Artist", "Song"]')
    )

    report = await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store)

    assert report.outcomes[0].status is OutcomeStatus.SKIPPED
    assert report.outcomes[0].reason is Reason.ALREADY_SYNCED
    assert client.loves == []


async def test_legacy_record_without_an_intent_is_written_once_more():
    """Rows from before v3 say only "synced"; writing again is how they heal."""
    target, client = _lastfm_target()
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await store.remember("plex", "lastfm", SyncedTrack(track.key))  # intent is NULL

    report = await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store)

    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert client.loves == [("Artist", "Song")]
    assert store.records[("plex", "lastfm")][track.key].intent == "love"


async def test_a_changed_rating_is_written_again():
    target, library = _plex_target([Candidate("Artist", "Song", external_id="1")])
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    key = track_key("Artist", "Song")
    await store.remember(
        "plex", "plex", SyncedTrack(key, intent="rating:8.0", rating=8.0, target_key='["1"]')
    )

    report = await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store)

    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert library.writes == [("1", 10.0)]
    record = store.records[("plex", "plex")][key]
    assert (record.intent, record.rating) == ("rating:10.0", 10.0)


async def test_a_love_becoming_a_hate_rewrites_the_feedback():
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), CountingResolver("rec-1"))
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await store.remember(
        "plex", "listenbrainz", SyncedTrack(track.key, intent="love", target_key="rec-1")
    )

    report = await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store, dry_run=False)

    # Same score as recorded (love) means skip, not a second submit.
    assert report.outcomes[0].status is OutcomeStatus.SKIPPED
    assert client.submissions == []


# ---------------------------------------------------------------------------
# Withdrawn at the source: undo, and only what we wrote
# ---------------------------------------------------------------------------


async def test_a_withdrawn_love_is_unloved():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await store.remember(
        "plex", "lastfm", SyncedTrack(gone.key, intent="love", target_key='["Artist", "Gone"]')
    )

    report = await pipeline.run_tracks([], [target], source_service="plex", sync_store=store)

    assert [o.status for o in report.outcomes] == [OutcomeStatus.REVERTED]
    assert report.outcomes[0].reason is Reason.WITHDRAWN
    assert client.unloves == [("Artist", "Gone")]
    assert report.targets[0].reverted == 1
    assert store.records[("plex", "lastfm")] == {}


async def test_only_the_withdrawn_track_is_undone():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    kept, gone = Track("Artist", "Kept"), Track("Artist", "Gone")
    await store.remember(
        "plex", "lastfm", SyncedTrack(kept.key, intent="love", target_key='["Artist", "Kept"]')
    )
    await store.remember(
        "plex", "lastfm", SyncedTrack(gone.key, intent="love", target_key='["Artist", "Gone"]')
    )

    report = await pipeline.run_tracks([kept], [target], source_service="plex", sync_store=store)

    assert [o.status for o in report.outcomes] == [OutcomeStatus.SKIPPED, OutcomeStatus.REVERTED]
    assert client.unloves == [("Artist", "Gone")]
    assert set(store.records[("plex", "lastfm")]) == {kept.key}


async def test_a_rating_on_the_target_that_we_did_not_write_is_left_alone():
    target, library = _plex_target([Candidate("Artist", "Song", external_id="9", rating=6.0)])
    store = MemorySyncStore()

    # A track we never wrote, on both sides: nothing to undo, nothing to clear.
    report = await pipeline.run_tracks([], [target], source_service="plex", sync_store=store)

    assert report.outcomes == ()
    assert library.writes == []


async def test_a_legacy_record_without_a_target_key_is_not_guessed_at():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await store.remember("plex", "lastfm", SyncedTrack(gone.key, intent="love"))  # no target_key

    report = await pipeline.run_tracks([], [target], source_service="plex", sync_store=store)

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.SKIPPED
    assert outcome.reason is Reason.UNRECORDED_TARGET
    assert client.unloves == []
    assert set(store.records[("plex", "lastfm")]) == {gone.key}


async def test_a_legacy_listenbrainz_record_is_undone_through_its_mbid():
    """Feedback is keyed by MBID, so the recorded MBID is a sufficient target key."""
    resolver = CountingResolver("looked-up")
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), resolver)
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await store.remember(
        "plex",
        "listenbrainz",
        SyncedTrack(gone.key, recording_mbid="rec-1", intent="love"),
    )

    report = await pipeline.run_tracks([], [target], source_service="plex", sync_store=store)

    assert report.outcomes[0].status is OutcomeStatus.REVERTED
    assert client.submissions == [(0, "rec-1")]
    assert resolver.calls == 0


async def test_an_undo_on_plex_clears_only_the_copies_we_rated():
    target, library = _plex_target([Candidate("Artist", "Gone", external_id="1")])
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await store.remember(
        "plex", "plex", SyncedTrack(gone.key, intent="rating:10.0", target_key='["1", "2"]')
    )

    await pipeline.run_tracks([], [target], source_service="plex", sync_store=store)

    assert library.writes == [("1", None), ("2", None)]


async def test_a_failed_undo_is_reported_and_the_record_kept():
    target, _ = _lastfm_target(fail_with="last.fm is down")
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await store.remember(
        "plex", "lastfm", SyncedTrack(gone.key, intent="love", target_key='["Artist", "Gone"]')
    )

    report = await pipeline.run_tracks([], [target], source_service="plex", sync_store=store)

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.reason is Reason.WRITE_FAILED
    assert set(store.records[("plex", "lastfm")]) == {gone.key}


# ---------------------------------------------------------------------------
# Dry run: the same decisions, nothing written
# ---------------------------------------------------------------------------


async def test_dry_run_reports_the_withdrawal_without_undoing_it():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await store.remember(
        "plex", "lastfm", SyncedTrack(gone.key, intent="love", target_key='["Artist", "Gone"]')
    )

    report = await pipeline.run_tracks([], [target], source_service="plex", sync_store=store, dry_run=True)

    assert report.outcomes[0].status is OutcomeStatus.REVERTED
    assert client.unloves == []
    assert set(store.records[("plex", "lastfm")]) == {gone.key}


# ---------------------------------------------------------------------------
# The record is also the MBID cache
# ---------------------------------------------------------------------------


async def test_a_rerun_reuses_the_recorded_recording_mbid():
    resolver = CountingResolver("rec-1")
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), resolver)
    store = MemorySyncStore()
    track = Track("Artist", "Song")  # no recording_mbid, so the first run must look it up

    await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store)
    await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store)

    assert resolver.calls == 1
    assert client.submissions == [(1, "rec-1")]


async def test_the_recorded_mbid_is_what_an_undo_uses():
    resolver = CountingResolver("looked-up")
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), resolver)
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await store.remember(
        "plex", "listenbrainz", SyncedTrack(gone.key, intent="love", target_key="rec-1")
    )

    await pipeline.run_tracks([], [target], source_service="plex", sync_store=store)

    assert client.submissions == [(0, "rec-1")]
    assert resolver.calls == 0
