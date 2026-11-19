"""The headless entry point: run a relay with no server and no browser.

    ratingrelay relay --source plex --target lastfm --dry-run
    ratingrelay run --config relays.toml
    ratingrelay list --config relays.toml
    ratingrelay plex-login

Everything below the marshalling is :mod:`ratingrelay.engine` — the same fetch,
match, reconcile and write the web app runs, so a headless run and a job in the
app cannot drift apart. No FastAPI import reaches this file (there is a test).

Exit codes, because cron and systemd read nothing else:

    0  ran, nothing failed
    1  ran, some tracks failed on a target
    2  could not complete, or a fatal error
"""

import argparse
import asyncio
from dataclasses import dataclass, replace
import sys
import time
from typing import Optional, Sequence, TextIO

from ratingrelay.cli import report as reports
from ratingrelay.cli.config import (
    ConfigError,
    DEFAULT_CONFIG,
    RelaySpec,
    load_relays,
)
from ratingrelay.engine.refs import KNOWN_SERVICES, parse_ref

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2

PROGRAM = "ratingrelay"


def _exit_code(report) -> int:
    """0 for completed, 1 for partial, 2 when the run failed outright."""
    status = report.status
    if status == "completed":
        return EXIT_OK
    return EXIT_PARTIAL if status == "partial" else EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Relay track ratings between Plex, Last.fm and ListenBrainz.",
        epilog="Credentials come from .config or the database; the API does not "
        "need to be running.",
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    subparsers = parser.add_subparsers(dest="command")

    relay = subparsers.add_parser("relay", help="run one relay from flags")
    _add_relay_flags(relay)

    run = subparsers.add_parser("run", help="run the relays in a config file")
    run.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"relay definitions to run (default: {DEFAULT_CONFIG})",
    )
    run.add_argument(
        "--only", action="append", default=[], metavar="NAME", help="just this relay"
    )
    _add_run_flags(run)

    listing = subparsers.add_parser("list", help="print the relays in a config file")
    listing.add_argument("--config", default=DEFAULT_CONFIG, help="config file to read")

    login = subparsers.add_parser(
        "plex-login",
        help="sign in to Plex from this terminal",
        description="Sign in to Plex without the web app: open the printed link on "
        "any device, and the token and server it returns are stored exactly as "
        "the browser sign-in stores them.",
    )
    login.add_argument(
        "--server",
        metavar="NAME",
        help="the Plex server to use (default: the one PLEX_SERVER_URL names, "
        "or the only one the account sees)",
    )
    login.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SECONDS",
        help="how long to wait for the sign-in (default: 120)",
    )
    return parser


def _account_ref(value: str) -> str:
    """argparse type for a source or target: ``service`` or ``service:account``."""
    try:
        return parse_ref(value).ref
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{exc} — use one of {', '.join(KNOWN_SERVICES)}, or one of those "
            "followed by ':account' (e.g. lastfm:new)"
        ) from exc



def _add_relay_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        default="plex",
        type=_account_ref,
        metavar="SERVICE[:ACCOUNT]",
        help="where the ratings come from (default: plex)",
    )
    parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        type=_account_ref,
        default=[],
        metavar="SERVICE[:ACCOUNT]",
        help="where they go; repeat for more than one (default: lastfm)",
    )
    parser.add_argument(
        "--feedback-type",
        choices=("love", "hate"),
        default="love",
        help="relay loves or hates (default: love). Plex reads and writes the "
        "love/hate thresholds set in the web app or LOVE_THRESHOLD / HATE_THRESHOLD",
    )
    _add_run_flags(parser)


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, match and reconcile for real; write nothing",
    )
    parser.add_argument(
        "--allow-mass-undo",
        action="store_true",
        help="undo even when most of a target's recorded writes were withdrawn "
        "(refused by default: that is usually a source that answered short)",
    )
    parser.add_argument(
        "--reapply-target-drift",
        action="store_true",
        help="write again what the target has since dropped, for every relay in "
        "this run (a target's own edits are otherwise left alone)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print a machine-readable report"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="a line per track, on stderr",
    )


def spec_from_args(args: argparse.Namespace) -> RelaySpec:
    """The relay the flags describe. Validation lives in :class:`RelaySpec`."""
    return RelaySpec(
        source=args.source,
        targets=tuple(args.targets) or ("lastfm",),
        dry_run=getattr(args, "dry_run", False),
        reapply_target_drift=getattr(args, "reapply_target_drift", False),
        allow_mass_undo=args.allow_mass_undo,
        feedback_type=args.feedback_type,
    )


async def run_relay(
    spec: RelaySpec,
    progress=None,
    session_factory=None,
):
    """Run one relay through the engine and return its report.

    *session_factory* and *progress* are injectable so tests can drive the CLI
    against in-memory SQLite and fake clients instead of a real library.
    """
    from ratingrelay.core.factory import (  # pylint: disable=import-outside-toplevel
        source_for_service,
        stores,
        target_for_service,
    )
    from ratingrelay.engine import pipeline  # pylint: disable=import-outside-toplevel
    from ratingrelay.settings import logger  # pylint: disable=import-outside-toplevel

    if session_factory is None:
        session_factory = _session_factory()

    async with session_factory() as db:
        source = await source_for_service(
            db, spec.source, feedback_type=spec.feedback_type
        )
        targets = [
            await target_for_service(db, name, relay=spec.relay)
            for name in spec.targets
        ]
        sync_store, _ = stores(db, spec.relay, dry_run=spec.dry_run)
        logger.info("Starting relay %s: %s", spec.name, spec.summary)
        report = await pipeline.run(
            source,
            targets,
            sync_store=sync_store,
            progress=progress if progress is not None else pipeline.NO_PROGRESS,
            dry_run=spec.dry_run,
            allow_mass_undo=spec.allow_mass_undo,
            reapply_drift=spec.reapply_target_drift,
        )
        logger.info("Relay %s finished: %s", spec.name, report.status)
        return report


def _session_factory():
    from ratingrelay.db import (  # pylint: disable=import-outside-toplevel
        session_maker,
    )

    return session_maker


def _check_database() -> None:
    """Fail with a sentence instead of 'no such table' from SQLite."""
    from pathlib import Path  # pylint: disable=import-outside-toplevel

    from ratingrelay.settings import get_settings  # pylint: disable=import-outside-toplevel

    database = Path(get_settings().database)
    if not database.exists():
        raise ConfigError(
            f"no database at {database} — run `just migrate`, or point DATABASE "
            "at the relay's database"
        )


class PlexLoginError(Exception):
    """A ``plex-login`` that could not finish, with a sentence for the terminal."""


def _sign_in(server: Optional[str], timeout: int, out: TextIO):
    """Print the link, wait for the click, and return what Plex handed back.

    The one thing a headless install cannot do for itself is the browser: Plex
    only hands a token to a signed-in human. Everything after that click is
    automatic.
    """
    from ratingrelay.core import plex_login  # pylint: disable=import-outside-toplevel
    from ratingrelay.settings import get_settings  # pylint: disable=import-outside-toplevel

    settings = get_settings()
    try:
        pin_login, url = plex_login.begin()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise PlexLoginError(f"could not start a Plex sign-in: {exc}") from exc

    print("Open this on any device and sign in to Plex:", file=out)
    print(f"  {url}", file=out, flush=True)
    print("Waiting for the sign-in (Ctrl-C to cancel)…", file=out, flush=True)

    try:
        token = plex_login.wait_for_token(pin_login, timeout=timeout)
        username, choices = plex_login.account_servers(token)
        # A server already named in the environment wins over asking, exactly as
        # the credential precedence does everywhere else.
        chosen = plex_login.choose(
            choices,
            name=server,
            url=str(settings.plex_server_url or "") or None,
        )
    except (plex_login.SignInExpired, TimeoutError, ValueError) as exc:
        raise PlexLoginError(str(exc)) from exc
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise PlexLoginError(f"Plex sign-in failed: {exc}") from exc
    return token, username, chosen


def _stored_owner(session_factory=None) -> Optional[str]:
    """The Plex username this relay already belongs to, if it has one."""
    from ratingrelay.db.credentials import (  # pylint: disable=import-outside-toplevel
        get_credential,
    )

    if session_factory is None:
        session_factory = _session_factory()

    async def fetch() -> Optional[str]:
        async with session_factory() as db:
            account = await get_credential(db, "plex")
            return account.username if account else None

    return asyncio.run(fetch())


def _store_plex_account(token, username, chosen, session_factory=None) -> None:
    """Store the account the sign-in produced, as the web callback would."""
    from ratingrelay.db.credentials import (  # pylint: disable=import-outside-toplevel
        upsert_credential,
    )

    # The same refusal the browser callback makes: the PIN link is a bearer
    # credential for this relay's identity, and whoever opens it first would
    # otherwise become the owner. One user, one library (AGENTS.md).
    owner = _stored_owner(session_factory)
    if owner and username and owner.casefold() != username.casefold():
        raise PlexLoginError(
            f"this relay belongs to Plex account {owner!r} and you signed in as "
            f"{username!r}; connect as the owner, or disconnect Plex first to "
            "hand the relay over"
        )

    if session_factory is None:
        session_factory = _session_factory()

    async def store() -> None:
        async with session_factory() as db:
            await upsert_credential(
                db,
                "plex",
                token=token,
                username=username,
                plex_server_url=chosen.url,
                plex_server_name=chosen.name,
            )

    try:
        asyncio.run(store())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise PlexLoginError(
            f"signed in as {username}, but could not store it: {exc}"
        ) from exc


def _warn_if_settings_win(chosen, err: TextIO) -> None:
    """Say what would make a fresh sign-in look like it was ignored.

    Settings describe the first account completely, including the server, and
    they win — so both halves of that precedence are worth a sentence here.
    """
    from urllib.parse import urlparse  # pylint: disable=import-outside-toplevel

    from ratingrelay.settings import get_settings  # pylint: disable=import-outside-toplevel

    settings = get_settings()
    if settings.plex_token:
        print(
            f"{PROGRAM}: PLEX_TOKEN is set in the environment and still wins; "
            "remove it to use the account just stored.",
            file=err,
        )
    if not settings.plex_server_url:
        return
    configured = str(settings.plex_server_url)
    if urlparse(configured).hostname != urlparse(chosen.url).hostname:
        print(
            f"{PROGRAM}: PLEX_SERVER_URL is {configured} and still wins; remove it "
            f"to relay to {chosen.name} ({chosen.url}).",
            file=err,
        )


def _plex_login(args, out: TextIO, err: TextIO, session_factory=None) -> int:
    """Sign in to Plex from the terminal, and store what the browser would have."""
    if args.timeout < 1:
        print(f"{PROGRAM}: --timeout must be at least 1 second", file=err)
        return EXIT_FAILED

    try:
        token, username, chosen = _sign_in(args.server, args.timeout, out)
        _store_plex_account(token, username, chosen, session_factory)
    except PlexLoginError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_FAILED
    except KeyboardInterrupt:
        # Waiting on a link is an interactive wait; leaving means leaving.
        print(f"{PROGRAM}: Plex sign-in cancelled", file=err)
        return EXIT_FAILED

    print(f"Signed in as {username}.", file=out)
    print(f"Stored Plex: {chosen.name} ({chosen.url})", file=out)
    _warn_if_settings_win(chosen, err)
    return EXIT_OK


async def _run_specs(
    specs: Sequence[RelaySpec],
    console: "Console",
    session_factory=None,
) -> int:
    worst = EXIT_OK
    documents = []
    for spec in specs:
        progress = (
            reports.Printer(console.err) if console.verbose and not console.as_json else None
        )
        started = time.monotonic()
        error: Optional[str] = None
        try:
            report = await run_relay(
                spec, progress=progress, session_factory=session_factory
            )
        except ValueError as exc:
            report, error = None, str(exc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            report, error = None, f"{type(exc).__name__}: {exc}"
        duration = time.monotonic() - started

        if report is None:
            print(f"{PROGRAM}: {spec.name}: {error}", file=console.err, flush=True)
            worst = EXIT_FAILED
        else:
            worst = max(worst, _exit_code(report))
        if console.as_json:
            documents.append(reports.document(spec, report, duration, error))
        else:
            print(
                reports.human(spec, report, duration, error),
                file=console.out,
                flush=True,
            )

    if console.as_json:
        payload = documents[0] if len(documents) == 1 else {"relays": documents}
        print(reports.dumps(payload), file=console.out, flush=True)
    return worst


@dataclass(frozen=True)
class Console:
    """Where a run's output goes, and in which shape."""

    out: TextIO
    err: TextIO
    as_json: bool = False
    verbose: bool = False


def _print_listing(specs: Sequence[RelaySpec], out: TextIO) -> int:
    width = max((len(spec.name) for spec in specs), default=0)
    for spec in specs:
        mode = " (dry run)" if spec.dry_run else ""
        print(f"{spec.name:<{width}}  {spec.summary}{mode}", file=out)
    return EXIT_OK


def _plex_login_command(args, out: TextIO, err: TextIO, session_factory=None) -> int:
    """``plex-login``: needs the database it writes the account into."""
    try:
        if session_factory is None:
            _check_database()
    except ConfigError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_FAILED
    return _plex_login(args, out, err, session_factory)


def main(
    argv: Optional[Sequence[str]] = None, session_factory=None
) -> int:
    """Parse *argv*, run, and return the exit code.

    *session_factory* is only for tests; nothing in the CLI passes it.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from ratingrelay.settings import (  # pylint: disable=import-outside-toplevel
            get_settings,
        )

        print(f"{PROGRAM} {get_settings().version}")
        return EXIT_OK

    if args.command is None:
        parser.print_help()
        return EXIT_FAILED

    out, err = sys.stdout, sys.stderr
    if args.command == "plex-login":
        return _plex_login_command(args, out, err, session_factory)

    try:
        if args.command == "list":
            return _print_listing(load_relays(args.config), out)
        if args.command == "relay":
            specs = (spec_from_args(args),)
        else:
            specs = _select(
                load_relays(args.config),
                args.only,
                args.dry_run,
                args.allow_mass_undo,
                args.reapply_target_drift,
            )
        # Same bucket as a bad config file: a missing database is something the
        # checkpoint can fix, not a run that went wrong.
        if session_factory is None:
            _check_database()
    except ConfigError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_FAILED

    return asyncio.run(
        _run_specs(
            specs,
            Console(out=out, err=err, as_json=args.json, verbose=args.verbose),
            session_factory=session_factory,
        )
    )


def _select(
    specs: Sequence[RelaySpec],
    only: Sequence[str],
    dry_run: bool,
    allow_mass_undo: bool = False,
    reapply_target_drift: bool = False,
) -> tuple[RelaySpec, ...]:
    """The relays to run, with ``--only`` and the run-wide flags applied."""
    chosen = tuple(specs)
    if only:
        known = {spec.name for spec in specs}
        missing = sorted(set(only) - known)
        if missing:
            raise ConfigError(
                f"no relay named {', '.join(missing)} "
                f"(have: {', '.join(sorted(known))})"
            )
        chosen = tuple(spec for spec in specs if spec.name in only)
    if dry_run:
        chosen = tuple(replace(spec, dry_run=True) for spec in chosen)
    if allow_mass_undo:
        chosen = tuple(replace(spec, allow_mass_undo=True) for spec in chosen)
    if reapply_target_drift:
        chosen = tuple(replace(spec, reapply_target_drift=True) for spec in chosen)
    return chosen


def entry() -> None:
    """Console-script wrapper: the process exits with :func:`main`'s code."""
    raise SystemExit(main())
