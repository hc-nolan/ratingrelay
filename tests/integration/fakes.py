"""Fakes for the three services, one level below the adapters.

Nothing here mirrors an HTTP API. Each fake stands in for the exact object an
adapter is handed by its library — a ``plexapi`` server, a ``pylast`` network
and track, a ``liblistenbrainz`` client — and keeps the state a *later* run
reads back (ratings, loves, feedback scores). Holding state is the point: it is
what turns "love, sync, unlove at the source, sync again" into one test instead
of an assertion about a mock's call log.

The rest of the stack is real. ``execute_relay`` is the worker's own entry
point, the adapters match and plan for real, the factory wires the stores, and
those stores are the SQL ones over in-memory SQLite. Only the network goes.
"""

from dataclasses import dataclass, field
from typing import Optional

import pylast
from plexapi.exceptions import BadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core import factory
from ratingrelay.core.relay import execute_relay
from ratingrelay.engine.services import lastfm as lastfm_service
from ratingrelay.engine.services import listenbrainz as listenbrainz_service
from ratingrelay.engine.services import plex as plex_service
from ratingrelay.engine.services.musicbrainz import Resolution
from ratingrelay.engine.types import OutcomeStatus, Reason, TrackOutcome
from ratingrelay.models.sync import SyncRecord


# ---------------------------------------------------------------------------
# Plex
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeGuid:
    """A ``plexapi`` media guid, as the adapter reads it (``guid.id``)."""

    id: str


@dataclass
class FakePlexTrack:
    """The attributes ``engine/services/plex.py`` reads off a plexapi track."""

    title: str
    grandparentTitle: str
    ratingKey: str
    userRating: Optional[float] = None
    guids: list = field(default_factory=lambda: [])

    def rate(self, rating=None):
        """``plexapi``'s rate(): a rating out of 10, or ``None`` to clear the copy.

        The library converts ``None`` to ``-1`` for the request and refuses any
        other out-of-range value, so this refuses them too: a fake more lenient
        than the library lets a bad call through that production would fail.
        """
        if rating is None:
            self.userRating = None
            return
        if not 0 <= rating <= 10:
            raise BadRequest("Rating must be between 0 to 10.")
        self.userRating = float(rating)


class FakeSection:
    """A Plex library section: filters are applied here, as the server would.

    ``userRating<<=`` deliberately includes unrated copies (their stored rating
    is 0). Plex does the same, and the adapter's local guard is what drops them
    — which means that guard is under test rather than bypassed.

    Both operators are **strict**, as a live server answers them: ``>>=8`` gives 9
    and 10, never 8. The adapter asks one step wider and applies the inclusive
    promise locally, so an inclusive fake here would hide that (RR-21).
    """

    def __init__(self, tracks: list[FakePlexTrack]):
        self.tracks = tracks
        self.calls: list[dict] = []

    def searchTracks(self, **filters):
        self.calls.append(filters)
        if not filters:
            return list(self.tracks)
        (operator, value), = filters.items()
        rating = float(value)
        if operator == "userRating>>=":
            return [track for track in self.tracks if (track.userRating or 0) > rating]
        if operator == "userRating<<=":
            return [track for track in self.tracks if (track.userRating or 0) < rating]
        # Not BadRequest: the adapter catches that and falls back to fetching the
        # whole library, which would hide a broken filter behind identical
        # results. An unexpected filter is a bug in the adapter, so say so.
        raise AssertionError(f"unexpected filter {operator}")


class FakePlex:
    """A Plex server and its library, as one run sees them.

    ``connect`` is what ``plexapi.PlexServer(url, token)`` is patched to, so the
    adapter's lazy connect lands here and is recorded rather than dialled.
    """

    def __init__(self, library: str = "Music"):
        self.section = FakeSection([])
        self.library = _FakeLibrary(self)
        self.requested_library: Optional[str] = None
        self.connections: list[tuple[str, str]] = []
        self._next_key = 1

    def connect(self, server_url: str, token: str) -> "FakePlex":
        """``plexapi.server.PlexServer(server_url, token)``."""
        self.connections.append((server_url, token))
        return self

    # -- seeding and reading the library -----------------------------------

    def add(
        self,
        title: str,
        artist: str,
        *,
        rating: Optional[float] = None,
        track_mbid: Optional[str] = None,
    ) -> FakePlexTrack:
        """Put one track in the library, as a file Plex has scanned."""
        guids = [FakeGuid(f"mbid://{track_mbid}")] if track_mbid else []
        track = FakePlexTrack(
            title=title,
            grandparentTitle=artist,
            ratingKey=str(self._next_key),
            userRating=rating,
            guids=guids,
        )
        self._next_key += 1
        self.section.tracks.append(track)
        return track

    def find(self, title: str) -> FakePlexTrack:
        for track in self.section.tracks:
            if track.title == title:
                return track
        raise LookupError(f"no Plex track titled {title!r}")

    def rating_of(self, title: str) -> Optional[float]:
        return self.find(title).userRating

    def rated_titles(self) -> list[str]:
        return [track.title for track in self.section.tracks if track.userRating]


class _FakeLibrary:
    """``server.library``: the only thing the adapter asks a server for."""

    def __init__(self, plex: FakePlex):
        self._plex = plex

    def section(self, name: str) -> FakeSection:
        self._plex.requested_library = name
        return self._plex.section


# ---------------------------------------------------------------------------
# Last.fm
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeTrackName:
    artist: str
    title: str


@dataclass(frozen=True)
class _FakeLovedItem:
    """A row of ``user.get_loved_tracks()``: ``item.track.artist`` and title."""

    track: _FakeTrackName


class _FakeLastFmTrack:
    """``pylast.Track``: an artist/title pair that can be loved in place."""

    def __init__(self, artist: str, title: str, account: "FakeLastFm"):
        self.artist = artist
        self.title = title
        self._account = account

    def love(self) -> None:
        self._account.love(self.artist, self.title, by_hand=False)

    def unlove(self) -> None:
        self._account.unlove(self.artist, self.title)


class _FakeNetwork:
    """``pylast.LastFMNetwork``: the account behind the signed session."""

    def __init__(self, account: "FakeLastFm"):
        self._account = account

    def get_authenticated_user(self) -> "_FakeNetwork":
        return self

    def get_loved_tracks(self, limit=None):
        """The loved set; ``limit=None`` is "all", the only call the adapter makes."""
        return [
            _FakeLovedItem(_FakeTrackName(artist, title))
            for artist, title in self._account.loved
        ]


class FakeLastFm:
    """A Last.fm account: its loved tracks, and the objects that change them."""

    def __init__(self):
        self.loved: dict[tuple[str, str], None] = {}
        self.loves: list[tuple[str, str]] = []
        self.unloves: list[tuple[str, str]] = []
        self.sessions: list[dict] = []

    def network(self, **kwargs) -> _FakeNetwork:
        """``pylast.LastFMNetwork(**kwargs)``, recording with what it signed in."""
        self.sessions.append(kwargs)
        return _FakeNetwork(self)

    def track(self, artist: str, title: str, network=None, **kwargs) -> _FakeLastFmTrack:
        """``pylast.Track(artist, title, network)``."""
        return _FakeLastFmTrack(artist, title, self)

    # -- seeding and reading the account -----------------------------------

    def love(self, artist: str, title: str, *, by_hand: bool = True) -> None:
        """Love a track. ``by_hand`` is the user's own love, not one we wrote."""
        self.loved[(artist, title)] = None
        if not by_hand:
            self.loves.append((artist, title))

    def unlove(self, artist: str, title: str, *, by_hand: bool = False) -> None:
        self.loved.pop((artist, title), None)
        if not by_hand:
            self.unloves.append((artist, title))

    def is_loved(self, artist: str, title: str) -> bool:
        return (artist, title) in self.loved


# ---------------------------------------------------------------------------
# ListenBrainz
# ---------------------------------------------------------------------------


class _FakeListenBrainzClient:
    """``liblistenbrainz.ListenBrainz``: feedback paged and submitted as it is."""

    def __init__(self, account: "FakeListenBrainz"):
        self._account = account
        self.token: Optional[str] = None

    def set_auth_token(self, token: str) -> None:
        self.token = token

    def get_user_feedback(self, username, score, metadata, count=100, offset=0):
        account = self._account
        account.page_requests.append((count, offset))
        mbids = [mbid for mbid, held in account.scores.items() if held == score]
        page = mbids[offset : offset + count]
        # ``metadata`` is what asks the API for artist/title alongside the MBID;
        # without it the adapter's tracks would carry no names at all.
        return {"feedback": [account.item(mbid, metadata) for mbid in page]}

    def submit_user_feedback(self, feedback: int, recording_mbid: str) -> None:
        self._account.submit(feedback, recording_mbid)


class FakeListenBrainz:
    """A ListenBrainz account: feedback per recording, keyed by MBID."""

    def __init__(self):
        self.scores: dict[str, int] = {}
        self.names: dict[str, tuple[str, str]] = {}
        self.submissions: list[tuple[int, str]] = []
        self.page_requests: list[tuple[int, int]] = []

    def client(self) -> _FakeListenBrainzClient:
        """``ListenBrainz()`` — a client bound to this account."""
        return _FakeListenBrainzClient(self)

    # -- seeding and reading the account -----------------------------------

    def set_feedback(
        self, recording_mbid: str, artist: str, title: str, score: int
    ) -> None:
        self.names[recording_mbid] = (artist, title)
        self.scores[recording_mbid] = score

    def love(self, recording_mbid: str, artist: str, title: str) -> None:
        self.set_feedback(recording_mbid, artist, title, 1)

    def hate(self, recording_mbid: str, artist: str, title: str) -> None:
        self.set_feedback(recording_mbid, artist, title, -1)

    def score(self, recording_mbid: str) -> Optional[int]:
        return self.scores.get(recording_mbid)

    def submitted(self, recording_mbid: str) -> list[int]:
        return [score for score, mbid in self.submissions if mbid == recording_mbid]

    def item(self, recording_mbid: str, metadata: bool = True) -> dict:
        item = {"recording_mbid": recording_mbid}
        if metadata:
            artist, title = self.names.get(recording_mbid, ("", ""))
            item["track_metadata"] = {"artist_name": artist, "track_name": title}
        return item

    def submit(self, feedback: int, recording_mbid: str) -> None:
        """A submission: ``0`` withdraws feedback, as ListenBrainz treats it."""
        self.submissions.append((feedback, recording_mbid))
        if feedback == 0:
            self.scores.pop(recording_mbid, None)
        else:
            self.scores[recording_mbid] = feedback


# ---------------------------------------------------------------------------
# MusicBrainz
# ---------------------------------------------------------------------------


class FakeMusicBrainz:
    """The recording lookup: a track MBID first, then exact artist + title.

    Mirrors the real ``_lookup``: the ``tid:`` shortcut is tried before the
    name search, and the name search only accepts the exact rung — MusicBrainz
    already ranks its own results fuzzily, so taking the first one would turn a
    guess into a love on the wrong recording. Proposals are not modelled; a
    miss is an empty :class:`Resolution` here.
    """

    def __init__(self):
        self.recordings: dict[tuple[str, str], str] = {}
        self.by_track_mbid: dict[str, str] = {}
        self.lookups: list[tuple[Optional[str], str, str]] = []

    def add(
        self,
        artist: str,
        title: str,
        recording_mbid: str,
        *,
        track_mbid: Optional[str] = None,
    ) -> None:
        self.recordings[(artist, title)] = recording_mbid
        if track_mbid:
            self.by_track_mbid[track_mbid] = recording_mbid

    async def __call__(
        self, track_mbid: Optional[str], artist: str, title: str
    ) -> Resolution:
        self.lookups.append((track_mbid, artist, title))
        if track_mbid and track_mbid in self.by_track_mbid:
            return Resolution(self.by_track_mbid[track_mbid])
        mbid = self.recordings.get((artist, title))
        return Resolution(mbid) if mbid else Resolution()


# ---------------------------------------------------------------------------
# The world, and one run of it
# ---------------------------------------------------------------------------


@dataclass
class RelayRun:
    """One ``execute_relay`` call: its response, and every typed outcome."""

    response: dict
    outcomes: list[TrackOutcome]

    @property
    def results(self) -> dict[str, dict]:
        """``{service: {"ok", "err", "skipped"}}`` — the row, without its own key."""
        return {
            row["service"]: {
                name: value for name, value in row.items() if name != "service"
            }
            for row in self.response["results"]
        }

    @property
    def tracks_fetched(self) -> int:
        return self.response["tracks_fetched"]

    def for_target(self, service: str) -> list[TrackOutcome]:
        return [outcome for outcome in self.outcomes if outcome.target == service]

    def decisions(self, service: Optional[str] = None) -> list[tuple[OutcomeStatus, Reason]]:
        """``[(status, reason), …]`` — what the run decided, per track."""
        outcomes = self.outcomes if service is None else self.for_target(service)
        return [(outcome.status, outcome.reason) for outcome in outcomes]


@dataclass
class World:
    """Three service accounts, the lookup, and the database a run writes to."""

    session: AsyncSession
    plex: FakePlex
    lastfm: FakeLastFm
    listenbrainz: FakeListenBrainz
    brainz: FakeMusicBrainz

    async def relay(
        self,
        source,
        targets,
        *,
        dry_run: bool = False,
        allow_mass_undo: bool = False,
        reapply_target_drift: bool = False,
    ) -> RelayRun:
        """Relay through the worker's entry point, collecting typed outcomes."""
        outcomes: list[TrackOutcome] = []

        async def on_track(_track, _target, _ok, _error, _skipped, outcome):
            # The callback the worker journals from: the typed outcome, not the
            # strings, which is why the assertions can be about decisions.
            if outcome is not None:
                outcomes.append(outcome)

        response = await execute_relay(
            self.session,
            source,
            targets,
            on_track=on_track,
            dry_run=dry_run,
            allow_mass_undo=allow_mass_undo,
            reapply_drift=reapply_target_drift,
        )
        return RelayRun(response=response, outcomes=outcomes)


def install(monkeypatch, world: World) -> None:
    """Point every adapter's library object at this world's fakes, and nothing else."""
    monkeypatch.setattr(plex_service, "PlexServer", world.plex.connect)
    monkeypatch.setattr(pylast, "LastFMNetwork", world.lastfm.network)
    monkeypatch.setattr(pylast, "Track", world.lastfm.track)
    monkeypatch.setattr(
        listenbrainz_service, "ListenBrainz", world.listenbrainz.client
    )
    monkeypatch.setattr(factory, "resolve_recording_mbid", world.brainz)
    # The 0.3s pacing is a rate-limit behaviour, not sync semantics; sleeping
    # through it in every Last.fm test would only make the suite slower.
    monkeypatch.setattr(lastfm_service, "WRITE_DELAY_SECONDS", 0)


# ---------------------------------------------------------------------------
# The database a run wrote to
# ---------------------------------------------------------------------------


async def synced(
    session: AsyncSession,
    *,
    source: Optional[str] = None,
    target: Optional[str] = None,
) -> list[SyncRecord]:
    """The sync records a run left behind, optionally for one pair."""
    statement = select(SyncRecord)
    if source is not None:
        statement = statement.where(SyncRecord.source_service == source)
    if target is not None:
        statement = statement.where(SyncRecord.target_service == target)
    result = await session.execute(statement)
    return list(result.scalars().all())
