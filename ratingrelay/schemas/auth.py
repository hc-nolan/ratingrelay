"""Schemas for the login surface."""

from typing import Optional

from pydantic import BaseModel


class AuthStatusResponse(BaseModel):
    """Whether a session is needed, and whether this browser has one."""

    required: bool
    authenticated: bool
    username: Optional[str] = None


class LoginInitResponse(BaseModel):
    pin_id: int
    auth_url: str


class LoginPollResponse(BaseModel):
    status: str  # pending | ok
    username: Optional[str] = None
