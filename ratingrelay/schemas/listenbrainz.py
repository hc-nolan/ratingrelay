from pydantic import BaseModel


class ListenBrainzAuthRequest(BaseModel):
    username: str
    token: str


class ListenBrainzStatusResponse(BaseModel):
    status: str  # "connected" | "needs-auth"
    username: str | None = None
