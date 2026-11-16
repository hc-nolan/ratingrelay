from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, model_validator


class PlexFromConfig(BaseModel):
    """Plex as a source. The ratings that count as a love or a hate are the
    ``love_threshold``/``hate_threshold`` settings, not per relay."""

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


class LastFMFromConfig(BaseModel):
    service: Literal["lastfm"]


class ListenBrainzFromConfig(BaseModel):
    service: Literal["listenbrainz"]
    feedback_type: Literal["love", "hate"] = "love"


class ResetFromConfig(BaseModel):
    """Reset the target to the state it was in before this tool ran.

    By default that means undoing the writes we recorded for the target and
    leaving every other rating alone. ``all`` clears the whole service, which is
    destructive and never implied.
    """

    service: Literal["reset"] = "reset"
    target_service: Literal["plex", "lastfm", "listenbrainz"] = "plex"
    all: bool = False


FromConfig = Annotated[
    Union[PlexFromConfig, LastFMFromConfig, ListenBrainzFromConfig, ResetFromConfig],
    Field(discriminator="service"),
]


class PlexToConfig(BaseModel):
    """Plex as a target. It writes the threshold for the relay's direction."""

    service: Literal["plex"]


class LastFMToConfig(BaseModel):
    service: Literal["lastfm"]


class ListenBrainzToConfig(BaseModel):
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
    """Returned by GET /api/relay/thresholds. Read-only: set in settings."""

    love_threshold: float
    hate_threshold: float
