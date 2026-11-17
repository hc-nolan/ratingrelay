"""Hyperfix as a source and a target of ratings (HF-182, HF-198).

A thin adapter over hyperfix's music module. As a **source**, ``GET /api/music/ratings``
returns every rated recording with its recording MBID, artist, title and a 0-100 rating —
identity is exact there, so nothing is resolved through MusicBrainz. As a **target**,
``PATCH /api/music/recordings/{mbid}`` sets or clears one recording's rating, which does
mean resolving: hyperfix stores ratings against recordings, like ListenBrainz stores
feedback against them (``MbidTarget``).

Note what a write here *also* does: hyperfix queues a Plex rating push for the same
recording, so a rating written into hyperfix reaches Plex through hyperfix. That is why a
hyperfix *source* still may not target Plex (two reconcilers on one field), while a hyperfix
*target* is fine.

That feed is deliberately not a delta: this engine decides what to withdraw by absence from
the fetched set, so a partial answer would read as "the user withdrew these" and either
un-love the library or trip the mass-undo guard.

The rating scale maps cleanly: hyperfix's 80/20 love/hate are Plex's 8.0/2.0 divided by ten,
which are this tool's defaults, so the shared thresholds describe both ends. A hyperfix
``0`` becomes ``0.1``: this engine treats ``0.0`` as unrated, and a deliberate zero must
still fail a love threshold and pass a hate one. Writing goes the other way and never sends
0, for the same reason.
"""

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import httpx

from ratingrelay.engine.credentials import HyperfixCredentials
from ratingrelay.engine.plan import Existing, WritePlan
from ratingrelay.engine.services.musicbrainz import MbidTarget, Resolver
from ratingrelay.engine.store import MappingStore, MbidCache, SyncedTrack
from ratingrelay.engine.types import DEFAULT_LOVE_RATING, Intent, IntentKind, Track
from ratingrelay.settings import logger

FEED_PATH = "/api/music/ratings"
RECORDING_PATH = "/api/music/recordings/{mbid}"
PAGE_SIZE = 500
# A zero rating is real here and "unrated" there, so it is sent just above nothing.
ZERO_RATING = 0.1
REQUEST_TIMEOUT = 30.0


class HyperfixError(Exception):
    """hyperfix answered with something this adapter cannot use."""


def _rating(value: Any) -> float:
    """The feed's 0-100 rating as this engine's 0-10 scale, 0 raised to ``ZERO_RATING``."""
    try:
        rating = float(value) / 10
    except (TypeError, ValueError) as exc:
        raise HyperfixError(f"rating {value!r} is not a number") from exc
    if not 0 <= rating <= 10:
        raise HyperfixError(f"rating {value!r} is outside 0-100")
    return rating if rating > 0 else ZERO_RATING


@dataclass(frozen=True, slots=True)
class HyperfixClient:
    """Reads hyperfix's ratings feed, one page at a time."""

    credentials: HyperfixCredentials
    page_size: int = PAGE_SIZE
    http: Optional[httpx.AsyncClient] = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One call to hyperfix, with the bearer key and a deadline on it.

        A transport failure is a ``ConnectionError``, not a :class:`HyperfixError`:
        the engine retries it as the outage it is, and fails one track rather than
        the run. A refused or unreadable *answer* is not retried — it will not
        improve by asking again.
        """
        url = f"{self.credentials.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.credentials.token}"}
        client = self.http or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        try:
            return await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectionError(f"hyperfix did not answer: {exc}") from exc
        finally:
            if self.http is None:
                await client.aclose()

    async def _get(self, cursor: Optional[str]) -> dict:
        params: dict[str, Any] = {"limit": self.page_size}
        if cursor:
            params["cursor"] = cursor
        response = await self._request("GET", FEED_PATH, params=params)
        if response.status_code != 200:
            raise HyperfixError(f"hyperfix answered {response.status_code} for {FEED_PATH}")
        try:
            body = response.json()
        except ValueError as exc:
            raise HyperfixError("hyperfix answered with non-JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise HyperfixError("hyperfix answered with an unexpected shape")
        return body

    async def write_rating(self, mbid: str, rating: Optional[int]) -> None:
        """Set one recording's rating (0-100), or clear it with ``None``."""
        path = RECORDING_PATH.format(mbid=quote(mbid, safe=""))
        response = await self._request("PATCH", path, json={"rating": rating})
        if response.status_code not in (200, 204):
            raise HyperfixError(f"hyperfix answered {response.status_code} for {path}")

    async def recording_rating(self, mbid: str) -> Optional[int]:
        """One recording's current rating, or ``None`` when it holds none.

        A recording hyperfix does not know is a miss, not an error: the relay has
        nothing to compare against, and :meth:`write_rating` will say so if it tries.
        """
        path = RECORDING_PATH.format(mbid=quote(mbid, safe=""))
        response = await self._request("GET", path)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise HyperfixError(f"hyperfix answered {response.status_code} for {path}")
        try:
            body = response.json()
        except ValueError as exc:
            raise HyperfixError("hyperfix answered with non-JSON") from exc
        rating = body.get("rating") if isinstance(body, dict) else None
        return None if rating is None else int(rating)

    async def hundredths(self) -> dict[str, int]:
        """``{recording_mbid: rating}`` exactly as the feed states it, 0 included.

        The target needs the raw value, not the engine's scale: a recorded ``prior``
        of ``0`` has to come back as ``0``, and the 0-to-0.1 raise on the way in
        would turn the user's zero into a one.
        """
        ratings: dict[str, int] = {}
        cursor: Optional[str] = None
        seen: set[str] = set()
        while True:
            body = await self._get(cursor)
            for item in body["items"]:
                mbid = item.get("recording_mbid")
                value = item.get("rating")
                if not mbid or value is None:
                    continue
                try:
                    ratings[str(mbid)] = int(value)
                except (TypeError, ValueError) as exc:
                    raise HyperfixError(f"rating {value!r} is not a number") from exc
            next_cursor = body.get("next_cursor")
            if not next_cursor:
                return ratings
            if str(next_cursor) in seen:
                raise HyperfixError("hyperfix repeated a cursor; refusing to loop")
            seen.add(str(next_cursor))
            cursor = str(next_cursor)

    async def ratings(self) -> list[Track]:
        """Every rated recording hyperfix holds, walking the feed's pages."""
        tracks: list[Track] = []
        cursor: Optional[str] = None
        seen: set[str] = set()
        while True:
            body = await self._get(cursor)
            tracks += [_to_track(item) for item in body["items"]]
            next_cursor = body.get("next_cursor")
            if not next_cursor:
                return tracks
            if str(next_cursor) in seen:
                # A repeated cursor would fetch the same page forever; a run must fail.
                raise HyperfixError("hyperfix repeated a cursor; refusing to loop")
            seen.add(str(next_cursor))
            cursor = str(next_cursor)


def _to_track(item: dict) -> Track:
    """One feed row as the engine's track; the recording MBID is the identity."""
    artist = str(item.get("artist") or "").strip()
    title = str(item.get("title") or "").strip()
    mbid = item.get("recording_mbid") or None
    if not artist or not title:
        raise HyperfixError(f"feed row {item!r} has no artist or title")
    return Track(
        artist=artist,
        title=title,
        recording_mbid=str(mbid) if mbid else None,
        rating=_rating(item.get("rating")),
        external_id=str(mbid) if mbid else None,
    )


def _passes(rating: Optional[float], threshold: float, comparison: str) -> bool:
    """Whether a rating is on this relay's side of the threshold.

    A love relay reads at or above it, a hate relay at or below — the same rule the Plex
    source applies, so a two-way setup reads back exactly what it wrote.
    """
    if rating is None:
        return False
    return rating >= threshold if comparison == "gte" else 0 < rating <= threshold


@dataclass(frozen=True, slots=True)
class HyperfixSource:
    """hyperfix's rated recordings, filtered to one direction of the relay."""

    client: HyperfixClient
    threshold: float = DEFAULT_LOVE_RATING
    comparison: str = "gte"
    service: str = "hyperfix"

    async def fetch(self) -> list[Track]:
        return [
            track
            for track in await self.client.ratings()
            if _passes(track.rating, self.threshold, self.comparison)
        ]


def _hundreds(rating: float) -> int:
    """This engine's 0-10 rating as hyperfix's 0-100, never 0 (which reads as unrated)."""
    return max(1, min(100, round(rating * 10)))


class HyperfixTarget(MbidTarget):
    """Writes a rating to one recording hyperfix holds, unless it already holds it.

    "Already holds it" is the Plex rule on hyperfix's scale: a love relay wants a
    rating at least the threshold, a hate relay one at most (and rated at all), so
    the user's own 95 under a love relay is left alone and a withdrawn love is
    written back rather than bumped.
    """

    KIND = "hyperfix"

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        client: HyperfixClient,
        mappings: MappingStore,
        resolve: Resolver,
        rating: float = DEFAULT_LOVE_RATING,
        direction: str = "love",
        cache: Optional[MbidCache] = None,
        *,
        service: str = KIND,
    ):
        super().__init__(mappings, resolve, cache, service=service)
        self._client = client
        self._rating = rating
        self._direction = direction
        self._current: Optional[dict[str, int]] = None

    async def current(self) -> dict[str, int]:
        """``{recording_mbid: rating}`` for everything hyperfix holds, once per run.

        One fetch of the whole feed, like the source uses: the alternative is a
        request per track, and this run already has the answer in hand. The values
        are the feed's own 0-100, so a ``prior`` of 0 restores a 0.
        """
        if self._current is None:
            self._current = await self._client.hundredths()
        return self._current

    def holds(self, current: Optional[int]) -> bool:
        """Whether a recording rated *current* already holds this target's intent."""
        if current is None:
            return False
        rating = _rating(current)
        if self._direction == "hate":
            return rating <= self._rating
        return rating >= self._rating

    async def plan(self, track: Track) -> WritePlan:
        resolution = await self.resolution(track)
        mbid = resolution.mbid
        if not mbid:
            return self.unmatched(track, resolution)

        current = (await self.current()).get(mbid)
        rating = self._rating
        wrote = _hundreds(rating)
        client = self._client

        async def commit() -> None:
            await client.write_rating(mbid, wrote)
            # After the write, never before: a dry run does not reach here, and a
            # failed write must not teach the cache an answer no run kept.
            await self.remembered(track, mbid)

        return WritePlan(
            track,
            self.service,
            recording_mbid=mbid,
            intent=Intent(IntentKind.RATING, rating),
            target_key=mbid,
            mapped=await self.has_mapping(track),
            existing=Existing(
                holds=self.holds(current),
                prior=None if current is None else str(current),
                occupied=current is not None,
            ),
            commit=commit,
        )

    async def undo(self, record: SyncedTrack) -> None:
        """Put back the rating the recording held before us, or clear it.

        A recording whose rating is no longer the one we wrote was changed by the
        user after us, so it is theirs and is left alone — the same promise the
        Plex target keeps.
        """
        mbid = self._recorded_mbid(record)
        current = await self._client.recording_rating(mbid)
        prior = int(record.prior) if record.prior else None
        if record.rating is not None and current != _hundreds(record.rating):
            if prior is not None and current == prior:
                return  # never written (it already held it); nothing to undo
            logger.info(
                "hyperfix recording %s is rated %s, not the %s we wrote; leaving it",
                mbid,
                current,
                _hundreds(record.rating),
            )
            return
        await self._client.write_rating(mbid, prior)
