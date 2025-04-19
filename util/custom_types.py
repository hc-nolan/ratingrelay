from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TrackTuple:
    """
    Tuple class to represent tracks as (track_title, artist_name) tuples
    """

    title: str
    artist: str
    mbid: Optional[str] = None
