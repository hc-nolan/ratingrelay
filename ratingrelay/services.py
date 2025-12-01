from dataclasses import dataclass
from typing import Optional

from .plex import Plex
from .database import Database
from .lastfm import LastFM
from .listenbrainz import ListenBrainz


@dataclass
class Services:
    """Container for various services used by the script"""

    plex: Plex
    db: Database
    lfm: Optional[LastFM]
    lbz: Optional[ListenBrainz]
