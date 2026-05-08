import uuid
from datetime import datetime
from typing import List, Optional
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlmodel import Field, SQLModel, Relationship

from ratingrelay.settings import get_settings


settings = get_settings()
timezone = ZoneInfo(settings.timezone)


class Base(SQLModel, table=False):
    """

    Attributes:
        id: Unique identifier
        added: Creation timestamp
        recording_id: MusicBrainz recording ID
        track_id: MusicBrainz track ID
        title: Track title
        artist: Artist name
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    added: datetime = Field(default_factory=lambda: datetime.now(tz=timezone))
    recoring_id: uuid.UUID = Field()
    track_id: uuid.UUID = Field(unique=True)
    title: str = Field()
    artist: str = Field()


class Hates(Base, table=True):
    pass


class Loves(Base, table=True):
    pass


class Reset(Base, table=True):
    pass
