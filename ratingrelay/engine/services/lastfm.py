"""Last.fm: a source of loves and a target for loves, with no library to match.

Last.fm has no catalogue to match against: "love this artist and title" is the
whole operation. Matching therefore happens when the love is written, which is
why this target never reports ``NO_MATCH`` — only a track missing the metadata
Last.fm needs to look one up.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Optional

import pylast

from ratingrelay.engine.credentials import LastFmCredentials
from ratingrelay.engine.normalize import track_key
from ratingrelay.engine.plan import Existing, WritePlan
from ratingrelay.engine.services import MappedTarget
from ratingrelay.engine.store import MappingStore, SyncedTrack
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import (
    Intent,
    IntentKind,
    OutcomeStatus,
    Reason,
    Track,
)

WRITE_DELAY_SECONDS = 0.3

# Last.fm error codes that mean "try again", not "you asked for the wrong thing":
# operation failed, service offline, temporarily unavailable, rate limited — plus
# the HTTP 5xx statuses pylast raises before it parses a body.
_TRANSIENT_STATUSES = {"8", "11", "16", "29", "500", "502", "503", "504"}


def _transient(exc: Exception) -> bool:
    if isinstance(exc, pylast.NetworkError):
        return True
    return isinstance(exc, pylast.WSError) and str(exc.status) in _TRANSIENT_STATUSES


async def _call(fn):
    """Run a blocking pylast call, surfacing transport trouble as ``OSError``.

    pylast wraps every connection failure in its own ``NetworkError``, which is
    not an ``OSError``, so without this the engine's retry would never see one.
    """
    try:
        return await run_sync(fn)
    except pylast.PyLastError as exc:
        if _transient(exc):
            raise ConnectionError(f"Last.fm: {exc}") from exc
        raise


class LastFmClient:
    """An async facade over the signed pylast session."""

    def __init__(self, credentials: LastFmCredentials):
        self._credentials = credentials
        self._network: Optional["pylast.LastFMNetwork"] = None

    async def network(self) -> "pylast.LastFMNetwork":
        if self._network is None:
            creds = self._credentials
            self._network = await _call(
                lambda: pylast.LastFMNetwork(
                    api_key=creds.api_key,
                    api_secret=creds.api_secret,
                    username=creds.username,
                    password_hash=creds.password_hash,
                )
            )
        return self._network

    async def loved(self) -> list[Track]:
        network = await self.network()
        user = await _call(network.get_authenticated_user)
        loved = await _call(lambda: user.get_loved_tracks(limit=None))
        return [Track(artist=str(item.track.artist), title=str(item.track.title)) for item in loved]

    async def love(self, artist: str, title: str) -> None:
        track = pylast.Track(artist, title, await self.network())
        await _call(track.love)
        await asyncio.sleep(WRITE_DELAY_SECONDS)

    async def unlove(self, artist: str, title: str) -> None:
        track = pylast.Track(artist, title, await self.network())
        await _call(track.unlove)
        await asyncio.sleep(WRITE_DELAY_SECONDS)


@dataclass
class LastFmSource:
    """The account's loved tracks.

    ``service`` is the account's ref (``lastfm``, ``lastfm:old``) — see
    ``engine.refs``.
    """

    client: LastFmClient
    service: str = "lastfm"

    async def fetch(self) -> list[Track]:
        return await self.client.loved()


class LastFmTarget(MappedTarget):
    """Loves a track, with a user mapping applied when one exists."""

    KIND = "lastfm"

    def __init__(
        self, client: LastFmClient, mappings: MappingStore, service: str = KIND
    ):
        super().__init__(mappings)
        self.service = service
        self._client = client
        self._loved: Optional[set[str]] = None

    async def loved_keys(self) -> set[str]:
        """The account's loves as normalized keys, fetched once per run."""
        if self._loved is None:
            self._loved = {loved.key for loved in await self._client.loved()}
        return self._loved

    async def plan(self, track: Track) -> WritePlan:
        artist, title, _ = await self.mapped(track)
        if not artist or not title:
            return WritePlan(
                track,
                self.service,
                OutcomeStatus.FAILED,
                Reason.MISSING_METADATA,
                "Missing artist or title",
            )

        async def commit() -> None:
            await self._client.love(artist, title)

        loved = track_key(artist, title) in await self.loved_keys()
        return WritePlan(
            track,
            self.service,
            intent=Intent(IntentKind.LOVE),
            target_key=json.dumps([artist, title]),
            mapped=await self.has_mapping(track),
            # A love has no value to replace: loved or not is the whole state.
            existing=Existing(holds=loved, occupied=loved),
            commit=commit,
        )

    @staticmethod
    def _track_name(record: SyncedTrack) -> tuple[str, str]:
        """The name we loved it under, or ``ValueError`` if the record does not say."""
        if not record.target_key:
            raise ValueError("no recorded Last.fm track name")
        artist, title = json.loads(record.target_key)
        return artist, title

    @staticmethod
    def undoable(record: SyncedTrack) -> None:
        """Raise exactly when :meth:`undo` would refuse this record.

        Static so that "could we undo this?" can be answered from the record
        alone, without building a client or touching the network.
        """
        LastFmTarget._track_name(record)

    async def undo(self, record: SyncedTrack) -> None:
        """Un-love what we loved, by the name we loved it under."""
        artist, title = self._track_name(record)
        await self._client.unlove(artist, title)
