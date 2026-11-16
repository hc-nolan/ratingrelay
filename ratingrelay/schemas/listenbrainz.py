from typing import Literal

from pydantic import BaseModel


class ListenBrainzAuthRequest(BaseModel):
    username: str
    token: str


class ListenBrainzStatusResponse(BaseModel):
    status: Literal["connected", "needs-auth"]
    username: str | None = None
