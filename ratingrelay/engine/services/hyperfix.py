"""Hyperfix as a source of ratings (HF-182).

A Thin adapter over hyperfix's music module: ``GET /api/music/ratings`` returns **every
rated recording** with its recording MBID, artist, title and a 0-100 rating. Identity is
exact here — the recording MBID *is* the relay's identity — so nothing is resolved through
MusicBrainz for this source.

That feed is deliberately not a delta: this engine decides what to withdraw by absence from
the fetched set, so a partial answer would read as "the user withdrew these" and either
un-love the library or trip the mass-undo guard.

The rating scale maps cleanly: hyperfix's 80/20 love/hate are Plex's 8.0/2.0 divided by ten,
which are this tool's defaults, so the shared thresholds describe both ends. A hyperfix
``0`` becomes ``0.1``: this engine treats ``0.0`` as unrated, and a deliberate zero must
still fail a love threshold and pass a hate one.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from ratingrelay.engine.credentials import HyperfixCredentials
from ratingrelay.engine.types import DEFAULT_LOVE_RATING, Track

FEED_PATH = "/api/music/ratings"
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
    _owned: list[httpx.AsyncClient] = field(default_factory=list, compare=False, repr=False)

    async def _get(self, cursor: Optional[str]) -> dict:
        params: dict[str, Any] = {"limit": self.page_size}
        if cursor:
            params["cursor"] = cursor
        url = f"{self.credentials.base_url}{FEED_PATH}"
        headers = {"Authorization": f"Bearer {self.credentials.token}"}
        client = self.http or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise HyperfixError(f"hyperfix did not answer: {exc}") from exc
        finally:
            if self.http is None:
                await client.aclose()
        if response.status_code != 200:
            raise HyperfixError(f"hyperfix answered {response.status_code} for {FEED_PATH}")
        try:
            body = response.json()
        except ValueError as exc:
            raise HyperfixError("hyperfix answered with non-JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise HyperfixError("hyperfix answered with an unexpected shape")
        return body

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
