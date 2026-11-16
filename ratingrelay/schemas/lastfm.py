from typing import Literal

from pydantic import BaseModel

from ratingrelay.schemas.services import AccountFields


class LastFMAuthRequest(AccountFields):
    api_key: str
    api_secret: str
    username: str
    password: str


class LastFMStatusResponse(BaseModel):
    status: Literal["connected", "needs-auth"]
    username: str | None = None
