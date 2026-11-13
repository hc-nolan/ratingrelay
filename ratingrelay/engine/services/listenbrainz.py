"""ListenBrainz: feedback is keyed by recording MBID, so matching means resolving one.

The lookup is injected rather than imported: a test (or a future offline mode)
can hand the target its own resolver, and the target stays free of MusicBrainz
network behaviour.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from liblistenbrainz import ListenBrainz

from ratingrelay.engine.credentials import ListenBrainzCredentials
from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.services import MappedTarget
from ratingrelay.engine.store import MappingStore
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import (
    Intent,
    IntentKind,
    OutcomeStatus,
    Reason,
    Track,
)

PAGE_SIZE = 100
RATE_LIMIT_BACKOFF_SECONDS = 60

Resolver = Callable[[Optional[str], str, str], Awaitable[Optional[str]]]


def _to_track(item: dict) -> Track:
    metadata = item.get("track_metadata", {})
    return Track(
        artist=metadata.get("artist_name", ""),
        title=metadata.get("track_name", ""),
        recording_mbid=item.get("recording_mbid") or None,
    )


class ListenBrainzClient:
    """An async facade over the ListenBrainz client, including its paging."""

    def __init__(self, credentials: ListenBrainzCredentials):
        self._credentials = credentials
        self._client = ListenBrainz()
        self._client.set_auth_token(credentials.token)

    async def feedback(self, score: int) -> list[Track]:
        """All feedback this account holds at *score* (1 love, -1 hate)."""
        username = self._credentials.username
        tracks: list[Track] = []
        offset = 0
        while True:
            response = await run_sync(
                lambda o=offset: self._client.get_user_feedback(
                    username, score, True, PAGE_SIZE, o
                )
            )
            if response is None:
                break
            batch = response.get("feedback", [])
            tracks.extend(_to_track(item) for item in batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return tracks

    async def submit(self, score: int, mbid: str) -> None:
        """Submit feedback, backing off once if the service rate-limits us."""
        try:
            await run_sync(lambda: self._client.submit_user_feedback(score, mbid))
        except Exception as exc:  # noqa: BLE001 - inspected below
            if "429" not in str(exc):
                raise
            await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS)
            await run_sync(lambda: self._client.submit_user_feedback(score, mbid))


@dataclass
class ListenBrainzSource:
    """The account's feedback at one score."""

    client: ListenBrainzClient
    score: int = 1
    service: str = field(default="listenbrainz", init=False)

    async def fetch(self) -> list[Track]:
        return await self.client.feedback(self.score)


class ListenBrainzTarget(MappedTarget):
    """Submits feedback for a resolved recording MBID."""

    service = "listenbrainz"

    def __init__(
        self,
        client: ListenBrainzClient,
        mappings: MappingStore,
        resolve: Resolver,
        score: int = 1,
    ):
        super().__init__(mappings)
        self._client = client
        self._resolve = resolve
        self._score = score

    async def recording_mbid(self, track: Track) -> Optional[str]:
        artist, title, mapped_mbid = await self.mapped(track)
        if track.recording_mbid:
            return track.recording_mbid
        if mapped_mbid:
            return mapped_mbid
        return await self._resolve(track.track_mbid, artist, title)

    async def plan(self, track: Track) -> WritePlan:
        mbid = await self.recording_mbid(track)
        if not mbid:
            return WritePlan(
                track,
                self.service,
                OutcomeStatus.UNMATCHED,
                Reason.NO_RECORDING_MBID,
                "No MusicBrainz recording ID found",
            )

        score = self._score

        async def commit() -> None:
            await self._client.submit(score, mbid)

        kind = IntentKind.LOVE if score > 0 else IntentKind.HATE
        return WritePlan(
            track,
            self.service,
            recording_mbid=mbid,
            intent=Intent(kind),
            commit=commit,
        )
