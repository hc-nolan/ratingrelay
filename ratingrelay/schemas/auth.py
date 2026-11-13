"""Schemas for the login surface."""

from typing import Optional

from pydantic import BaseModel


class AuthStatusResponse(BaseModel):
    """Whether a session is needed, whether this browser has one, and whether
    it sent a session cookie at all.

    ``cookie`` is the difference between "you are not signed in" and "your
    browser is not keeping the cookie" — the two look identical from the page
    and have completely different fixes.
    """

    required: bool
    authenticated: bool
    username: Optional[str] = None
    cookie: bool = False


class LoginInitResponse(BaseModel):
    pin_id: int
    auth_url: str


class LoginPollResponse(BaseModel):
    status: str  # pending | ok
    username: Optional[str] = None
