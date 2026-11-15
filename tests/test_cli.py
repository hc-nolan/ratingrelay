"""The headless CLI: flags, config files, exit codes and the report shapes.

Every service here is fake. What is being tested is the contract cron sees —
what runs, what is printed, and the exit code — plus the layering guard that
keeps the CLI off FastAPI and the request schemas.
"""

import ast
import asyncio
import json
import pathlib
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.jobs  # noqa: F401 — register tables
import ratingrelay.models.mappings  # noqa: F401
import ratingrelay.models.services  # noqa: F401
import ratingrelay.models.sync  # noqa: F401

from ratingrelay.cli import main as cli
from ratingrelay.cli.config import ConfigError, RelaySpec, load_relays
from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.types import Intent, IntentKind, Track

TRACKS = [
    Track(artist="Artist A", title="Song A", rating=9.0),
    Track(artist="Artist B", title="Song B", rating=10.0),
]


class FakeSource:
    service = "plex"

    def __init__(self, tracks=None, error=None):
        self._tracks = tracks if tracks is not None else TRACKS
        self._error = error
        self.fetched = False

    async def fetch(self):
        if self._error:
            raise self._error
        self.fetched = True
        return list(self._tracks)


class FakeTarget:
    """Loves what it is given; refuses the titles named in ``fail_on``."""

    def __init__(self, service="lastfm", fail_on=()):
        self.service = service
        self._fail_on = set(fail_on)
        self.writes = []

    async def plan(self, track: Track) -> WritePlan:
        async def commit() -> None:
            if track.title in self._fail_on:
                raise RuntimeError("the target said no")
            self.writes.append(track.key)

        return WritePlan(
            track=track,
            target=self.service,
            intent=Intent(IntentKind.LOVE),
            target_key=f"{track.artist}||{track.title}",
            commit=commit,
        )

    def undoable(self, record):  # pragma: no cover - no resets in the CLI
        return None

    async def undo(self, record):  # pragma: no cover - no resets in the CLI
        self.writes.remove(record.key)


@pytest.fixture
def session_factory(tmp_path):
    """A file-backed SQLite factory, so each CLI call can own its own loop."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cli.db")

    async def create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create())
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    asyncio.run(engine.dispose())


def run_cli(argv, session_factory, source=None, targets=None):
    """Run the CLI with fakes in place. Returns the exit code."""
    source_patch = patch(
        "ratingrelay.core.factory.source_for_service",
        AsyncMock(return_value=source or FakeSource()),
    )
    target_patch = patch(
        "ratingrelay.core.factory.target_for_service",
        AsyncMock(side_effect=list(targets) if targets else [FakeTarget()]),
    )
    with source_patch, target_patch:
        return cli.main(argv, session_factory=session_factory)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_relay_flags_build_the_spec():
    args = cli.build_parser().parse_args(
        ["relay", "--source", "plex", "--target", "lastfm", "--target", "listenbrainz"]
    )
    spec = cli.spec_from_args(args)

    assert spec.source == "plex"
    assert spec.targets == ("lastfm", "listenbrainz")
    assert spec.dry_run is False


def test_relay_defaults_to_plex_and_lastfm():
    spec = cli.spec_from_args(cli.build_parser().parse_args(["relay"]))

    assert (spec.source, spec.targets) == ("plex", ("lastfm",))


def test_dry_run_flag_reaches_the_spec():
    spec = cli.spec_from_args(cli.build_parser().parse_args(["relay", "--dry-run"]))

    assert spec.dry_run is True


def test_plex_options_reach_the_spec():
    args = cli.build_parser().parse_args(
        [
            "relay",
            "--rating-threshold",
            "9",
            "--comparison",
            "lte",
            "--plex-rating",
            "6",
            "--feedback-type",
            "hate",
        ]
    )
    spec = cli.spec_from_args(args)

    assert spec.rating_threshold == 9.0
    assert spec.comparison == "lte"
    assert spec.plex_rating == 6.0
    assert spec.feedback_type == "hate"


def test_an_unknown_service_is_a_parser_error():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["relay", "--target", "spotify"])


def test_a_relay_cannot_write_back_to_its_source():
    with pytest.raises(ConfigError, match="both the source and a target"):
        RelaySpec(source="lastfm", targets=("lastfm",))


def test_a_relay_needs_a_target():
    with pytest.raises(ConfigError, match="at least one target"):
        RelaySpec(source="plex", targets=())


def test_the_threshold_stays_in_range():
    with pytest.raises(ConfigError, match="rating_threshold"):
        RelaySpec(rating_threshold=11.0)


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------


def _write(tmp_path, text: str) -> pathlib.Path:
    path = tmp_path / "relays.toml"
    path.write_text(text)
    return path


def test_a_config_file_can_hold_several_relays(tmp_path):
    path = _write(
        tmp_path,
        """
[[relay]]
name = "nightly"
source = "plex"
targets = ["lastfm", "listenbrainz"]
rating_threshold = 9.0

[[relay]]
name = "reverse"
source = "listenbrainz"
targets = ["plex"]
dry_run = true
""",
    )

    specs = load_relays(path)

    assert [spec.name for spec in specs] == ["nightly", "reverse"]
    assert specs[0].targets == ("lastfm", "listenbrainz")
    assert specs[0].rating_threshold == 9.0
    assert specs[1].dry_run is True


def test_a_missing_name_defaults_to_default(tmp_path):
    path = _write(tmp_path, '[[relay]]\nsource = "plex"\ntargets = ["lastfm"]\n')

    assert load_relays(path)[0].name == "default"


def test_targets_may_be_a_string(tmp_path):
    path = _write(tmp_path, '[[relay]]\nsource = "plex"\ntargets = "lastfm"\n')

    assert load_relays(path)[0].targets == ("lastfm",)


def test_an_unknown_key_is_an_error(tmp_path):
    path = _write(
        tmp_path, '[[relay]]\nsource = "plex"\ntargets = ["lastfm"]\ntargts = []\n'
    )

    with pytest.raises(ConfigError, match="unknown key"):
        load_relays(path)


def test_duplicate_names_are_an_error(tmp_path):
    path = _write(
        tmp_path,
        """
[[relay]]
name = "same"
[[relay]]
name = "same"
""",
    )

    with pytest.raises(ConfigError, match="duplicate"):
        load_relays(path)


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(ConfigError, match="no config file"):
        load_relays(tmp_path / "nope.toml")


def test_a_file_without_relays_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match=r"no \[\[relay\]\]"):
        load_relays(_write(tmp_path, ""))


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def test_a_clean_run_exits_zero(capsys, session_factory):
    code = run_cli(["relay"], session_factory)

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "relay default: plex → lastfm" in out
    assert "fetched 2 tracks" in out
    assert "written 2" in out
    assert "result: completed" in out


def test_a_clean_run_writes_through_the_engine(session_factory):
    target = FakeTarget()

    run_cli(["relay"], session_factory, targets=[target])

    assert sorted(target.writes) == sorted(track.key for track in TRACKS)


def test_a_partial_run_exits_one(capsys, session_factory):
    code = run_cli(["relay"], session_factory, targets=[FakeTarget(fail_on=["Song B"])])

    out = capsys.readouterr().out
    assert code == cli.EXIT_PARTIAL
    assert "result: partial" in out
    assert "failed 1" in out


def test_a_failed_fetch_exits_two(capsys, session_factory):
    code = run_cli(
        ["relay"], session_factory, source=FakeSource(error=RuntimeError("plex is down"))
    )

    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILED
    assert "plex is down" in captured.err


def test_a_dry_run_writes_nothing_and_says_so(capsys, session_factory):
    target = FakeTarget()

    code = run_cli(["relay", "--dry-run"], session_factory, targets=[target])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert target.writes == []
    assert "dry run — nothing was written" in out


def test_the_json_report_is_one_document(capsys, session_factory):
    run_cli(["relay", "--json", "--target", "lastfm"], session_factory)

    document = json.loads(capsys.readouterr().out)

    assert document["relay"] == "default"
    assert document["source"] == "plex"
    assert document["targets"] == ["lastfm"]
    assert document["status"] == "completed"
    assert document["tracks_fetched"] == 2
    assert document["counts"] == [
        {
            "target": "lastfm",
            "written": 2,
            "reverted": 0,
            "skipped": 0,
            "unmatched": 0,
            "failed": 0,
        }
    ]
    assert document["unmatched"] == []
    assert document["failed"] == []
    assert "duration_seconds" in document


def test_the_json_report_names_a_failure(capsys, session_factory):
    code = run_cli(
        ["relay", "--json"],
        session_factory,
        source=FakeSource(error=RuntimeError("plex is down")),
    )

    document = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_FAILED
    assert document["status"] == "failed"
    assert "plex is down" in document["error"]


def test_every_target_gets_its_own_counts(capsys, session_factory):
    code = run_cli(
        ["relay", "--json", "--target", "lastfm", "--target", "listenbrainz"],
        session_factory,
        targets=[FakeTarget("lastfm"), FakeTarget("listenbrainz", fail_on=["Song A"])],
    )

    document = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_PARTIAL
    assert [row["target"] for row in document["counts"]] == ["lastfm", "listenbrainz"]
    assert document["counts"][1]["failed"] == 1
    assert document["failed"][0]["title"] == "Song A"


def test_verbose_reports_each_track_on_stderr(capsys, session_factory):
    run_cli(["relay", "--verbose"], session_factory)

    err = capsys.readouterr().err
    assert "written   lastfm" in err


def test_list_prints_the_config(capsys, tmp_path):
    path = _write(
        tmp_path,
        '[[relay]]\nname = "nightly"\nsource = "plex"\ntargets = ["lastfm"]\n',
    )

    code = cli.main(["list", "--config", str(path)])

    assert code == cli.EXIT_OK
    assert "nightly  plex → lastfm" in capsys.readouterr().out


def test_a_missing_config_exits_two(capsys, tmp_path):
    code = cli.main(["run", "--config", str(tmp_path / "nope.toml")])

    assert code == cli.EXIT_FAILED
    assert "no config file" in capsys.readouterr().err


def test_only_runs_the_named_relay(capsys, tmp_path, session_factory):
    path = _write(
        tmp_path,
        """
[[relay]]
name = "one"
source = "plex"
targets = ["lastfm"]

[[relay]]
name = "two"
source = "plex"
targets = ["lastfm"]
""",
    )

    code = run_cli(["run", "--config", str(path), "--only", "two"], session_factory)

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "relay two:" in out
    assert "relay one:" not in out


def test_only_with_an_unknown_name_exits_two(capsys, tmp_path, session_factory):
    path = _write(tmp_path, '[[relay]]\nname = "one"\n')

    code = run_cli(["run", "--config", str(path), "--only", "nope"], session_factory)

    assert code == cli.EXIT_FAILED
    assert "no relay named nope" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Layering guard — the CLI stays headless
# ---------------------------------------------------------------------------


CLI_FORBIDDEN = ("fastapi", "starlette", "uvicorn")


def test_the_cli_imports_no_web_framework_or_request_schemas():
    cli_dir = pathlib.Path("ratingrelay/cli")
    offenders = []
    for path in [cli_dir / "main.py", cli_dir / "config.py", cli_dir / "report.py"]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in CLI_FORBIDDEN or name.startswith(
                    ("ratingrelay.routes", "ratingrelay.schemas", "app")
                ):
                    offenders.append(f"{path}: {name}")
    assert offenders == []
