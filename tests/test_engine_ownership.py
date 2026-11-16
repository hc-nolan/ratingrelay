"""Behaviour contract §3: only undo our own writes.

A target may already hold what a relay wants: a track the user loved on Last.fm
by hand, a hate on ListenBrainz, a rating the user gave in Plex. The relay must
not adopt that state as its own (a later withdrawal would remove the user's love
or rating), and where it does overwrite something, undo must put it back rather
than clear it. See docs/plans/2026-11-15-010-dont-adopt-target-state.md.
"""

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from typing import Optional

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.sync  # noqa: F401
from ratingrelay.db.store import SqlSyncStore
from ratingrelay.engine import Candidate, MemorySyncStore, OutcomeStatus, Track, pipeline
from ratingrelay.engine.services.lastfm import LastFmTarget
from ratingrelay.engine.services.listenbrainz import ListenBrainzTarget
from ratingrelay.engine.services.musicbrainz import Resolution
from ratingrelay.engine.services.plex import PlexTarget
from ratingrelay.engine.store import MemoryMappingStore, SyncedTrack
from ratingrelay.engine.types import Reason

SONG = Track("Artist", "Song")


class Library:
    """A Plex library whose ratings change when written, like the real one."""

    def __init__(self, ratings: dict[str, Optional[float]]):
        self.ratings = dict(ratings)
        self.writes: list[tuple[str, Optional[float]]] = []

    async def candidates(self) -> tuple[Candidate, ...]:
        return tuple(
            Candidate("Artist", "Song", external_id=key, rating=rating)
            for key, rating in self.ratings.items()
        )

    async def current_rating(self, external_id: str) -> Optional[float]:
        return self.ratings.get(external_id)

    async def rate(self, external_id: str, rating: Optional[float]) -> None:
        self.writes.append((external_id, rating))
        self.ratings[external_id] = rating


class LastFm:
    def __init__(self, loved: tuple[Track, ...] = ()):
        self._loved = list(loved)
        self.loves: list[tuple[str, str]] = []
        self.unloves: list[tuple[str, str]] = []

    async def loved(self) -> list[Track]:
        return list(self._loved)

    async def love(self, artist: str, title: str) -> None:
        self.loves.append((artist, title))

    async def unlove(self, artist: str, title: str) -> None:
        self.unloves.append((artist, title))


class ListenBrainz:
    def __init__(self, feedback: Optional[dict[str, int]] = None):
        self._feedback = feedback or {}
        self.submissions: list[tuple[int, str]] = []

    async def feedback(self, score: int) -> list[Track]:
        return [
            Track("Artist", "Song", recording_mbid=mbid)
            for mbid, held in self._feedback.items()
            if held == score
        ]

    async def submit(self, score: int, mbid: str) -> None:
        self.submissions.append((score, mbid))


async def _resolve(_track_mbid, _artist, _title) -> Resolution:
    return Resolution("rec-1")


def _plex(ratings, direction: str = "love", rating: float = 10.0):
    library = Library(ratings)
    return PlexTarget(library, MemoryMappingStore(), rating, direction), library


async def _run(tracks, target, store, source="lastfm", **kwargs):
    return await pipeline.run_tracks(
        tracks, [target], source_service=source, sync_store=store, **kwargs
    )


# ---------------------------------------------------------------------------
# Last.fm
# ---------------------------------------------------------------------------


async def test_a_love_the_user_made_by_hand_is_not_adopted_or_undone():
    client = LastFm(loved=(Track("artist", "SONG"),))
    target = LastFmTarget(client, MemoryMappingStore())
    store = MemorySyncStore()

    first = await _run([SONG], target, store, source="plex")
    await _run([], target, store, source="plex")

    assert first.outcomes[0].status is OutcomeStatus.SKIPPED
    assert first.outcomes[0].reason is Reason.ALREADY_ON_TARGET
    assert client.loves == [] and client.unloves == []
    assert store.records.get(("plex", "lastfm"), {}) == {}


async def test_a_love_we_recorded_is_still_ours_even_though_it_is_loved():
    """The loved list includes our own writes; a record says which ones those are."""
    client = LastFm(loved=(SONG,))
    target = LastFmTarget(client, MemoryMappingStore())
    store = MemorySyncStore()
    await store.remember(
        "plex", "lastfm", SyncedTrack(SONG.key, intent="love", target_key='["Artist", "Song"]')
    )

    first = await _run([SONG], target, store, source="plex")
    await _run([], target, store, source="plex", allow_mass_undo=True)

    assert first.outcomes[0].reason is Reason.ALREADY_SYNCED
    assert client.unloves == [("Artist", "Song")]


async def test_a_legacy_record_heals_instead_of_being_skipped_as_preexisting():
    client = LastFm(loved=(SONG,))
    target = LastFmTarget(client, MemoryMappingStore())
    store = MemorySyncStore()
    await store.remember("plex", "lastfm", SyncedTrack(SONG.key))  # intent is NULL

    report = await _run([SONG], target, store, source="plex")

    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert store.records[("plex", "lastfm")][SONG.key].intent == "love"


# ---------------------------------------------------------------------------
# ListenBrainz
# ---------------------------------------------------------------------------


async def test_listenbrainz_feedback_the_user_already_gave_is_not_adopted():
    client = ListenBrainz({"rec-1": 1})
    target = ListenBrainzTarget(client, MemoryMappingStore(), _resolve)
    store = MemorySyncStore()

    first = await _run([SONG], target, store, source="plex")
    await _run([], target, store, source="plex")

    assert first.outcomes[0].reason is Reason.ALREADY_ON_TARGET
    assert client.submissions == []


async def test_loving_over_a_hate_puts_the_hate_back_on_undo():
    client = ListenBrainz({"rec-1": -1})
    target = ListenBrainzTarget(client, MemoryMappingStore(), _resolve)
    store = MemorySyncStore()

    await _run([SONG], target, store, source="plex")
    assert store.records[("plex", "listenbrainz")][SONG.key].prior == "-1"
    await _run([], target, store, source="plex", allow_mass_undo=True)

    assert client.submissions == [(1, "rec-1"), (-1, "rec-1")]


async def test_listenbrainz_feedback_we_wrote_onto_nothing_is_neutralised():
    client = ListenBrainz()
    target = ListenBrainzTarget(client, MemoryMappingStore(), _resolve)
    store = MemorySyncStore()

    await _run([SONG], target, store, source="plex")
    await _run([], target, store, source="plex", allow_mass_undo=True)

    assert client.submissions == [(1, "rec-1"), (0, "rec-1")]


# ---------------------------------------------------------------------------
# Plex as a target (v1's TWO_WAY)
# ---------------------------------------------------------------------------


async def test_a_rating_the_user_gave_is_restored_not_cleared():
    target, library = _plex({"1": 6.0})
    store = MemorySyncStore()

    await _run([SONG], target, store)
    assert library.ratings == {"1": 10.0}
    await _run([], target, store, allow_mass_undo=True)

    assert library.ratings == {"1": 6.0}
    assert library.writes == [("1", 10.0), ("1", 6.0)]


async def test_an_unrated_track_goes_back_to_unrated():
    target, library = _plex({"1": None})
    store = MemorySyncStore()

    await _run([SONG], target, store)
    await _run([], target, store, allow_mass_undo=True)

    assert library.writes == [("1", 10.0), ("1", None)]


@pytest.mark.parametrize(
    "direction, rating, held",
    [
        ("love", 8.0, 8.0),  # exactly the love rating
        ("love", 8.0, 10.0),  # a 5-star track is not lowered to 4
        ("hate", 2.0, 2.0),
        ("hate", 2.0, 1.0),  # half a star is already "hated"; not raised to 1 star
    ],
)
async def test_a_copy_already_on_the_right_side_is_left_alone(direction, rating, held):
    target, library = _plex({"1": held}, direction, rating)
    store = MemorySyncStore()

    first = await _run([SONG], target, store)
    await _run([], target, store, allow_mass_undo=True)

    assert first.outcomes[0].reason is Reason.ALREADY_ON_TARGET
    assert library.writes == []


@pytest.mark.parametrize(
    "direction, rating, held",
    [
        ("love", 8.0, 6.0),  # below a love: raised
        ("hate", 2.0, 6.0),  # above a hate: lowered
        ("hate", 2.0, None),  # unrated is not a hate
    ],
)
async def test_a_copy_on_the_wrong_side_is_written(direction, rating, held):
    target, library = _plex({"1": held}, direction, rating)
    store = MemorySyncStore()

    report = await _run([SONG], target, store)

    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert library.writes == [("1", rating)]


async def test_only_copies_that_do_not_hold_it_are_written_and_undone():
    target, library = _plex({"1": 10.0, "2": 4.0})
    store = MemorySyncStore()

    await _run([SONG], target, store)
    assert library.writes == [("2", 10.0)]
    await _run([], target, store, allow_mass_undo=True)

    assert library.ratings == {"1": 10.0, "2": 4.0}


async def test_a_rating_the_user_changed_after_us_is_left_alone_on_undo():
    target, library = _plex({"1": 6.0})
    store = MemorySyncStore()

    await _run([SONG], target, store)
    library.ratings["1"] = 4.0  # the user re-rated it in Plex
    await _run([], target, store, allow_mass_undo=True)

    assert library.ratings == {"1": 4.0}
    assert library.writes == [("1", 10.0)]


async def test_a_rewrite_keeps_the_original_prior_not_our_earlier_write():
    target, library = _plex({"1": 6.0}, rating=8.0)
    store = MemorySyncStore()
    await _run([SONG], target, store)

    # Same library; the relay's rating was changed to 10.
    target = PlexTarget(library, MemoryMappingStore(), 10.0)
    await _run([SONG], target, store)
    assert library.ratings == {"1": 10.0}
    await _run([], target, store, allow_mass_undo=True)

    assert library.ratings == {"1": 6.0}


async def test_dry_run_reports_what_a_real_run_does():
    for held, expected in ((10.0, OutcomeStatus.SKIPPED), (4.0, OutcomeStatus.WRITTEN)):
        dry_target, dry_library = _plex({"1": held})
        dry = await _run([SONG], dry_target, MemorySyncStore(), dry_run=True)
        real_target, _ = _plex({"1": held})
        real = await _run([SONG], real_target, MemorySyncStore())

        assert dry_library.writes == []
        assert dry.outcomes[0].reason == real.outcomes[0].reason
        assert real.outcomes[0].status is expected


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def test_the_sql_store_keeps_the_prior():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    prior = json.dumps({"1": 6.0})
    async with factory() as session:
        store = SqlSyncStore(session, "love")
        await store.remember(
            "lastfm", "plex", SyncedTrack(SONG.key, intent="rating", rating=10.0, prior=prior)
        )
        loaded = await store.load("lastfm", "plex")
    await engine.dispose()

    assert loaded[SONG.key].prior == prior


def test_the_prior_migration_adds_a_nullable_column_and_reverses(tmp_path):
    root = pathlib.Path(__file__).resolve().parent.parent
    database = tmp_path / "prior.db"
    env = {**os.environ, "DATABASE": str(database)}

    def alembic(*args: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
        )

    def columns() -> set[str]:
        with sqlite3.connect(database) as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(syncrecord)")}

    alembic("upgrade", "c4d5e6f7a8b9")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO syncrecord (id, source_service, target_service, normalized_key,"
            " synced_at, relay) VALUES ('1', 'plex', 'lastfm', 'a', '2026-01-01 00:00:00', 'love')"
        )
    alembic("upgrade", "head")
    assert "prior" in columns()
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT prior FROM syncrecord").fetchall() == [(None,)]

    alembic("downgrade", "c4d5e6f7a8b9")
    assert "prior" not in columns()
