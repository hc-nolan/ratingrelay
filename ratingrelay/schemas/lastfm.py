from pydantic import BaseModel


class LastFMAuthRequest(BaseModel):
    api_key: str
    api_secret: str
    username: str
    password: str


class LastFMStatusResponse(BaseModel):
    status: str  # "connected" | "needs-auth"
    username: str | None = None
