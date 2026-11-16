"""Target drift: a write the target dropped, and the relay that puts it back.

Contract §2 (reconcile, don't append) has a blind spot that plan 010 chose on
purpose and plan 013 pinned: an unchanged intent is ``already_synced``, so a
track somebody un-loved on the target stays un-loved while the source keeps the
love. ``reapply_target_drift`` closes it for one relay, and only for a write the
target has *dropped* — a value the user replaced is theirs, exactly as undo
leaves a copy somebody re-rated alone.

All fakes; no service, no database.
"""

import json
from typing import Iterable, Optional, Sequence

from ratingrelay.engine import (
    Candidate,
    MemoryMappingStore,
    MemorySyncStore,
    OutcomeStatus,
    Reason,
    RunReport,
    SyncedTrack,
    Track,
    pipeline,
)
from ratingrelay.engine.normalize import track_key
from ratingrelay.engine.services.lastfm import LastFmTarget
from ratingrelay.engine.services.listenbrainz import ListenBrainzTarget
from ratingrelay.engine.services.musicbrainz import Resolution
from ratingrelay.engine.services.plex import PlexTarget


class FakeLastFmClient:
    """Loves as an account holds them: a set that a write or an edit changes."""

    def __init__(self, loved: Iterable[tuple[str, str]] = ()):
        self._loved = {json.dumps([artist, title]) for artist, title in loved}
        self.loves: list[tuple[str, str]] = []
        self.unloves: list[tuple[str, str]] = []

    async def loved(self) -> list[Track]:
        return [Track(*json.loads(key)) for key in sorted(self._loved)]

    async def love(self, artist: str, title: str) -> None:
        self.loves.append((artist, title))
        self._loved.add(json.dumps([artist, title]))

    async def unlove(self, artist: str, title: str) -> None:
        self.unloves.append((artist, title))
        self._loved.discard(json.dumps([artist, title]))


class FakeListenBrainzClient:
    def __init__(self, scores: Optional[dict[str, int]] = None):
        self.scores = dict(scores or {})
        self.submissions: list[tuple[int, str]] = []

    async def feedback(self, score: int) -> list[Track]:
        return [
            Track("Artist", "Song", recording_mbid=mbid)
            for mbid, held in self.scores.items()
            if held == score
        ]

    async def submit(self, score: int, mbid: str) -> None:
        self.submissions.append((score, mbid))
        self.scores[mbid] = score


class FakePlexLibrary:
    """A library whose ratings a person can change between runs."""

    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates
        self.writes: list[tuple[str, Optional[float]]] = []

    def _set(self, external_id: str, rating: Optional[float]) -> None:
        self._candidates = [
            Candidate(c.artist, c.title, external_id=c.external_id, rating=rating)
            if c.external_id == external_id
            else c
            for c in self._candidates
        ]

    def edit(self, external_id: str, rating: Optional[float]) -> None:
        """What a hand edit on Plex looks like to the next run."""
        self._set(external_id, rating)
        self.writes = [write for write in self.writes if write[0] != external_id]

    async def candidates(self) -> tuple[Candidate, ...]:
        return tuple(self._candidates)

    async def rate(self, external_id: str, rating: Optional[float]) -> None:
        self.writes.append((external_id, rating))
        self._set(external_id, rating)

    async def current_rating(self, external_id: str) -> Optional[float]:
        return next(
            (c.rating for c in self._candidates if c.external_id == external_id), None
        )


class FixedResolver:
    def __init__(self, mbid: str):
        self._mbid = mbid

    async def __call__(self, track_mbid, artist, title):  # noqa: ARG002 - the resolver port
        return Resolution(self._mbid)


def _lastfm_target(
    loved: Iterable[tuple[str, str]] = (),
) -> tuple[LastFmTarget, FakeLastFmClient]:
    client = FakeLastFmClient(loved)
    return LastFmTarget(client, MemoryMappingStore()), client


def _listenbrainz_target(
    scores: Optional[dict[str, int]] = None,
) -> tuple[ListenBrainzTarget, FakeListenBrainzClient]:
    client = FakeListenBrainzClient(scores)
    return ListenBrainzTarget(client, MemoryMappingStore(), FixedResolver("rec-1")), client


def _plex_target(candidates: list[Candidate]) -> tuple[PlexTarget, FakePlexLibrary]:
    library = FakePlexLibrary(candidates)
    return PlexTarget(library, MemoryMappingStore(), 10.0), library


async def _a_recorded_love(store: MemorySyncStore, track: Track, *, target: str = "lastfm"):
    await store.remember(
        "plex",
        target,
        SyncedTrack(
            track.key, intent="love", target_key=json.dumps([track.artist, track.title])
        ),
    )


def _decisions(report: RunReport) -> list[tuple[OutcomeStatus, Reason]]:
    return [(outcome.status, outcome.reason) for outcome in report.outcomes]


# ---------------------------------------------------------------------------
# A dropped love
# ---------------------------------------------------------------------------


async def test_a_dropped_love_is_left_alone_without_the_option():
    """Today's behaviour, named here so the pair of tests reads as one decision."""
    target, client = _lastfm_target()  # the target holds no love: it was dropped
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await _a_recorded_love(store, track)

    report = await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store)

    assert _decisions(report)[0] == (OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)
    assert client.loves == []


async def test_a_neutralised_listenbrainz_score_is_written_again():
    """Feedback withdrawn on the target is a removal, not a rival score."""
    target, client = _listenbrainz_target()  # the account holds nothing now
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await _a_recorded_love(store, track, target="listenbrainz")

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)
    assert client.submissions == [(1, "rec-1")]


async def test_a_dropped_love_is_written_again_when_the_relay_asks():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await _a_recorded_love(store, track)

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)
    assert client.loves == [("Artist", "Song")]
    assert report.targets[0].written == 1


async def test_the_second_run_is_a_no_op_again():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await _a_recorded_love(store, track)

    await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )
    # A run builds its targets fresh (``core/factory``), so the second run sees
    # the account as it is now, not this target's cached loved set.
    again = LastFmTarget(client, MemoryMappingStore())
    report = await pipeline.run_tracks(
        [track], [again], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)
    assert client.loves == [("Artist", "Song")]


async def test_a_write_the_target_still_holds_is_not_written_again():
    target, client = _lastfm_target([("Artist", "Song")])
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await _a_recorded_love(store, track)

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)
    assert client.loves == []


# ---------------------------------------------------------------------------
# A replacement is a person's edit, and stays
# ---------------------------------------------------------------------------


async def test_a_love_replaced_by_a_hate_is_not_reapplied():
    target, client = _listenbrainz_target({"rec-1": -1})
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await _a_recorded_love(store, track, target="listenbrainz")

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)
    assert client.submissions == []
    assert client.scores["rec-1"] == -1


async def test_a_cleared_plex_rating_is_written_again():
    target, library = _plex_target([Candidate("Artist", "Song", external_id="1")])
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await store.remember(
        "plex",
        "plex",
        SyncedTrack(
            track.key,
            intent="rating:10.0",
            rating=10.0,
            target_key='["1"]',
            prior='{"1": null}',
        ),
    )

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)
    assert library.writes == [("1", 10.0)]


async def test_a_rerated_plex_copy_is_not_reapplied():
    """The copy holds a rating the user chose; plan 010's rule still stands."""
    target, library = _plex_target([Candidate("Artist", "Song", external_id="1", rating=6.0)])
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await store.remember(
        "plex",
        "plex",
        SyncedTrack(track.key, intent="rating:10.0", rating=10.0, target_key='["1"]'),
    )

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)
    assert library.writes == []


async def test_a_plex_copy_rated_zero_counts_as_unrated():
    """Plex reports a cleared rating as 0 as readily as ``None``; both are empty."""
    target, library = _plex_target(
        [Candidate("Artist", "Song", external_id="1", rating=0.0)]
    )
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await store.remember(
        "plex",
        "plex",
        SyncedTrack(track.key, intent="rating:10.0", rating=10.0, target_key='["1"]'),
    )

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)
    assert library.writes == [("1", 10.0)]


async def test_a_legacy_record_without_an_intent_is_written_once_more_not_as_drift():
    """A v2 row says only "synced": it heals by an ordinary rewrite, never as drift."""
    target, client = _lastfm_target()
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await store.remember(
        "plex",
        "lastfm",
        SyncedTrack(track.key, target_key=json.dumps(["Artist", "Song"])),
    )

    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.WRITTEN, None)
    assert client.loves == [("Artist", "Song")]


# ---------------------------------------------------------------------------
# The record keeps its memory: a drift re-write is not a new write
# ---------------------------------------------------------------------------


async def test_a_drift_rewrite_keeps_the_prior_an_undo_restores():
    target, library = _plex_target([Candidate("Artist", "Song", external_id="1", rating=4.0)])
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    key = track_key("Artist", "Song")

    # A first run writes 10 over the user's 4, recording 4 as the prior.
    await pipeline.run_tracks([track], [target], source_service="plex", sync_store=store)
    assert store.records[("plex", "plex")][key].prior == '{"1": 4.0}'

    # The user clears it on Plex; a drift re-write puts the 10 back.
    library.edit("1", None)
    report = await pipeline.run_tracks(
        [track], [target], source_service="plex", sync_store=store, reapply_drift=True
    )
    assert _decisions(report)[0] == (OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)
    assert await library.current_rating("1") == 10.0

    # The source stops loving it: the undo still puts back the *first* prior.
    report = await pipeline.run_tracks(
        [], [target], source_service="plex", sync_store=store, reapply_drift=True
    )
    assert _decisions(report)[0] == (OutcomeStatus.REVERTED, Reason.WITHDRAWN)
    assert await library.current_rating("1") == 4.0
    assert store.records[("plex", "plex")] == {}


async def test_a_withdrawn_love_is_still_undone_with_the_option_on():
    target, client = _lastfm_target([("Artist", "Song")])
    store = MemorySyncStore()
    gone = Track("Artist", "Gone")
    await _a_recorded_love(store, gone)

    report = await pipeline.run_tracks(
        [], [target], source_service="plex", sync_store=store, reapply_drift=True
    )

    assert _decisions(report)[0] == (OutcomeStatus.REVERTED, Reason.WITHDRAWN)
    assert client.unloves == [("Artist", "Gone")]


# ---------------------------------------------------------------------------
# A dry run promises exactly what a real one does
# ---------------------------------------------------------------------------


async def test_a_dry_run_reports_the_drift_and_writes_nothing():
    target, client = _lastfm_target()
    store = MemorySyncStore()
    track = Track("Artist", "Song")
    await _a_recorded_love(store, track)

    report = await pipeline.run_tracks(
        [track],
        [target],
        source_service="plex",
        sync_store=store,
        dry_run=True,
        reapply_drift=True,
    )

    assert _decisions(report)[0] == (OutcomeStatus.WRITTEN, Reason.TARGET_DRIFT)
    assert client.loves == []
    assert store.records[("plex", "lastfm")][track.key].intent == "love"
