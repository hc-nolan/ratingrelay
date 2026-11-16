"""Relay definitions for unattended runs.

A ``relays.toml`` file names *which* service talks to *which*, and nothing else:
secrets stay in settings and the credential row, exactly as the web app reads
them. One ``[[relay]]`` table is one run.

    [[relay]]
    name = "nightly"
    source = "plex"
    targets = ["lastfm", "listenbrainz"]

Unknown keys are an error. A typo in a cron file that silently did nothing would
be worse than a crash.
"""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from ratingrelay.engine.refs import KNOWN_SERVICES, parse_ref

DEFAULT_CONFIG = "relays.toml"

# A source or target is an account *ref*: a service, optionally followed by
# ``:account`` for a second one (``lastfm:new``). The bare kind is the account
# that already exists, so a file written before accounts existed still means what
# it meant. See ``ratingrelay.engine.refs``.
FEEDBACK_TYPES = ("love", "hate")

_KNOWN_KEYS = {
    "name",
    "source",
    "targets",
    "dry_run",
    "feedback_type",
    "reapply_target_drift",
}

# Keys earlier v3 builds accepted. The Plex ratings are settings now, shared by
# every relay, so a file still naming them is told where they went instead of
# getting a bare "unknown key".
_MOVED_KEYS = {
    "rating_threshold": "set the love/hate thresholds in the web app or settings",
    "plex_rating": "a Plex target writes the shared love/hate thresholds",
    "comparison": 'use feedback_type = "love" or "hate"',
}


class ConfigError(ValueError):
    """A relay definition the CLI refuses to guess at."""


def _read_ref(name: str, side: str, value: str) -> str:
    """Validate one source or target as an account ref, or say why not."""
    try:
        return parse_ref(value).ref
    except ValueError as exc:
        raise ConfigError(
            f"{name}: {exc} (a {side} is one of "
            f"{', '.join(KNOWN_SERVICES)}, or one of those followed by "
            "':account' — e.g. lastfm:new)"
        ) from exc


@dataclass(frozen=True)
class RelaySpec:
    """One relay: fetch from ``source``, write the same state to every target.

    One field per key a ``relays.toml`` table may hold, deliberately flat: the
    spec is the config file's shape, and nesting it would only move the names.
    """
    name: str = "default"
    source: str = "plex"
    targets: tuple[str, ...] = ("lastfm",)
    dry_run: bool = False
    # The direction: which Plex/ListenBrainz ratings a source reads, and which
    # feedback a ListenBrainz target writes. The Plex ratings that mean love and
    # hate are the shared thresholds (web app, or pinned in settings).
    feedback_type: str = "love"
    # Write again what the target has since dropped, for this relay only. A
    # policy rather than a one-run override, so unlike ``allow_mass_undo`` it
    # may be set in the file (see ``engine.pipeline._drifted`` for what counts).
    reapply_target_drift: bool = False
    # Only ever set from the command line, never from relays.toml: a file key
    # would switch the guard off for every unattended run after this one.
    allow_mass_undo: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("a relay needs a name")
        _read_ref(self.name, "source", self.source)
        if not self.targets:
            raise ConfigError(f"{self.name}: a relay needs at least one target")
        for target in self.targets:
            _read_ref(self.name, "target", target)
            # Judged on the ref, not the kind: ``plex`` -> ``plex:attic`` is the
            # point of a second account, ``plex`` -> ``plex`` is one account
            # relaying into itself.
            if parse_ref(target).ref == parse_ref(self.source).ref:
                raise ConfigError(
                    f"{self.name}: {target!r} is both the source and a target"
                )
        if self.feedback_type not in FEEDBACK_TYPES:
            raise ConfigError(
                f"{self.name}: feedback_type must be one of {', '.join(FEEDBACK_TYPES)}"
            )
        # Lazy, like ``relay`` below: parsing a config must not need settings.
        from ratingrelay.core.factory import (  # pylint: disable=import-outside-toplevel
            unsupported_target,
        )

        for target in self.targets:
            refusal = unsupported_target(self.relay, target)
            if refusal:
                raise ConfigError(f"{self.name}: {refusal}")

    @property
    def relay(self) -> str:
        """Which relay's sync records this spec reconciles against."""
        # Lazy, like the CLI's other factory imports: reading a config file
        # should not need settings or a database.
        from ratingrelay.core.factory import (  # pylint: disable=import-outside-toplevel
            relay_identity,
        )

        return relay_identity(self.source, feedback_type=self.feedback_type)

    @property
    def summary(self) -> str:
        """``plex → lastfm, listenbrainz``, for logs and the human report."""
        return f"{self.source} → {', '.join(self.targets)}"


def _relay_from_table(table: dict[str, Any], position: int) -> RelaySpec:
    label = f"relay[{position}]"
    moved = sorted(set(table) & set(_MOVED_KEYS))
    if moved:
        raise ConfigError(
            f"{label}: {'; '.join(f'{key} is gone ({_MOVED_KEYS[key]})' for key in moved)}"
        )
    unknown = sorted(set(table) - _KNOWN_KEYS)
    if unknown:
        raise ConfigError(f"{label}: unknown key(s): {', '.join(unknown)}")

    name = table.get("name", "default")
    if not isinstance(name, str):
        raise ConfigError(f"{label}: name must be a string")
    label = name or label

    source = table.get("source", "plex")
    targets = table.get("targets", ["lastfm"])
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        raise ConfigError(f"{label}: targets must be a list of service names")

    return RelaySpec(
        name=label,
        source=source,
        targets=tuple(targets),
        dry_run=bool(table.get("dry_run", False)),
        feedback_type=table.get("feedback_type", "love"),
        reapply_target_drift=_a_bool(label, table, "reapply_target_drift"),
    )


def _a_bool(label: str, table: dict[str, Any], key: str) -> bool:
    """A flag that must be written as a TOML boolean, not a truthy string."""
    value = table.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{label}: {key} must be true or false")
    return value


def load_relays(path: str | Path = DEFAULT_CONFIG) -> tuple[RelaySpec, ...]:
    """Read every ``[[relay]]`` table in *path*, in file order."""
    config_path = Path(path)
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigError(f"no config file at {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {config_path}: {exc}") from exc

    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{config_path} is not valid TOML: {exc}") from exc

    tables = document.get("relay", [])
    unknown = sorted(set(document) - {"relay"})
    if unknown:
        raise ConfigError(f"{config_path}: unknown top-level key(s): {', '.join(unknown)}")
    if isinstance(tables, dict):
        tables = [tables]
    if not isinstance(tables, list) or not tables:
        raise ConfigError(f"{config_path}: no [[relay]] tables")

    specs = tuple(_relay_from_table(table, index) for index, table in enumerate(tables))
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ConfigError(f"{config_path}: duplicate relay name {spec.name!r}")
        seen.add(spec.name)
    _refuse_shared_records(config_path, specs)
    return specs


def _refuse_shared_records(config_path: Path, specs: tuple[RelaySpec, ...]) -> None:
    """Two writing relays that would reconcile against the same records.

    Sync records are kept per source, target and direction. Two relays that
    agree on all three but differ in anything else would each undo the tracks
    only the other one relays, every run. A dry run writes nothing,
    so it may share records with the relay it previews.
    """
    owners: dict[tuple[str, str, str], str] = {}
    for spec in specs:
        if spec.dry_run:
            continue
        for target in spec.targets:
            address = (spec.source, target, spec.relay)
            if address in owners:
                raise ConfigError(
                    f"{config_path}: relays {owners[address]!r} and {spec.name!r} both "
                    f"write {spec.relay}s from {spec.source} to {target}; each would "
                    "undo what only the other one relays. Merge them into one relay."
                )
            owners[address] = spec.name
