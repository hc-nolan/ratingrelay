"""The headless entry point: run a relay with no server and no browser.

    ratingrelay relay --source plex --target lastfm --dry-run
    ratingrelay run --config relays.toml
    ratingrelay list --config relays.toml

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
    SOURCE_SERVICES,
    TARGET_SERVICES,
    load_relays,
)

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
    return parser


def _add_relay_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        default="plex",
        choices=SOURCE_SERVICES,
        help="where the ratings come from (default: plex)",
    )
    parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        choices=TARGET_SERVICES,
        default=[],
        metavar="SERVICE",
        help="where they go; repeat for more than one (default: lastfm)",
    )
    parser.add_argument(
        "--rating-threshold",
        type=float,
        default=8.0,
        help="Plex source: the rating a track needs (default: 8)",
    )
    parser.add_argument(
        "--comparison",
        choices=("gte", "lte"),
        default="gte",
        help="Plex source: at least (gte) or at most (lte) the threshold",
    )
    parser.add_argument(
        "--feedback-type",
        choices=("love", "hate"),
        default="love",
        help="Last.fm/ListenBrainz: which feedback to relay (default: love)",
    )
    parser.add_argument(
        "--plex-rating",
        type=float,
        default=10.0,
        help="Plex target: the rating to write (default: 10)",
    )
    _add_run_flags(parser)


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, match and reconcile for real; write nothing",
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
        rating_threshold=args.rating_threshold,
        comparison=args.comparison,
        feedback_type=args.feedback_type,
        plex_rating=args.plex_rating,
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
            db,
            spec.source,
            rating_threshold=spec.rating_threshold,
            comparison=spec.comparison,
            feedback_type=spec.feedback_type,
        )
        targets = [
            await target_for_service(
                db,
                name,
                plex_rating=spec.plex_rating,
                feedback_type=spec.feedback_type,
            )
            for name in spec.targets
        ]
        sync_store, _ = stores(db)
        logger.info("Starting relay %s: %s", spec.name, spec.summary)
        report = await pipeline.run(
            source,
            targets,
            sync_store=sync_store,
            progress=progress if progress is not None else pipeline.NO_PROGRESS,
            dry_run=spec.dry_run,
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
    try:
        if args.command == "list":
            return _print_listing(load_relays(args.config), out)
        if args.command == "relay":
            specs = (spec_from_args(args),)
        else:
            specs = _select(load_relays(args.config), args.only, args.dry_run)
    except ConfigError as exc:
        print(f"{PROGRAM}: {exc}", file=err)
        return EXIT_FAILED

    try:
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
    specs: Sequence[RelaySpec], only: Sequence[str], dry_run: bool
) -> tuple[RelaySpec, ...]:
    """The relays to run, with ``--only`` and ``--dry-run`` applied."""
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
    return chosen


def entry() -> None:
    """Console-script wrapper: the process exits with :func:`main`'s code."""
    raise SystemExit(main())
