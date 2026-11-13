"""Undoability is one rule, asked in three places.

`undo()` enforces it, `undoable()` asks it without side effects (so a dry run can
promise only what a real run keeps), and the reset queue lists the rows it
refuses. If any of those grows its own copy of the rule, a queue can advertise a
reset that then refuses — the exact divergence this project already had once.
"""

import pytest

from ratingrelay.engine.services.lastfm import LastFmTarget
from ratingrelay.engine.services.listenbrainz import ListenBrainzTarget
from ratingrelay.engine.services.plex import PlexTarget
from ratingrelay.engine.services.registry import UNDO_PRECONDITIONS
from ratingrelay.engine.store import MemoryMappingStore, SyncedTrack

TARGETS = (PlexTarget, LastFmTarget, ListenBrainzTarget)


def test_the_queue_asks_the_question_the_targets_undo_runs():
    """Same function object, not an equal one: no room for a second rule."""
    for cls in TARGETS:
        assert UNDO_PRECONDITIONS[cls.service] is cls.undoable


@pytest.mark.parametrize("cls", TARGETS, ids=lambda c: c.service)
async def test_undo_refuses_an_unaddressable_record_without_calling_out(cls):
    calls: list[tuple] = []

    class StubClient:
        async def unlove(self, artist, title):
            calls.append((artist, title))

        async def submit(self, score, mbid):
            calls.append((score, mbid))

    class StubLibrary:
        async def rate(self, external_id, rating):
            calls.append((external_id, rating))

    async def resolve(_mbid, _artist, _title):
        return None

    mapping_store = MemoryMappingStore()
    if cls is PlexTarget:
        target = PlexTarget(StubLibrary(), mapping_store)
    elif cls is LastFmTarget:
        target = LastFmTarget(StubClient(), mapping_store)
    else:
        target = ListenBrainzTarget(StubClient(), mapping_store, resolve)

    record = SyncedTrack(key="artist||song", recording_mbid=None, intent="love")

    with pytest.raises(ValueError):
        await target.undo(record)
    assert calls == [], "undo wrote something before checking it could"


@pytest.mark.parametrize("cls", TARGETS, ids=lambda c: c.service)
def test_asking_is_side_effect_free(cls):
    """``undoable`` must not need a client at all: the queue calls it per row."""
    record = SyncedTrack(key="artist||song", intent="love")

    with pytest.raises(ValueError):
        UNDO_PRECONDITIONS[cls.service](record)
