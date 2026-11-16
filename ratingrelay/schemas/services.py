"""Which account a request is about, and the list of accounts we hold.

``account`` is the slug after the colon in an account's ref (``lastfm:old``);
leaving it out names the account that already existed, which is what every
request sent before accounts existed means.
"""

from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from ratingrelay.engine.refs import check_account

ServiceKind = Literal["plex", "lastfm", "listenbrainz"]


class AccountFields(BaseModel):
    """The account part of a credential request."""

    account: Optional[str] = None
    # Only used when the account is created: the name the services page shows.
    # A ref is immutable, so this is the one thing about it that can change.
    label: Optional[str] = None

    @field_validator("account")
    @classmethod
    def _valid_account(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return check_account(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class ServiceAccountItem(BaseModel):
    """One connected account, for the services page and the flow builder.

    ``configured`` means the stored values are complete, not that the service
    answered: verifying a token costs a network call, and this list is what the
    UI reads on every page load. Each service's own ``/auth/status`` does the
    verified check for the account it is asked about.
    """

    id: str
    service: ServiceKind
    label: str
    # The account the bare kind refs, and the one settings may override.
    primary: bool
    configured: bool
    username: Optional[str] = None
    server_name: Optional[str] = None


class ServicesResponse(BaseModel):
    accounts: list[ServiceAccountItem]
