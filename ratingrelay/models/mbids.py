from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrackMbid(SQLModel, table=True):
    """Which MusicBrainz recording a track is.

    Kept apart from ``SyncRecord`` on purpose. A sync record says *what we wrote
    to a target*, and reset exists to retract it; this says *what the track is*,
    and nothing a reset does makes that untrue. Losing it would send the next
    run back to MusicBrainz at one request a second.

    The key is ``track_key(artist, title)`` — the same string the resolver is
    asked about — so a lookup needs no artist/title parsing. ``artist`` and
    ``title`` are kept alongside it for logs and for reading the table by hand.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    normalized_key: str = Field(unique=True, index=True)
    artist: str = Field(default="")
    title: str = Field(default="")
    recording_mbid: str = Field()
    resolved_at: datetime = Field(default_factory=_utcnow)


__all__ = ["TrackMbid"]
