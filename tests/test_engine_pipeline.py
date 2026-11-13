"""Pipeline tests: the behaviour contract, exercised without any real service.

Each test drives the real engine (matching ladder, plans, pipeline) against fake
service clients and an in-memory store. Nothing here touches Plex, Last.fm,
ListenBrainz, MusicBrainz or a database.
"""

from typing import Optional

import pytest

from ratingrelay.engine import (
    Candidate,
    MemoryMappingStore,
    MemorySyncStore,
    OutcomeStatus,
    Reason,
    Track,
    TrackMapping,
    match_track,
)
from ratingrelay.engine import pipeline
from ratingrelay.engine.services.lastfm import LastFmTarget
from ratingrelay.engine.services.listenbrainz import ListenBrainzTarget
from ratingrelay.engine.services.plex import PlexTarget
from ratingrelay.engine.store import NullProgress


class FakePlexLibrary:
    """Just enough of PlexLibrary: a candidate list and a rate() that records calls."""

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
    def __init__(self):
        self.loves: list[tuple[str, str]] = []

    async def love(self, artist: str, title: str) -> None:
        self.loves.append((artist, title))


class FakeListenBrainzClient:
    def __init__(self):
        self.submissions: list[tuple[int, str]] = []

    async def submit(self, score: int, mbid: str) -> None:
        self.submissions.append((score, mbid))


class CollectingProgress:
    def __init__(self):
        self.counts: list[int] = []
        self.outcomes: list = []

    async def fetch_complete(self, count: int) -> None:
        self.counts.append(count)

    async def outcome(self, outcome) -> None:
        self.outcomes.append(outcome)


class FakeSource:
    service = "plex"

    def __init__(self, tracks: list[Track]):
        self._tracks = tracks

    async def fetch(self) -> list[Track]:
        return self._tracks


def _plex_target(candidates: list[Candidate], **kwargs) -> tuple[PlexTarget, FakePlexLibrary]:
    library = FakePlexLibrary(candidates, fail_with=kwargs.pop("fail_with", None))
    return PlexTarget(library, MemoryMappingStore(), kwargs.pop("rating", 10.0)), library


# ---------------------------------------------------------------------------
# Dry run is the real pipeline with the writes suppressed
# ---------------------------------------------------------------------------


async def test_dry_run_takes_the_same_decisions_as_a_real_run():
    target, library = _plex_target([Candidate("Artist", "Song", external_id="1")])
    tracks = [Track("Artist", "Song")]

    dry = await pipeline.run_tracks(
        tracks, [target], source_service="plex", sync_store=MemorySyncStore(), dry_run=True
    )
    store = MemorySyncStore()
    real = await pipeline.run_tracks(tracks, [target], source_service="plex", sync_store=store)

    assert dry.outcomes == real.outcomes
    assert library.writes == [("1", 10.0)]
    assert set(store.records[("plex", "plex")]) == {"artist||song"}


async def test_dry_run_writes_nothing_to_the_sync_store():
    target, library = _plex_target([Candidate("Artist", "Song", external_id="1")])
    store = MemorySyncStore()

    await pipeline.run_tracks([Track("Artist", "Song")], [target], source_service="plex", sync_store=store, dry_run=True)

    assert library.writes == []
    assert store.records == {}


# ---------------------------------------------------------------------------
# Idempotence: a completed run is a no-op when repeated
# ---------------------------------------------------------------------------


async def test_second_run_skips_what_the_first_wrote():
    target, library = _plex_target([Candidate("Artist", "Song", external_id="1")])
    store = MemorySyncStore()
    tracks = [Track("Artist", "Song")]

    await pipeline.run_tracks(tracks, [target], source_service="plex", sync_store=store)
    again = await pipeline.run_tracks(tracks, [target], source_service="plex", sync_store=store)

    assert [o.status for o in again.outcomes] == [OutcomeStatus.SKIPPED]
    assert again.outcomes[0].reason is Reason.ALREADY_SYNCED
    assert library.writes == [("1", 10.0)]


# ---------------------------------------------------------------------------
# Ambiguity goes to a human, never to a guess
# ---------------------------------------------------------------------------


async def test_ambiguous_match_writes_nothing_and_attaches_candidates():
    target, library = _plex_target(
        [
            Candidate("Artist", "Song (Live at Reading)", external_id="1"),
            Candidate("Artist", "Song (Live at Paramount)", external_id="2"),
        ]
    )
    store = MemorySyncStore()

    report = await pipeline.run_tracks([Track("Artist", "Song")], [target], source_service="plex", sync_store=store)

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.UNMATCHED
    assert outcome.reason is Reason.AMBIGUOUS_MATCH
    assert len(outcome.matches) == 2
    assert library.writes == []
    assert store.records == {}


async def test_unmatched_track_carries_candidates_it_was_not_written_to():
    target, library = _plex_target([Candidate("Artist Tribute Band", "Song", external_id="1")])
    store = MemorySyncStore()

    report = await pipeline.run_tracks([Track("Artist", "Song")], [target], source_service="plex", sync_store=store)

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.UNMATCHED
    assert outcome.reason is Reason.NO_MATCH
    assert outcome.detail == "No match found in Plex library"
    assert [c.external_id for c in outcome.matches] == ["1"]
    assert library.writes == []


# ---------------------------------------------------------------------------
# Failures are typed, counted and never recorded as done
# ---------------------------------------------------------------------------


async def test_write_failure_is_typed_and_not_recorded():
    target, _ = _plex_target(
        [Candidate("Artist", "Song", external_id="1")], fail_with="server exploded"
    )
    store = MemorySyncStore()

    report = await pipeline.run_tracks([Track("Artist", "Song")], [target], source_service="plex", sync_store=store)

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.reason is Reason.WRITE_FAILED
    assert outcome.detail == "server exploded"
    assert store.records == {}
    assert report.had_errors
    assert report.status == "failed"


async def test_one_failed_write_does_not_stop_the_run():
    target, library = _plex_target(
        [
            Candidate("Artist", "Song", external_id="1"),
            Candidate("Artist", "Other", external_id="2"),
        ],
        fail_with="first write failed",
    )

    report = await pipeline.run_tracks(
        [Track("Artist", "Song"), Track("Artist", "Other")],
        [target],
        source_service="plex",
        sync_store=MemorySyncStore(),
    )

    assert [o.status for o in report.outcomes] == [OutcomeStatus.FAILED, OutcomeStatus.FAILED]
    assert library.writes == []


async def test_progress_sees_every_outcome_and_the_fetch_count():
    progress = CollectingProgress()

    async def fake_pipeline_run():
        return await pipeline.run(
            FakeSource([Track("Artist", "Song"), Track("Artist", "Gone")]),
            [],
            sync_store=MemorySyncStore(),
            progress=progress,
        )

    report = await fake_pipeline_run()

    assert progress.counts == [2]
    assert progress.outcomes == []
    assert report.tracks_fetched == 2


async def test_outcomes_are_reported_as_they_are_decided():
    target, _ = _plex_target([Candidate("Artist", "Song", external_id="1")])
    progress = CollectingProgress()

    await pipeline.run(
        FakeSource([Track("Artist", "Song"), Track("Artist", "Gone")]),
        [target],
        sync_store=MemorySyncStore(),
        progress=progress,
    )

    assert [o.status for o in progress.outcomes] == [
        OutcomeStatus.WRITTEN,
        OutcomeStatus.UNMATCHED,
    ]


async def test_default_progress_sink_is_a_no_op():
    target, _ = _plex_target([])
    report = await pipeline.run_tracks(
        [Track("Artist", "Song")], [target], source_service="plex", sync_store=MemorySyncStore(), progress=NullProgress()
    )
    assert report.outcomes[0].status is OutcomeStatus.UNMATCHED


# ---------------------------------------------------------------------------
# Last.fm: a mapping is the only matching that happens
# ---------------------------------------------------------------------------


async def test_lastfm_target_loves_the_mapped_track():
    client = FakeLastFmClient()
    store = MemoryMappingStore(
        {"lastfm": {Track("Artist", "Song").key: TrackMapping("Mapped Artist", "Mapped Song")}}
    )
    target = LastFmTarget(client, store)

    report = await pipeline.run_tracks(
        [Track("Artist", "Song")], [target], source_service="plex", sync_store=MemorySyncStore()
    )

    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert client.loves == [("Mapped Artist", "Mapped Song")]


async def test_lastfm_target_reports_missing_metadata_without_writing():
    client = FakeLastFmClient()
    target = LastFmTarget(client, MemoryMappingStore())

    report = await pipeline.run_tracks(
        [Track("", "")], [target], source_service="plex", sync_store=MemorySyncStore()
    )

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.reason is Reason.MISSING_METADATA
    assert client.loves == []


# ---------------------------------------------------------------------------
# ListenBrainz: the resolved recording MBID is what gets recorded
# ---------------------------------------------------------------------------


async def _resolver(mbid: Optional[str]):
    async def resolve(track_mbid, artist, title):  # noqa: ARG001 - signature is the port
        return mbid

    return resolve


async def test_listenbrainz_target_records_the_resolved_mbid():
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(
        client, MemoryMappingStore(), await _resolver("rec-1"), score=1
    )
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        [Track("Artist", "Song")], [target], source_service="plex", sync_store=store
    )

    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert client.submissions == [(1, "rec-1")]
    assert store.records[("plex", "listenbrainz")]["artist||song"].recording_mbid == "rec-1"


async def test_listenbrainz_target_without_an_mbid_is_unmatched():
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), await _resolver(None))
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        [Track("Artist", "Song")], [target], source_service="plex", sync_store=store
    )

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.UNMATCHED
    assert outcome.reason is Reason.NO_RECORDING_MBID
    assert client.submissions == []
    assert store.records == {}


async def test_listenbrainz_target_prefers_a_mapping_mbid():
    client = FakeListenBrainzClient()
    mappings = MemoryMappingStore(
        {"listenbrainz": {Track("Artist", "Song").key: TrackMapping("A", "S", "mapped-mbid")}}
    )
    target = ListenBrainzTarget(client, mappings, await _resolver("looked-up"))

    await pipeline.run_tracks([Track("Artist", "Song")], [target], source_service="plex", sync_store=MemorySyncStore())

    assert client.submissions == [(1, "mapped-mbid")]


@pytest.mark.parametrize("score", [1, -1])
async def test_listenbrainz_intent_matches_the_feedback_score(score):
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), await _resolver("rec-1"), score)
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        [Track("Artist", "Song")], [target], source_service="plex", sync_store=store
    )

    record = store.records[("plex", "listenbrainz")]["artist||song"]
    assert record.intent == report.outcomes[0].intent.key
    assert report.outcomes[0].intent.key == ("love" if score > 0 else "hate")


# ---------------------------------------------------------------------------
# The ladder is what the Plex target uses, not a second implementation
# ---------------------------------------------------------------------------


async def test_plex_target_writes_every_copy_of_a_matched_recording():
    target, library = _plex_target(
        [
            Candidate("Artist", "Song", external_id="1"),
            Candidate("Artist", "Song", external_id="2"),
        ]
    )

    await pipeline.run_tracks([Track("Artist", "Song")], [target], source_service="plex", sync_store=MemorySyncStore())

    assert sorted(library.writes) == [("1", 10.0), ("2", 10.0)]


async def test_plex_target_and_engine_agree_on_the_tier():
    source = Track("Artist", "Song (Remastered 2011)")
    candidates = [Candidate("Artist", "Song", external_id="1")]
    direct = match_track(source, candidates)
    target, library = _plex_target(candidates)

    report = await pipeline.run_tracks([source], [target], source_service="plex", sync_store=MemorySyncStore())

    assert direct.matched
    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert library.writes == [("1", 10.0)]
