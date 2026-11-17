"""Which account of which service, as one ref string.

Identity here is a *ref*: ``plex``, ``lastfm`` or ``listenbrainz`` for the account
that already exists, and ``lastfm:old`` for a second one. The bare kind stays the
ref of the first account **permanently**, which is what lets every stored sync
record, mapping, unmatched row and ``relays.toml`` keep its meaning when a
second account is added: they already hold the bare kind.

The ref is what the sync store and the mapping store key on, so it must be
produced here and never assembled at a call site. A ref that names a different
account than the run intends is a silent cross-account reconcile — the mass-undo
guard would be the only thing between that and a wipe.

No framework imports: the CLI, the API and the engine all use this module.
"""

from dataclasses import dataclass
from typing import Optional
import re

# The services a relay can name, as kinds. A ref adds an account to one of these.
KNOWN_SERVICES = ("plex", "lastfm", "listenbrainz", "hyperfix")

# Kinds that hold the ratings rather than receive them. Hyperfix pushes its own album
# and track ratings to Plex, so a relay writing back into it would fight that, and
# pairing it with Plex as a target would leave two reconcilers on one field (HF-182).
SOURCE_ONLY_SERVICES = ("hyperfix",)


def is_target_kind(kind: str) -> bool:
    """Whether an account of this kind can be a relay's destination."""
    return kind in KNOWN_SERVICES and kind not in SOURCE_ONLY_SERVICES

SEPARATOR = ":"

# An account slug appears in a ref, which appears in a URL, a TOML string and a
# database primary key. Keeping it to lowercase words means none of those has to
# quote it. The *label* the user sees is free-form; this is its slug.
_ACCOUNT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_ACCOUNT_LENGTH = 32


def check_account(account: str) -> str:
    """Validate an account slug, or raise ``ValueError`` naming what is allowed."""
    if not _ACCOUNT_PATTERN.match(account):
        raise ValueError(
            f"invalid account name {account!r}: use lowercase letters, digits, "
            "'-' and '_', starting with a letter or digit"
        )
    if len(account) > MAX_ACCOUNT_LENGTH:
        raise ValueError(
            f"invalid account name {account!r}: at most {MAX_ACCOUNT_LENGTH} characters"
        )
    return account


@dataclass(frozen=True, slots=True)
class ServiceRef:
    """A kind, plus the account of it (``None`` for the account that was here first)."""

    kind: str
    account: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in KNOWN_SERVICES:
            raise ValueError(
                f"unknown service {self.kind!r} (one of {', '.join(KNOWN_SERVICES)})"
            )
        if self.account is not None:
            check_account(self.account)

    @property
    def ref(self) -> str:
        """``lastfm`` or ``lastfm:old`` — the string everything else keys on."""
        if self.account is None:
            return self.kind
        return f"{self.kind}{SEPARATOR}{self.account}"

    @property
    def is_primary(self) -> bool:
        """Whether this is the account a bare kind refs, and settings describe."""
        return self.account is None

    def __str__(self) -> str:
        return self.ref


def parse_ref(value: str) -> ServiceRef:
    """Read a ``kind`` or ``kind:account`` string.

    Strict on purpose: a typo in a cron file that silently named another account
    would reconcile against the wrong history.
    """
    if not isinstance(value, str):
        raise ValueError(f"a service ref is a string, not {type(value).__name__}")
    kind, separator, account = value.partition(SEPARATOR)
    if not separator:
        return ServiceRef(kind)
    if not account:
        raise ValueError(f"ref {value!r} names an account but does not say which")
    return ServiceRef(kind, account)


def account_ref(kind: str, account: Optional[str] = None) -> str:
    """The ref for one account, validating both halves."""
    return ServiceRef(kind, account).ref


def service_kind(ref: str) -> str:
    """The kind behind a ref, for the capabilities that belong to the service.

    Last.fm has no hate whatever account it is, and only Plex has servers,
    ratings and thresholds.
    """
    return parse_ref(ref).kind


def is_primary(ref: str) -> bool:
    return parse_ref(ref).is_primary


# The name the web app shows for an account that has not been given one. Shared
# with the migration that creates the first accounts, so an upgraded database and
# a fresh connect read the same.
_DEFAULT_LABELS = {
    "plex": "Plex",
    "lastfm": "Last.fm",
    "listenbrainz": "ListenBrainz",
    "hyperfix": "Hyperfix",
}


def default_label(kind: str) -> str:
    """The display name of the first account of *kind*."""
    return _DEFAULT_LABELS[ServiceRef(kind).kind]


def mapping_scopes(ref: str) -> tuple[str, ...]:
    """Where a target's mappings may live, most specific first.

    A mapping scoped to one account wins over one scoped to its kind, which wins
    over ``all``. Kind-wide rows predate a second account, and applying them to
    one is what makes "move to a new account" work without re-creating them.
    """
    parsed = parse_ref(ref)
    scopes = [parsed.ref]
    if parsed.kind not in scopes:
        scopes.append(parsed.kind)
    scopes.append("all")
    return tuple(scopes)
