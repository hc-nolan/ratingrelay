from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class RatingThresholds(SQLModel, table=True):
    """The love/hate thresholds as set from the UI. One row, ``id`` 1.

    A column is ``None`` until the user sets it, so the default (or a later
    change of default) applies. A threshold set explicitly in settings wins over
    its column: see :func:`ratingrelay.core.thresholds.effective_thresholds`.
    """

    __tablename__ = "rating_thresholds"

    id: int = Field(default=1, primary_key=True)
    love: Optional[float] = Field(default=None)
    hate: Optional[float] = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
