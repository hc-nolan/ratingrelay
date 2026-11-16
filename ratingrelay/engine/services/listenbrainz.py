"""ListenBrainz: feedback is keyed by recording MBID, so matching means resolving one.

The lookup is injected rather than imported: a test (or a future offline mode)
can hand the target its own resolver, and the target stays free of MusicBrainz
network behaviour.
"""

from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from liblistenbrainz import ListenBrainz

from ratingrelay.engine import http_timeouts
from ratingrelay.engine.credentials import ListenBrainzCredentials
from ratingrelay.engine.plan import Existing, WritePlan
from ratingrelay.engine.services import MappedTarget
from ratingrelay.engine.services.musicbrainz import Resolution
from ratingrelay.engine.store import MappingStore, MbidCache, NullMbidCache, SyncedTrack
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import (
    Intent,
    IntentKind,
    OutcomeStatus,
    Reason,
    Track,
)

http_timeouts.install()

PAGE_SIZE = 100

Resolver = Callable[[Optional[str], str, str], Awaitable[Resolution]]


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
        """Submit feedback.

        Rate limiting is liblistenbrainz's job: it waits out ``X-RateLimit-Reset-In``
        and retries a 429 itself (honouring ``Retry-After``). When it gives up it
        raises ``requests.RetryError`` — an ``OSError``, so the engine's retry
        sees it like any other transport failure.
        """
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
        cache: Optional[MbidCache] = None,
    ):
        super().__init__(mappings)
        self._client = client
        self._resolve = resolve
        self._score = score
        self._cache = cache or NullMbidCache()
        self._feedback: Optional[dict[str, int]] = None

    async def feedback(self) -> dict[str, int]:
        """``{recording_mbid: score}`` for the account's loves and hates, once per run."""
        if self._feedback is None:
            feedback: dict[str, int] = {}
            for score in (1, -1):
                for item in await self._client.feedback(score):
                    if item.recording_mbid:
                        feedback[item.recording_mbid] = score
            self._feedback = feedback
        return self._feedback

    async def resolution(self, track: Track) -> Resolution:
        """The recording this track is, or the ones it was nearly.

        Order is authority: what the source said, then what a human decided,
        then what we resolved before, then the network. What we resolved before
        (the recorded MBID and the cache, both keyed by the *source* track) sits
        below the mapping on purpose — it answered for the unmapped name, so
        where a mapping exists it is not consulted at all; a cache that
        outranked a user's correction would make the correction stop working.
        """
        artist, title, mapped_mbid = await self.mapped(track)
        if track.recording_mbid:
            return Resolution(track.recording_mbid)
        if mapped_mbid:
            return Resolution(mapped_mbid)
        if await self.has_mapping(track):
            return await self._resolve(track.track_mbid, artist, title)
        if track.known_recording_mbid:
            return Resolution(track.known_recording_mbid)
        cached = await self._cache.get(track.key)
        if cached:
            return Resolution(cached)
        return await self._resolve(track.track_mbid, artist, title)

    async def plan(self, track: Track) -> WritePlan:
        resolution = await self.resolution(track)
        mbid = resolution.mbid
        if not mbid:
            return WritePlan(
                track,
                self.service,
                OutcomeStatus.UNMATCHED,
                Reason.NO_RECORDING_MBID,
                "No MusicBrainz recording ID found",
                matches=resolution.proposals,
            )

        score = self._score
        cache = self._cache

        async def commit() -> None:
            await self._client.submit(score, mbid)
            # After the write, never before: a dry run does not reach here, and
            # a failed submit must not teach the cache an answer no run kept.
            await cache.put(track.key, track.artist, track.title, mbid)

        kind = IntentKind.LOVE if score > 0 else IntentKind.HATE
        held = (await self.feedback()).get(mbid)
        return WritePlan(
            track,
            self.service,
            recording_mbid=mbid,
            intent=Intent(kind),
            target_key=mbid,
            mapped=await self.has_mapping(track),
            # Loving over the user's hate (or the reverse) must hand it back on undo.
            existing=Existing(
                holds=held == score,
                prior=str(held) if held is not None else None,
            ),
            commit=commit,
        )

    @staticmethod
    def _recorded_mbid(record: SyncedTrack) -> str:
        """The recording we gave feedback on, or ``ValueError`` if unrecorded.

        Feedback is keyed by recording MBID, so a row written before
        ``target_key`` existed is still undoable when it recorded the MBID.
        """
        mbid = record.target_key or record.recording_mbid
        if not mbid:
            raise ValueError("no recorded recording MBID")
        return mbid

    @staticmethod
    def undoable(record: SyncedTrack) -> None:
        """Raise exactly when :meth:`undo` would refuse this record.

        Static so that "could we undo this?" can be answered from the record
        alone, without building a client or touching the network.
        """
        ListenBrainzTarget._recorded_mbid(record)

    async def undo(self, record: SyncedTrack) -> None:
        """Put back the score the recording held before us: neutral, unless the
        user had the opposite feedback there."""
        prior = int(record.prior) if record.prior else 0
        await self._client.submit(prior, self._recorded_mbid(record))
