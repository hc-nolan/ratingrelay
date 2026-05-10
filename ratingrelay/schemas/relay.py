from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class PlexFromConfig(BaseModel):
    service: Literal["plex"]
    rating_threshold: float = Field(default=8.0, ge=1.0, le=10.0)
    comparison: Literal["gte", "lte"] = "gte"


class LastFMFromConfig(BaseModel):
    service: Literal["lastfm"]


class ListenBrainzFromConfig(BaseModel):
    service: Literal["listenbrainz"]
    feedback_type: Literal["love", "hate"] = "love"


FromConfig = Annotated[
    Union[PlexFromConfig, LastFMFromConfig, ListenBrainzFromConfig],
    Field(discriminator="service"),
]


class PlexToConfig(BaseModel):
    service: Literal["plex"]
    rating: float = Field(default=10.0, ge=1.0, le=10.0)


class LastFMToConfig(BaseModel):
    service: Literal["lastfm"]


class ListenBrainzToConfig(BaseModel):
    service: Literal["listenbrainz"]
    feedback_type: Literal["love", "hate"] = "love"


ToConfig = Annotated[
    Union[PlexToConfig, LastFMToConfig, ListenBrainzToConfig],
    Field(discriminator="service"),
]


class RelayRequest(BaseModel):
    source: FromConfig
    targets: list[ToConfig]


class TargetResult(BaseModel):
    service: str
    processed: int = 0
    errors: int = 0


class RelayResponse(BaseModel):
    status: Literal["completed", "partial", "error"]
    tracks_fetched: int
    results: list[TargetResult]
