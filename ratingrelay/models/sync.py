from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SyncRecord(SQLModel, table=True):
    """Tracks which (source, target, track) combinations have been successfully synced."""
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    source_service: str = Field()
    target_service: str = Field()
    normalized_key: str = Field()   # _normalize(artist) + "||" + _normalize(title)
    recording_mbid: Optional[str] = Field(default=None)
    synced_at: datetime = Field(default_factory=_utcnow)
