from typing import Literal

from pydantic import BaseModel

from ratingrelay.schemas.services import AccountFields


class ListenBrainzAuthRequest(AccountFields):
    username: str
    token: str


class ListenBrainzStatusResponse(BaseModel):
    status: Literal["connected", "needs-auth"]
    username: str | None = None
