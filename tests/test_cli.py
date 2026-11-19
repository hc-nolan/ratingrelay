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


def test_allow_mass_undo_is_off_unless_asked_for(tmp_path):
    relay = cli.build_parser().parse_args(["relay", "--allow-mass-undo"])
    assert cli.spec_from_args(relay).allow_mass_undo is True
    assert cli.spec_from_args(cli.build_parser().parse_args(["relay"])).allow_mass_undo is False

    specs = load_relays(_write(tmp_path, '[[relay]]\nname = "one"\n'))
    assert [s.allow_mass_undo for s in cli._select(specs, [], False)] == [False]  # pylint: disable=protected-access
    assert [s.allow_mass_undo for s in cli._select(specs, [], False, True)] == [True]  # pylint: disable=protected-access


def test_a_config_file_cannot_switch_the_mass_undo_guard_off(tmp_path):
    path = _write(tmp_path, '[[relay]]\nallow_mass_undo = true\n')

    with pytest.raises(ConfigError, match="unknown key"):
        load_relays(path)


def test_the_drift_option_comes_from_the_file_or_the_flag(tmp_path):
    on = load_relays(_write(tmp_path, '[[relay]]\nreapply_target_drift = true\n'))
    assert [spec.reapply_target_drift for spec in on] == [True]

    flag = cli.build_parser().parse_args(["relay", "--reapply-target-drift"])
    assert cli.spec_from_args(flag).reapply_target_drift is True
    assert cli.spec_from_args(cli.build_parser().parse_args(["relay"])).reapply_target_drift is False


def test_the_flag_turns_the_option_on_for_every_relay_in_a_run(tmp_path):
    path = _write(
        tmp_path,
        '[[relay]]\nname = "one"\n\n[[relay]]\nname = "two"\nsource = "lastfm"\ntargets = ["plex"]\n',
    )
    specs = load_relays(path)

    assert [s.reapply_target_drift for s in cli._select(specs, [], False)] == [False, False]  # pylint: disable=protected-access
    assert [s.reapply_target_drift for s in cli._select(specs, [], False, reapply_target_drift=True)] == [True, True]  # pylint: disable=protected-access


def test_a_non_boolean_drift_option_is_an_error(tmp_path):
    path = _write(tmp_path, '[[relay]]\nreapply_target_drift = "yes"\n')

    with pytest.raises(ConfigError, match="must be true or false"):
        load_relays(path)


def test_the_direction_reaches_the_spec():
    args = cli.build_parser().parse_args(
        ["relay", "--feedback-type", "hate", "--target", "listenbrainz"]
    )
    spec = cli.spec_from_args(args)

    assert spec.feedback_type == "hate"
    assert spec.relay == "hate"


def test_the_rating_direction_reaches_the_spec():
    """A star relay is its own identity, so its records cannot collide."""
    args = cli.build_parser().parse_args(
        ["relay", "--feedback-type", "rating", "--target", "hyperfix"]
    )
    spec = cli.spec_from_args(args)

    assert spec.feedback_type == "rating"
    assert spec.relay == "rating"


@pytest.mark.parametrize("flag", ["--rating-threshold", "--comparison", "--plex-rating"])
def test_the_per_relay_plex_ratings_are_gone(flag):
    """The Plex ratings are the shared love/hate thresholds now."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["relay", flag, "8"])


def test_an_unknown_service_is_a_parser_error():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["relay", "--target", "spotify"])


def test_a_relay_cannot_write_back_to_its_source():
    with pytest.raises(ConfigError, match="both the source and a target"):
        RelaySpec(source="lastfm", targets=("lastfm",))


# ---------------------------------------------------------------------------
# A second account of a service
# ---------------------------------------------------------------------------


def test_a_relay_can_name_a_second_account():
    spec = RelaySpec(source="lastfm", targets=("lastfm:new",))
    assert spec.targets == ("lastfm:new",)
    assert spec.summary == "lastfm → lastfm:new"
    # The source reads the same direction either way; the pair of refs keeps the
    # two accounts' records apart.
    assert spec.relay == "love"


def test_the_same_account_cannot_be_both_ends():
    """``lastfm`` -> ``lastfm:new`` is a migration; ``lastfm`` -> ``lastfm`` is not."""
    with pytest.raises(ConfigError, match="both the source and a target"):
        RelaySpec(source="lastfm:old", targets=("lastfm:old",))
    with pytest.raises(ConfigError, match="both the source and a target"):
        RelaySpec(source="plex", targets=("plex",))


def test_a_plex_relay_between_two_servers_is_allowed():
    spec = RelaySpec(source="plex", targets=("plex:attic",))
    assert spec.targets == ("plex:attic",)


def test_a_hate_relay_cannot_target_a_second_lastfm_account():
    with pytest.raises(ConfigError, match="no hate"):
        RelaySpec(source="plex", targets=("lastfm:new",), feedback_type="hate")


def test_an_unreadable_account_name_says_what_is_allowed():
    with pytest.raises(ConfigError, match="lastfm:new"):
        RelaySpec(source="plex", targets=("lastfm:New Account",))


def test_the_flags_take_a_service_and_an_account():
    args = cli.build_parser().parse_args(
        ["relay", "--source", "lastfm", "--target", "lastfm:new"]
    )
    assert (args.source, args.targets) == ("lastfm", ["lastfm:new"])


def test_a_flag_with_an_unreadable_ref_is_a_parser_error():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["relay", "--target", "lastfm:"])


def test_the_config_file_takes_a_second_account(tmp_path):
    path = tmp_path / "relays.toml"
    path.write_text(
        '[[relay]]\nname = "move"\nsource = "lastfm"\ntargets = ["lastfm:new"]\n'
    )
    (spec,) = load_relays(path)
    assert (spec.source, spec.targets) == ("lastfm", ("lastfm:new",))


def test_a_relay_needs_a_target():
    with pytest.raises(ConfigError, match="at least one target"):
        RelaySpec(source="plex", targets=())


def test_the_direction_is_love_hate_or_rating():
    with pytest.raises(ConfigError, match="feedback_type"):
        RelaySpec(feedback_type="meh")

    assert RelaySpec(source="plex", targets=("hyperfix",), feedback_type="rating").relay == "rating"


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
targets = ["listenbrainz"]
feedback_type = "hate"

[[relay]]
name = "reverse"
source = "listenbrainz"
targets = ["plex"]
dry_run = true
""",
    )

    specs = load_relays(path)

    assert [spec.name for spec in specs] == ["nightly", "reverse"]
    assert specs[0].targets == ("listenbrainz",)
    assert specs[0].feedback_type == "hate"
    assert specs[1].dry_run is True


def test_a_missing_name_defaults_to_default(tmp_path):
    path = _write(tmp_path, '[[relay]]\nsource = "plex"\ntargets = ["lastfm"]\n')

    assert load_relays(path)[0].name == "default"


def test_targets_may_be_a_string(tmp_path):
    path = _write(tmp_path, '[[relay]]\nsource = "plex"\ntargets = "lastfm"\n')

    assert load_relays(path)[0].targets == ("lastfm",)


@pytest.mark.parametrize(
    "line, hint",
    [
        ("rating_threshold = 9.0", "love/hate thresholds"),
        ("plex_rating = 8.0", "love/hate thresholds"),
        ('comparison = "lte"', "feedback_type"),
    ],
)
def test_a_removed_plex_key_says_where_it_went(tmp_path, line, hint):
    path = _write(tmp_path, f'[[relay]]\nsource = "plex"\n{line}\n')

    with pytest.raises(ConfigError, match=f"is gone.*{hint}"):
        load_relays(path)


def test_an_unknown_key_is_an_error(tmp_path):
    path = _write(
        tmp_path, '[[relay]]\nsource = "plex"\ntargets = ["lastfm"]\ntargts = []\n'
    )

    with pytest.raises(ConfigError, match="unknown key"):
        load_relays(path)


def test_two_relays_writing_the_same_records_are_an_error(tmp_path):
    """Same source, target and direction: each would undo what only the other passes."""
    path = _write(
        tmp_path,
        """
[[relay]]
name = "strict"
targets = ["lastfm"]
[[relay]]
name = "loose"
targets = ["listenbrainz", "lastfm"]
""",
    )

    with pytest.raises(ConfigError, match="'strict' and 'loose' both write loves"):
        load_relays(path)


def test_relays_may_share_a_pair_in_opposite_directions_or_as_a_dry_run(tmp_path):
    path = _write(
        tmp_path,
        """
[[relay]]
name = "loves"
targets = ["listenbrainz"]
[[relay]]
name = "hates"
feedback_type = "hate"
targets = ["listenbrainz"]
[[relay]]
name = "preview"
targets = ["listenbrainz"]
dry_run = true
""",
    )

    assert [spec.relay for spec in load_relays(path)] == ["love", "hate", "love"]


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
targets = ["listenbrainz"]
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


def test_the_report_says_why_a_mass_undo_failed_and_how_to_allow_it():
    from ratingrelay.cli import report as cli_report  # pylint: disable=import-outside-toplevel
    from ratingrelay.engine.types import (  # pylint: disable=import-outside-toplevel
        OutcomeStatus,
        Reason,
        RunReport,
        TargetReport,
        TrackOutcome,
    )

    outcomes = tuple(
        TrackOutcome(
            Track("A", f"S{n}"),
            "lastfm",
            OutcomeStatus.FAILED,
            Reason.MASS_UNDO_REFUSED,
            "would undo 12 of 12 recorded writes",
        )
        for n in range(12)
    )
    run = RunReport(
        source="plex",
        tracks_fetched=0,
        targets=(TargetReport.from_outcomes("lastfm", outcomes),),
        outcomes=outcomes,
    )

    text = cli_report.human(RelaySpec(name="nightly"), run, 1.0)

    assert text.count("refused on lastfm") == 1
    assert "would undo 12 of 12 recorded writes (pass --allow-mass-undo)" in text


@pytest.mark.parametrize("source", ["plex", "listenbrainz"])
def test_a_hate_relay_to_lastfm_is_refused(source):
    with pytest.raises(ConfigError, match="hate relay"):
        RelaySpec(source=source, targets=("lastfm",), feedback_type="hate")


def test_a_hate_relay_to_lastfm_is_refused_from_flags():
    args = cli.build_parser().parse_args(
        ["relay", "--feedback-type", "hate", "--target", "lastfm"]
    )
    with pytest.raises(ConfigError, match="hate relay"):
        cli.spec_from_args(args)


# ---------------------------------------------------------------------------
# plex-login — the one browser trip a headless install needs, from the terminal
# ---------------------------------------------------------------------------


class _FakeConnection:
    def __init__(self, uri, local=True, relay=False, protocol="http"):
        self.uri = uri
        self.local = local
        self.relay = relay
        self.protocol = protocol


class _FakeResource:
    def __init__(self, name, *connections, product="Plex Media Server"):
        self.name = name
        self.product = product
        self.connections = list(connections)


class _FakeSignInAccount:
    """The account Plex hands back once the PIN is confirmed."""

    username = "chunned"

    def __init__(self, **_kwargs):
        pass

    def resources(self):
        return [
            _FakeResource(
                "MS01",
                _FakeConnection("http://relay.plex.invalid:443", local=False, relay=True),
                _FakeConnection("http://192.168.1.10:32400"),
            ),
            _FakeResource("Attic", _FakeConnection("http://attic.invalid:32400")),
            # A player is not a library: it must never be offered as a server.
            _FakeResource(
                "Chunned's Phone",
                _FakeConnection("http://phone.invalid:32500"),
                product="Plex for Android",
            ),
        ]


class _FakePinLogin:
    token = "plex-token"
    expired = False

    def __init__(self, confirmed=True, url="https://app.plex.tv/auth#?code=abc"):
        self._confirmed = confirmed
        self._url = url

    def oauthUrl(self, **_kwargs):
        return self._url

    def checkLogin(self):
        return self._confirmed


def _sign_in(monkeypatch, session_factory, argv, **settings):
    """Run ``plex-login`` against fakes, with nothing from the real environment."""
    from ratingrelay.settings import Settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        "ratingrelay.core.plex_auth.MyPlexPinLogin", lambda **_kwargs: _FakePinLogin()
    )
    monkeypatch.setattr("ratingrelay.core.plex_login.MyPlexAccount", _FakeSignInAccount)
    monkeypatch.setattr(
        "ratingrelay.settings.get_settings",
        lambda: Settings(_env_file=None, **settings),
    )
    return cli.main(["plex-login", *argv], session_factory=session_factory)


def _plex_row(session_factory):
    from ratingrelay.db.credentials import (  # pylint: disable=import-outside-toplevel
        get_credential,
    )

    async def fetch():
        async with session_factory() as db:
            return await get_credential(db, "plex")

    return asyncio.run(fetch())


def test_plex_login_prints_a_link_and_stores_what_it_gets(monkeypatch, capsys, session_factory):
    code = _sign_in(monkeypatch, session_factory, ["--server", "ms01"])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "https://app.plex.tv/auth#?code=abc" in out
    assert "Signed in as chunned." in out
    # Local before relay: the slow path is the last resort, not the default.
    assert "Stored Plex: MS01 (http://192.168.1.10:32400)" in out

    account = _plex_row(session_factory)
    assert (
        account.id,
        account.token,
        account.username,
        account.plex_server_name,
        account.plex_server_url,
    ) == ("plex", "plex-token", "chunned", "MS01", "http://192.168.1.10:32400")


def test_plex_login_takes_the_server_already_named_in_the_environment(
    monkeypatch, capsys, session_factory
):
    """PLEX_SERVER_URL is a server choice; nothing has to be picked twice."""
    code = _sign_in(
        monkeypatch,
        session_factory,
        [],
        plex_server_url="http://attic.invalid:32400",
    )

    assert code == cli.EXIT_OK
    assert "Stored Plex: Attic (http://attic.invalid:32400)" in capsys.readouterr().out


def test_plex_login_refuses_to_guess_between_servers(monkeypatch, capsys, session_factory):
    code = _sign_in(monkeypatch, session_factory, [])

    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILED
    assert "MS01, Attic" in captured.err
    assert "--server" in captured.err
    assert _plex_row(session_factory) is None


def test_plex_login_names_the_server_it_could_not_find(monkeypatch, capsys, session_factory):
    code = _sign_in(monkeypatch, session_factory, ["--server", "Nope"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILED
    assert "no Plex server named 'Nope'" in captured.err
    assert "MS01, Attic" in captured.err
    assert _plex_row(session_factory) is None


def test_plex_login_gives_up_instead_of_waiting_forever(monkeypatch, capsys, session_factory):
    """A link nobody opened is not a reason to hang a shell or a pipeline."""
    from ratingrelay.settings import Settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        "ratingrelay.core.plex_auth.MyPlexPinLogin", lambda **_kwargs: _FakePinLogin()
    )
    monkeypatch.setattr(
        "ratingrelay.settings.get_settings", lambda: Settings(_env_file=None)
    )

    def _timeout(*_args, **_kwargs):
        raise TimeoutError("Plex sign-in did not complete within 5 seconds")

    monkeypatch.setattr("ratingrelay.core.plex_login.wait_for_token", _timeout)

    code = cli.main(["plex-login", "--timeout", "5"], session_factory=session_factory)

    assert code == cli.EXIT_FAILED
    assert "did not complete within 5 seconds" in capsys.readouterr().err
    assert _plex_row(session_factory) is None


def test_wait_for_token_polls_until_plex_confirms():
    from ratingrelay.core import plex_login  # pylint: disable=import-outside-toplevel

    class Pin:
        token = "tok"
        expired = False

        def __init__(self):
            self.checks = 0

        def checkLogin(self):
            self.checks += 1
            return self.checks >= 3

    elapsed = [0.0]

    def clock():
        elapsed[0] += 0.5
        return elapsed[0]

    assert plex_login.wait_for_token(Pin(), timeout=10, sleep=lambda _s: None, clock=clock) == "tok"


def test_wait_for_token_raises_when_nothing_happens():
    from ratingrelay.core import plex_login  # pylint: disable=import-outside-toplevel

    class Pin:
        token = None
        expired = False

        def checkLogin(self):
            return False

    elapsed = [0.0]

    def clock():
        elapsed[0] += 5.0
        return elapsed[0]

    with pytest.raises(TimeoutError, match="within 3 seconds"):
        plex_login.wait_for_token(Pin(), timeout=3, sleep=lambda _s: None, clock=clock)


def test_plex_login_never_prints_the_token(monkeypatch, capsys, session_factory):
    """The token goes to the database, and nowhere near a scrollback or a log."""
    _sign_in(monkeypatch, session_factory, ["--server", "ms01"])

    captured = capsys.readouterr()
    assert "plex-token" not in captured.out + captured.err


def test_plex_login_warns_when_a_settings_token_still_wins(
    monkeypatch, capsys, session_factory
):
    code = _sign_in(
        monkeypatch, session_factory, ["--server", "ms01"], plex_token="from-settings"
    )

    assert code == cli.EXIT_OK
    assert "PLEX_TOKEN" in capsys.readouterr().err


def test_plex_login_warns_when_the_environment_names_a_different_server(
    monkeypatch, capsys, session_factory
):
    """`--server Attic` is what was asked for; PLEX_SERVER_URL is what runs use."""
    code = _sign_in(
        monkeypatch,
        session_factory,
        ["--server", "Attic"],
        plex_server_url="http://shelf.invalid:32400",
    )

    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert "Stored Plex: Attic (http://attic.invalid:32400)" in captured.out
    assert "PLEX_SERVER_URL" in captured.err
    assert "still wins" in captured.err


def test_plex_login_says_nothing_when_the_environment_agrees(
    monkeypatch, capsys, session_factory
):
    _sign_in(
        monkeypatch,
        session_factory,
        [],
        plex_server_url="http://attic.invalid:32400",
    )

    assert capsys.readouterr().err == ""


def test_plex_login_will_not_hand_the_relay_to_another_plex_account(
    monkeypatch, capsys, session_factory
):
    """The link is a bearer credential; the callback refuses a stranger, so must we."""
    from ratingrelay.db.credentials import (  # pylint: disable=import-outside-toplevel
        upsert_credential,
    )

    async def seed():
        async with session_factory() as db:
            await upsert_credential(
                db, "plex", token="owner-token", username="owner", plex_server_name="MS01"
            )

    asyncio.run(seed())

    code = _sign_in(monkeypatch, session_factory, ["--server", "ms01"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILED
    assert "belongs to Plex account 'owner'" in captured.err
    # Nothing was overwritten, so the relay still has its owner.
    assert _plex_row(session_factory).token == "owner-token"


def test_plex_login_refills_a_slot_that_was_disconnected(
    monkeypatch, capsys, session_factory
):
    """Bare refs are slots: disconnect then sign in again is a reconnect."""
    from ratingrelay.db.credentials import (  # pylint: disable=import-outside-toplevel
        delete_credential,
        upsert_credential,
    )

    async def seed_and_disconnect():
        async with session_factory() as db:
            await upsert_credential(
                db, "plex", token="stale", username="chunned", plex_server_name="MS01"
            )
            await delete_credential(db, "plex")

    asyncio.run(seed_and_disconnect())

    code = _sign_in(monkeypatch, session_factory, ["--server", "ms01"])

    assert code == cli.EXIT_OK
    account = _plex_row(session_factory)
    assert (account.token, account.plex_server_name) == ("plex-token", "MS01")


def test_wait_for_token_reports_a_refused_pin():
    """A PIN Plex has expired is a refusal, not a slow human."""
    from ratingrelay.core import plex_login  # pylint: disable=import-outside-toplevel

    class Pin:
        token = None
        expired = True

        def checkLogin(self):
            return False

    with pytest.raises(plex_login.SignInExpired, match="expired or refused"):
        plex_login.wait_for_token(Pin(), timeout=10, sleep=lambda _s: None, clock=lambda: 0.0)


def test_a_player_is_not_offered_as_a_server():
    from ratingrelay.core import plex_login  # pylint: disable=import-outside-toplevel

    with patch("ratingrelay.core.plex_login.MyPlexAccount", _FakeSignInAccount):
        username, choices = plex_login.account_servers("plex-token")

    assert username == "chunned"
    assert [choice.name for choice in choices] == ["MS01", "Attic"]
