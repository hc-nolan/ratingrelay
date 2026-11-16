"""Which Plex ratings count as a love and a hate, after every override.

Settings beat the database, as with credentials: a threshold set explicitly in
the environment or ``.config`` wins and the UI shows it locked. Otherwise the
value saved from the UI applies, and failing that the default.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db.thresholds import get_thresholds
from ratingrelay.settings import Settings, get_settings


@dataclass(frozen=True)
class Thresholds:
    """The thresholds in force, and which ones settings pin."""

    love: float
    hate: float
    love_locked: bool
    hate_locked: bool

    def for_relay(self, relay: str) -> float:
        """The threshold for *relay* (``love``/``hate``)."""
        return self.love if relay == "love" else self.hate

    def overlap(self) -> Optional[str]:
        """Why this pair can't be used, or None. A track can't be both."""
        if self.hate >= self.love:
            return (
                f"the hate threshold ({self.hate:g}) must be below "
                f"the love threshold ({self.love:g})"
            )
        return None


def locked_fields(current: Settings) -> tuple[bool, bool]:
    """Whether settings set the love and hate thresholds explicitly."""
    explicit = current.model_fields_set
    return "love_threshold" in explicit, "hate_threshold" in explicit


async def effective_thresholds(
    db: AsyncSession, current: Optional[Settings] = None
) -> Thresholds:
    """The thresholds every relay uses now."""
    current = current or get_settings()
    love_locked, hate_locked = locked_fields(current)
    stored = await get_thresholds(db)
    love = current.love_threshold
    hate = current.hate_threshold
    if stored is not None:
        if not love_locked and stored.love is not None:
            love = stored.love
        if not hate_locked and stored.hate is not None:
            hate = stored.hate
    return Thresholds(love, hate, love_locked, hate_locked)
