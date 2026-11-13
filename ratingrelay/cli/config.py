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

DEFAULT_CONFIG = "relays.toml"

SOURCE_SERVICES = ("plex", "lastfm", "listenbrainz")
TARGET_SERVICES = ("plex", "lastfm", "listenbrainz")
COMPARISONS = ("gte", "lte")
FEEDBACK_TYPES = ("love", "hate")

_OPINIONATED_KEYS = ("rating_threshold", "comparison", "feedback_type", "plex_rating")

_KNOWN_KEYS = {
    "name",
    "source",
    "targets",
    "dry_run",
    "rating_threshold",
    "comparison",
    "feedback_type",
    "plex_rating",
}


class ConfigError(ValueError):
    """A relay definition the CLI refuses to guess at."""


@dataclass(frozen=True)
class RelaySpec:  # pylint: disable=too-many-instance-attributes
    """One relay: fetch from ``source``, write the same state to every target.

    One field per key a ``relays.toml`` table may hold, deliberately flat: the
    spec is the config file's shape, and nesting it would only move the names.
    """
    name: str = "default"
    source: str = "plex"
    targets: tuple[str, ...] = ("lastfm",)
    dry_run: bool = False
    # Plex as a source.
    rating_threshold: float = 8.0
    comparison: str = "gte"
    # Last.fm / ListenBrainz feedback, as a source or a target.
    feedback_type: str = "love"
    # Plex as a target.
    plex_rating: float = 10.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("a relay needs a name")
        if self.source not in SOURCE_SERVICES:
            raise ConfigError(
                f"{self.name}: unknown source {self.source!r} "
                f"(one of {', '.join(SOURCE_SERVICES)})"
            )
        if not self.targets:
            raise ConfigError(f"{self.name}: a relay needs at least one target")
        for target in self.targets:
            if target not in TARGET_SERVICES:
                raise ConfigError(
                    f"{self.name}: unknown target {target!r} "
                    f"(one of {', '.join(TARGET_SERVICES)})"
                )
            if target == self.source:
                raise ConfigError(
                    f"{self.name}: {target!r} is both the source and a target"
                )
        if self.comparison not in COMPARISONS:
            raise ConfigError(
                f"{self.name}: comparison must be one of {', '.join(COMPARISONS)}"
            )
        if self.feedback_type not in FEEDBACK_TYPES:
            raise ConfigError(
                f"{self.name}: feedback_type must be one of {', '.join(FEEDBACK_TYPES)}"
            )
        if not 1.0 <= self.rating_threshold <= 10.0:
            raise ConfigError(f"{self.name}: rating_threshold must be between 1 and 10")
        if not 1.0 <= self.plex_rating <= 10.0:
            raise ConfigError(f"{self.name}: plex_rating must be between 1 and 10")

    @property
    def summary(self) -> str:
        """``plex → lastfm, listenbrainz``, for logs and the human report."""
        return f"{self.source} → {', '.join(self.targets)}"


def _relay_from_table(table: dict[str, Any], position: int) -> RelaySpec:
    label = f"relay[{position}]"
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

    # Tuning that does not apply to the services this relay names is simply
    # unused; it still has to be the right type and in range, so a relay that
    # switches source later fails now rather than at 3am.
    options = {key: table[key] for key in _OPINIONATED_KEYS if key in table}

    return RelaySpec(
        name=label,
        source=source,
        targets=tuple(targets),
        dry_run=bool(table.get("dry_run", False)),
        **options,
    )


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
    return specs
