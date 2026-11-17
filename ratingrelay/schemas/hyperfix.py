"""Connecting a hyperfix instance: an address and an API key, no OAuth."""

from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator

from ratingrelay.engine.refs import check_account


class HyperfixAuthRequest(BaseModel):
    """The instance's base URL and a key that may read its ratings feed."""

    url: HttpUrl
    token: str
    account: Optional[str] = None
    label: Optional[str] = None

    @field_validator("token")
    @classmethod
    def _token(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("an API key is required")
        return value

    @field_validator("account")
    @classmethod
    def _account(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return check_account(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class HyperfixStatusResponse(BaseModel):
    status: str
    url: Optional[str] = None
