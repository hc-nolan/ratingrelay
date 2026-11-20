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
from ratingrelay.engine.services.musicbrainz import Resolution
from ratingrelay.engine.services.plex import PlexTarget
from ratingrelay.engine.store import NullProgress, SyncedTrack
from ratingrelay.engine.types import RunReport, TargetRefused, TargetReport


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
            # An exception instance is raised as-is, so a test can make a target
            # refuse (`TargetRefused`) rather than merely break.
            if isinstance(self._fail_with, BaseException):
                raise self._fail_with
            raise RuntimeError(self._fail_with)
        self.writes.append((external_id, rating))

    async def current_rating(self, external_id: str) -> Optional[float]:
        rating = next((c.rating for c in self._candidates if c.external_id == external_id), None)
        for key, written in self.writes:
            if key == external_id:
                rating = written
        return rating


class FakeLastFmClient:
    def __init__(self):
        self.loves: list[tuple[str, str]] = []

    async def love(self, artist: str, title: str) -> None:
        self.loves.append((artist, title))

    async def loved(self) -> list:
        return []


class FakeListenBrainzClient:
    def __init__(self):
        self.submissions: list[tuple[int, str]] = []

    async def submit(self, score: int, mbid: str) -> None:
        self.submissions.append((score, mbid))

    async def feedback(self, score: int) -> list:
        return []


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
        return Resolution(mbid) if mbid else Resolution()

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


async def test_listenbrainz_target_carries_the_lookups_proposals():
    """An unmatched track reports what it nearly matched, so it can be resolved.

    The resolver only writes on an exact match, but the ladder's looser rungs know
    which recordings were close. Dropping them left the queue with a dead end:
    the row said "no MBID" and offered nothing to act on.
    """
    client = FakeListenBrainzClient()
    proposal = Candidate(artist="Artist A", title="Song A (Live)", recording_mbid="rec-live")

    async def resolve(track_mbid, artist, title):  # noqa: ARG001 - the resolver port
        return Resolution(proposals=(proposal,))

    target = ListenBrainzTarget(client, MemoryMappingStore(), resolve)

    report = await pipeline.run_tracks(
        [Track("Artist", "Song")], [target], source_service="plex", sync_store=MemorySyncStore()
    )

    outcome = report.outcomes[0]
    assert outcome.reason is Reason.NO_RECORDING_MBID
    assert outcome.matches == (proposal,)
    assert client.submissions == []


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


def test_one_failure_among_many_converged_tracks_is_partial():
    """Skipped means already converged: that run did its job for 503 of 504."""
    report = RunReport(
        source="plex",
        tracks_fetched=504,
        targets=(TargetReport(target="lastfm", written=0, skipped=500, unmatched=0, failed=1, reverted=3),),
    )
    assert report.status == "partial"

    nothing = RunReport(
        source="plex",
        tracks_fetched=1,
        targets=(TargetReport(target="lastfm", written=0, skipped=0, unmatched=0, failed=1, reverted=0),),
    )
    assert nothing.status == "failed"


async def test_a_target_that_refuses_is_not_a_write_failure():
    """A refusal is final: typed apart from transport trouble, and not recorded."""
    target, _ = _plex_target(
        [Candidate("Artist", "Song", external_id="1")], fail_with=TargetRefused("not here")
    )
    store = MemorySyncStore()

    report = await pipeline.run_tracks(
        [Track("Artist", "Song")], [target], source_service="plex", sync_store=store
    )

    outcome = report.outcomes[0]
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.reason is Reason.TARGET_REFUSED
    assert outcome.detail == "not here"
    assert store.records == {}


# ---------------------------------------------------------------------------
# Two fetched rows, one recording: write once (RR-17)
# ---------------------------------------------------------------------------


async def test_a_library_copy_is_written_once_not_once_per_row():
    """A library that holds a track twice is one recording, so one write.

    The record the run keeps in memory was never updated after a write, so the
    second row sharing the key looked unwritten and went out again — 357 writes
    for 217 recordings on a live run, and a count that overstated the work.
    """
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), await _resolver("rec-1"))
    store = MemorySyncStore()
    tracks = [
        Track("Artist", "Song", external_id="101"),
        Track("Artist", "Song", external_id="202"),
    ]

    report = await pipeline.run_tracks(
        tracks, [target], source_service="plex", sync_store=store
    )

    assert client.submissions == [(1, "rec-1")]
    written, repeated = report.outcomes
    assert written.status is OutcomeStatus.WRITTEN
    assert repeated.status is OutcomeStatus.SKIPPED
    assert repeated.reason is Reason.ALREADY_SYNCED
    assert report.targets[0].written == 1


async def test_a_dry_run_counts_a_library_copy_the_way_a_real_run_does():
    """Contract §4: the dry run is the authority on what a real run would do.

    Only a real write used to make the second copy of a track look settled, so the
    dry run promised one write more than the real run made.
    """
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), await _resolver("rec-1"))
    tracks = [Track("Artist", "Song"), Track("Artist", "Song")]

    report = await pipeline.run_tracks(
        tracks, [target], source_service="plex", sync_store=MemorySyncStore(), dry_run=True
    )

    assert client.submissions == []
    assert [o.status for o in report.outcomes] == [
        OutcomeStatus.WRITTEN,
        OutcomeStatus.SKIPPED,
    ]


async def test_two_recordings_under_one_key_are_both_written():
    """The identity is the recording, not the key: two recordings both get a write.

    A studio and a live version can share ``artist||title`` and still be different
    recordings, which a source that names its own MBIDs (ListenBrainz feedback,
    hyperfix) can hand us in one run. The sync store holds one record per key, so
    it can only name one of them — writing both is what keeps the other from
    silently never being rated, and every write here is idempotent.
    """
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), await _resolver("rec-1"))
    store = MemorySyncStore()
    tracks = [
        Track("Artist", "Song", recording_mbid="rec-studio"),
        Track("Artist", "Song", recording_mbid="rec-live"),
    ]

    report = await pipeline.run_tracks(
        tracks, [target], source_service="plex", sync_store=store
    )

    assert client.submissions == [(1, "rec-studio"), (1, "rec-live")]
    assert [o.status for o in report.outcomes] == [OutcomeStatus.WRITTEN] * 2
    # The record can only name one of them: the last write, as before.
    assert store.records[("plex", "listenbrainz")]["artist||song"].recording_mbid == (
        "rec-live"
    )


async def test_a_record_from_an_earlier_run_settles_a_shared_key():
    """The collision guard is about *this* run's writes, not about the record.

    A record from an earlier run whose key now names two recordings must still
    settle: treating it as a collision would rewrite both rows on every run.
    """
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), await _resolver("rec-1"))
    store = MemorySyncStore()
    await store.remember(
        "plex",
        "listenbrainz",
        SyncedTrack(
            key="artist||song", recording_mbid="rec-studio", intent="love", rating=None
        ),
    )
    tracks = [
        Track("Artist", "Song", recording_mbid="rec-studio"),
        Track("Artist", "Song", recording_mbid="rec-live"),
    ]

    report = await pipeline.run_tracks(
        tracks, [target], source_service="plex", sync_store=store
    )

    assert client.submissions == []
    assert [o.reason for o in report.outcomes] == [Reason.ALREADY_SYNCED] * 2


async def test_a_duplicate_row_is_not_target_drift():
    """A relay that reapplies drift must not read its own fresh write as dropped.

    Every target plans against the listing it cached before the run, so the second
    row of a track looks absent to it — and an ordinary drift relay re-wrote the
    recording it had just written (RR-17 survived in that configuration).
    """
    client = FakeListenBrainzClient()
    target = ListenBrainzTarget(client, MemoryMappingStore(), await _resolver("rec-1"))
    tracks = [Track("Artist", "Song"), Track("Artist", "Song")]

    report = await pipeline.run_tracks(
        tracks,
        [target],
        source_service="plex",
        sync_store=MemorySyncStore(),
        reapply_drift=True,
    )

    assert client.submissions == [(1, "rec-1")]
    assert [o.reason for o in report.outcomes] == [None, Reason.ALREADY_SYNCED]
