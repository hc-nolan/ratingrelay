"""ListenBrainz: feedback is keyed by recording MBID, so matching means resolving one.

The lookup is injected rather than imported: a test (or a future offline mode)
can hand the target its own resolver, and the target stays free of MusicBrainz
network behaviour.
"""

from dataclasses import dataclass
from typing import Optional

from liblistenbrainz import ListenBrainz

from ratingrelay.engine import http_timeouts
from ratingrelay.engine.credentials import ListenBrainzCredentials
from ratingrelay.engine.plan import Existing, WritePlan
from ratingrelay.engine.services.musicbrainz import MbidTarget, Resolver
from ratingrelay.engine.store import MappingStore, MbidCache, SyncedTrack
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import Intent, IntentKind, Track

http_timeouts.install()

PAGE_SIZE = 100


def _to_track(item: dict) -> Track:
    # ``track_metadata`` is present but null for feedback the server cannot
    # resolve any more, and a null is not a mapping: reading through it aborted
    # a whole run (RR-20).
    metadata = item.get("track_metadata") or {}
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
    """The account's feedback at one score.

    ``service`` is the account's ref (``listenbrainz``, ``listenbrainz:new``) —
    see ``engine.refs``.
    """

    client: ListenBrainzClient
    score: int = 1
    service: str = "listenbrainz"

    async def fetch(self) -> list[Track]:
        return await self.client.feedback(self.score)


class ListenBrainzTarget(MbidTarget):
    """Submits feedback for a resolved recording MBID."""

    KIND = "listenbrainz"

    def __init__(
        self,
        client: ListenBrainzClient,
        mappings: MappingStore,
        resolve: Resolver,
        score: int = 1,
        cache: Optional[MbidCache] = None,
        *,
        service: str = KIND,
    ):
        super().__init__(mappings, resolve, cache, service=service)
        self._client = client
        self._score = score
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

    async def plan(self, track: Track) -> WritePlan:
        resolution = await self.resolution(track)
        mbid = resolution.mbid
        if not mbid:
            return self.unmatched(track, resolution)

        score = self._score

        async def commit() -> None:
            await self._client.submit(score, mbid)
            # After the write, never before: a dry run does not reach here, and
            # a failed submit must not teach the cache an answer no run kept.
            await self.remembered(track, mbid)

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
                occupied=held is not None,
            ),
            commit=commit,
        )

    async def undo(self, record: SyncedTrack) -> None:
        """Put back the score the recording held before us: neutral, unless the
        user had the opposite feedback there."""
        prior = int(record.prior) if record.prior else 0
        await self._client.submit(prior, self._recorded_mbid(record))
