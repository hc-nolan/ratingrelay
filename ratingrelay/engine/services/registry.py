"""Ask "could we undo this record?" without building a client.

The answer belongs to the target that wrote the row: Plex needs the rating keys
it recorded, Last.fm needs the name it loved under, ListenBrainz needs the
recording MBID. Re-deriving that rule anywhere else (a SQL predicate, a copy in
the API) is how the dry run once promised undos the real run refused, so the
registry holds the *same function objects* those targets' ``undoable`` uses.

They are static methods, so asking the question costs no client and no network.
"""

from collections.abc import Callable

from ratingrelay.engine.services.lastfm import LastFmTarget
from ratingrelay.engine.services.listenbrainz import ListenBrainzTarget
from ratingrelay.engine.services.plex import PlexTarget
from ratingrelay.engine.store import SyncedTrack

# Raises ValueError exactly when the target's undo would refuse the record.
UndoPrecondition = Callable[[SyncedTrack], None]

UNDO_PRECONDITIONS: dict[str, UndoPrecondition] = {
    PlexTarget.service: PlexTarget.undoable,
    LastFmTarget.service: LastFmTarget.undoable,
    ListenBrainzTarget.service: ListenBrainzTarget.undoable,
}
