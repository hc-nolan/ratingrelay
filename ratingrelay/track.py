from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    mbid: Optional[str] = None
    track_mbid: Optional[str] = None
