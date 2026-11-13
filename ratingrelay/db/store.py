"""SQL implementations of the engine's store ports.

The engine takes a ``SyncStore``, a ``MappingStore`` and an ``MbidCache``; this is
the trio that reads and writes the rows behind them. Keeping them here (rather
than in the engine) is what lets the pipeline run against an in-memory double,
and later against a CLI's own storage.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.engine.store import SyncedTrack, TrackMapping
from ratingrelay.models.mappings import TrackMapping as TrackMappingRow
from ratingrelay.models.mbids import TrackMbid
from ratingrelay.models.sync import SyncRecord


class SqlSyncStore:
    """What this tool has written, in the ``syncrecord`` table."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def load(self, source_service: str, target_service: str) -> dict[str, SyncedTrack]:
        result = await self._db.execute(
            select(SyncRecord)
            .where(SyncRecord.source_service == source_service)
            .where(SyncRecord.target_service == target_service)
        )
        return {
            row.normalized_key: SyncedTrack(
                key=row.normalized_key,
                recording_mbid=row.recording_mbid,
                intent=row.intent,
                rating=row.rating,
                target_key=row.target_key,
            )
            for row in result.scalars().all()
        }

    async def remember(
        self, source_service: str, target_service: str, synced: SyncedTrack
    ) -> None:
        """Upsert: the latest write for a key replaces what the row said before."""
        await self._delete_key(source_service, target_service, synced.key)
        self._db.add(
            SyncRecord(
                source_service=source_service,
                target_service=target_service,
                normalized_key=synced.key,
                recording_mbid=synced.recording_mbid,
                intent=synced.intent,
                rating=synced.rating,
                target_key=synced.target_key,
            )
        )
        await self._db.commit()

    async def forget(self, source_service: str, target_service: str, key: str) -> None:
        await self._delete_key(source_service, target_service, key)
        await self._db.commit()

    async def forget_target(self, target_service: str) -> None:
        """Drop every record for a target service, because its ratings were cleared."""
        await self._db.execute(
            delete(SyncRecord).where(SyncRecord.target_service == target_service)
        )
        await self._db.commit()

    async def records_for_target(
        self, target_service: str
    ) -> list[tuple[str, SyncedTrack]]:
        """Every record for a target, paired with the source that produced it.

        Reset needs this shape: it undoes per record (whatever the source was)
        and then forgets it under the pair the row was written with.
        """
        result = await self._db.execute(
            select(SyncRecord).where(SyncRecord.target_service == target_service)
        )
        return [
            (
                row.source_service,
                SyncedTrack(
                    key=row.normalized_key,
                    recording_mbid=row.recording_mbid,
                    intent=row.intent,
                    rating=row.rating,
                    target_key=row.target_key,
                ),
            )
            for row in result.scalars().all()
        ]

    async def all_records(self) -> list[tuple[str, SyncedTrack]]:
        """Every record there is, paired with the target it was written to.

        The queue uses this to ask each target which of its own rows it can no
        longer address; the answer depends on the target, so it cannot be
        spelled as SQL here.
        """
        result = await self._db.execute(select(SyncRecord))
        return [
            (
                row.target_service,
                SyncedTrack(
                    key=row.normalized_key,
                    recording_mbid=row.recording_mbid,
                    intent=row.intent,
                    rating=row.rating,
                    target_key=row.target_key,
                ),
            )
            for row in result.scalars().all()
        ]

    async def _delete_key(
        self, source_service: str, target_service: str, key: str
    ) -> None:
        await self._db.execute(
            delete(SyncRecord)
            .where(SyncRecord.source_service == source_service)
            .where(SyncRecord.target_service == target_service)
            .where(SyncRecord.normalized_key == key)
        )


class SqlMbidCache:
    """Resolved recording MBIDs, in the ``trackmbid`` table.

    Deliberately not part of the sync store: reset deletes sync records, and a
    reset must not cost a cold run's worth of MusicBrainz lookups.
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get(self, key: str) -> Optional[str]:
        result = await self._db.execute(
            select(TrackMbid).where(TrackMbid.normalized_key == key)
        )
        row = result.scalars().first()
        return row.recording_mbid if row else None

    async def put(self, key: str, artist: str, title: str, recording_mbid: str) -> None:
        """Upsert: a later resolution for the same key replaces the earlier one."""
        result = await self._db.execute(
            select(TrackMbid).where(TrackMbid.normalized_key == key)
        )
        row = result.scalars().first()
        if row is None:
            self._db.add(
                TrackMbid(
                    normalized_key=key,
                    artist=artist,
                    title=title,
                    recording_mbid=recording_mbid,
                )
            )
        else:
            row.artist = artist
            row.title = title
            row.recording_mbid = recording_mbid
            row.resolved_at = datetime.now(timezone.utc)
        await self._db.commit()


class SqlMappingStore:
    """User overrides from the ``track_mapping`` table."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def load(self, target_service: str) -> dict[str, TrackMapping]:
        result = await self._db.execute(
            select(TrackMappingRow).where(
                (TrackMappingRow.target_service == target_service)
                | (TrackMappingRow.target_service == "all")
            )
        )
        mappings: dict[str, TrackMapping] = {}
        for row in result.scalars().all():
            # A service-specific mapping wins over an "all" one.
            if row.source_normalized_key not in mappings or row.target_service == target_service:
                mappings[row.source_normalized_key] = TrackMapping(
                    row.mapped_artist, row.mapped_title, row.recording_mbid
                )
        return mappings
