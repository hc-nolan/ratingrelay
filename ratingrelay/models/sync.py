from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SyncRecord(SQLModel, table=True):
    """A write this tool made: which intent, to which track, and where.

    ``intent`` is what reconciliation compares against: ``love``, ``hate`` or
    ``rating:<value>``. It is nullable for rows written before v3, which said
    only that *something* was synced; those rows are never undone because they
    do not record what to undo.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    source_service: str = Field()
    target_service: str = Field()
    normalized_key: str = Field()   # track_key(artist, title)
    recording_mbid: Optional[str] = Field(default=None)
    intent: Optional[str] = Field(default=None)
    rating: Optional[float] = Field(default=None)
    target_key: Optional[str] = Field(default=None)   # opaque per target
    synced_at: datetime = Field(default_factory=_utcnow)
