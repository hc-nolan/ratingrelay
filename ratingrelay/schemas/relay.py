from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from ratingrelay.engine.refs import check_account


class _AccountScoped(BaseModel):
    """A config that may name a second account of its service.

    ``account`` is the part after the colon in an account's ref
    (``lastfm:old`` — see ``engine.refs``). Leaving it out names the account that
    already existed, which is exactly what every config stored before accounts
    existed means, so old job rows and old clients keep working.
    """

    account: Optional[str] = None

    @field_validator("account")
    @classmethod
    def _valid_account(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return check_account(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class PlexFromConfig(_AccountScoped):
    """Plex as a source. The ratings that count as a love or a hate are the
    shared love/hate thresholds (settings or the UI), not per relay."""

    service: Literal["plex"]
    feedback_type: Literal["love", "hate"] = "love"

    @model_validator(mode="before")
    @classmethod
    def _legacy_comparison(cls, data: object) -> object:
        # A job stored before the thresholds moved to settings carries
        # ``comparison``. Extra keys are ignored, so without this an ``lte``
        # (hate) job would quietly come back as a love relay.
        if isinstance(data, dict) and "comparison" in data:
            data = dict(data)
            comparison = data.pop("comparison")
            data.pop("rating_threshold", None)
            data.setdefault("feedback_type", "hate" if comparison == "lte" else "love")
        return data


class LastFMFromConfig(_AccountScoped):
    service: Literal["lastfm"]


class ListenBrainzFromConfig(_AccountScoped):
    service: Literal["listenbrainz"]
    feedback_type: Literal["love", "hate"] = "love"


class ResetFromConfig(BaseModel):
    """Reset one account's target to the state it was in before this tool ran.

    By default that means undoing the writes we recorded for *that account* and
    leaving every other rating alone — including another account of the same
    service. ``all`` clears the whole service, which is destructive and never
    implied.
    """

    service: Literal["reset"] = "reset"
    target_service: Literal["plex", "lastfm", "listenbrainz"] = "plex"
    target_account: Optional[str] = None
    all: bool = False

    @field_validator("target_account")
    @classmethod
    def _valid_target_account(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return check_account(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


FromConfig = Annotated[
    Union[PlexFromConfig, LastFMFromConfig, ListenBrainzFromConfig, ResetFromConfig],
    Field(discriminator="service"),
]


class PlexToConfig(_AccountScoped):
    """Plex as a target. It writes the threshold for the relay's direction."""

    service: Literal["plex"]


class LastFMToConfig(_AccountScoped):
    service: Literal["lastfm"]


class ListenBrainzToConfig(_AccountScoped):
    """ListenBrainz as a target. It writes the relay's direction: a love relay
    loves, a hate relay hates. Jobs from older builds carry a ``feedback_type``
    here; it is ignored, so a target can never write the opposite of its source.
    """

    service: Literal["listenbrainz"]


ToConfig = Annotated[
    Union[PlexToConfig, LastFMToConfig, ListenBrainzToConfig],
    Field(discriminator="service"),
]


class RelayRequest(BaseModel):
    source: FromConfig
    targets: list[ToConfig]
    # A dry run fetches, matches and reconciles exactly like a real run and
    # reports what it would write; only the write calls are suppressed. It is
    # the same flag the job worker already carries.
    dry_run: bool = False
    # Write again what the target has since dropped. Off by default: a target's
    # own edits are left alone unless the relay asks for this.
    reapply_target_drift: bool = False


class TargetResult(BaseModel):
    service: str
    processed: int = 0
    errors: int = 0
    skipped: int = 0


class RelayResponse(BaseModel):
    status: Literal["completed", "partial", "error"]
    tracks_fetched: int
    results: list[TargetResult]
    dry_run: bool = False


source_adapter: TypeAdapter[FromConfig] = TypeAdapter(FromConfig)
targets_adapter: TypeAdapter[list[ToConfig]] = TypeAdapter(list[ToConfig])


class UnrevertableRecord(BaseModel):
    """A write we recorded but can no longer address, so a reset refuses it.

    v2 stored where it wrote to only for some targets, so an old row can say
    "we loved this track on Last.fm" without saying which name we loved. The
    row is reported rather than reconstructed: the key is normalised, and
    guessing the name would clear somebody else's love.

    ``artist``/``title`` are the normalised forms the key holds, which is all
    such a row ever recorded.
    """

    artist: str
    title: str
    target_service: str
    intent: str | None = None
    reason: Literal["unrecorded_target"] = "unrecorded_target"
    error: str


class ThresholdsResponse(BaseModel):
    """Returned by GET/PUT /api/relay/thresholds: the thresholds in force.

    A ``*_locked`` threshold is set in settings (env or ``.config``) and can't
    be changed from the UI.
    """

    love_threshold: float
    hate_threshold: float
    love_locked: bool = False
    hate_locked: bool = False


class ThresholdsUpdate(BaseModel):
    """Body of PUT /api/relay/thresholds. Plex scale, whole numbers: 1 is half
    a star, 10 is five. A field left out keeps its value."""

    love_threshold: Optional[int] = Field(default=None, ge=1, le=10)
    hate_threshold: Optional[int] = Field(default=None, ge=1, le=10)
