"""Last.fm: a source of loves and a target for loves, with no library to match.

Last.fm has no catalogue to match against: "love this artist and title" is the
whole operation. Matching therefore happens when the love is written, which is
why this target never reports ``NO_MATCH`` — only a track missing the metadata
Last.fm needs to look one up.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional

import pylast

from ratingrelay.engine.credentials import LastFmCredentials
from ratingrelay.engine.plan import WritePlan
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


class LastFmClient:
    """An async facade over the signed pylast session."""

    def __init__(self, credentials: LastFmCredentials):
        self._credentials = credentials
        self._network: Optional["pylast.LastFMNetwork"] = None

    async def network(self) -> "pylast.LastFMNetwork":
        if self._network is None:
            creds = self._credentials
            self._network = await run_sync(
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
        user = await run_sync(network.get_authenticated_user)
        loved = await run_sync(lambda: user.get_loved_tracks(limit=None))
        return [Track(artist=str(item.track.artist), title=str(item.track.title)) for item in loved]

    async def love(self, artist: str, title: str) -> None:
        track = pylast.Track(artist, title, await self.network())
        await run_sync(track.love)
        await asyncio.sleep(WRITE_DELAY_SECONDS)

    async def unlove(self, artist: str, title: str) -> None:
        track = pylast.Track(artist, title, await self.network())
        await run_sync(track.unlove)
        await asyncio.sleep(WRITE_DELAY_SECONDS)


@dataclass
class LastFmSource:
    """The account's loved tracks."""

    client: LastFmClient
    service: str = field(default="lastfm", init=False)

    async def fetch(self) -> list[Track]:
        return await self.client.loved()


class LastFmTarget(MappedTarget):
    """Loves a track, with a user mapping applied when one exists."""

    service = "lastfm"

    def __init__(self, client: LastFmClient, mappings: MappingStore):
        super().__init__(mappings)
        self._client = client

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

        return WritePlan(
            track,
            self.service,
            intent=Intent(IntentKind.LOVE),
            target_key=json.dumps([artist, title]),
            commit=commit,
        )

    async def undo(self, record: SyncedTrack) -> None:
        """Un-love what we loved, by the name we loved it under."""
        if not record.target_key:
            raise ValueError("no recorded Last.fm track name")
        artist, title = json.loads(record.target_key)
        await self._client.unlove(artist, title)
