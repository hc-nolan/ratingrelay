"""Turn a :class:`~ratingrelay.engine.types.RunReport` into output.

Two renderings, one source of truth: the human block a cron mail reads, and the
JSON document a script reads. Neither re-derives a number — every count comes
from the report's own :class:`TargetReport`, so the CLI cannot disagree with the
web app about what happened.
"""

import json
from typing import Optional, TextIO

from ratingrelay.cli.config import RelaySpec
from ratingrelay.engine.types import OutcomeStatus, RunReport

# A report that is 20 MB because the library moved is not machine-readable in
# practice; details are capped and the document says so.
MAX_DETAILS = 200

_STATUS_FIELDS = ("written", "reverted", "skipped", "unmatched", "failed")


def _details(report: RunReport, status: OutcomeStatus) -> list[dict]:
    rows = []
    for outcome in report.outcomes:
        if outcome.status is not status:
            continue
        row = {
            "artist": outcome.track.artist,
            "title": outcome.track.title,
            "target": outcome.target,
        }
        if outcome.reason is not None:
            row["reason"] = outcome.reason.value
        if outcome.detail:
            row["detail"] = outcome.detail
        rows.append(row)
    return rows


def document(
    spec: RelaySpec,
    report: Optional[RunReport],
    duration: float,
    error: Optional[str] = None,
) -> dict:
    """The JSON report for one relay: one object, no nulls, capped details."""
    if report is None:
        return {"relay": spec.name, "status": "failed", "error": error or "did not run"}

    unmatched = _details(report, OutcomeStatus.UNMATCHED)
    failed = _details(report, OutcomeStatus.FAILED)
    doc: dict = {
        "relay": spec.name,
        "source": report.source,
        "targets": [target.target for target in report.targets],
        "dry_run": report.dry_run,
        "status": report.status,
        "tracks_fetched": report.tracks_fetched,
        "duration_seconds": round(duration, 3),
        "counts": [
            {
                "target": target.target,
                **{key: getattr(target, key) for key in _STATUS_FIELDS},
            }
            for target in report.targets
        ],
        "unmatched": unmatched[:MAX_DETAILS],
        "failed": failed[:MAX_DETAILS],
    }
    if error:
        doc["error"] = error
    if len(unmatched) > MAX_DETAILS or len(failed) > MAX_DETAILS:
        doc["truncated"] = True
    return doc


def human(
    spec: RelaySpec,
    report: Optional[RunReport],
    duration: float,
    error: Optional[str] = None,
) -> str:
    """The block a human reads in a cron mail or a terminal."""
    lines = [f"relay {spec.name}: {spec.summary}"]
    if report is None:
        lines.append(f"failed after {duration:.1f}s: {error or 'unknown error'}")
        return "\n".join(lines)

    if report.dry_run:
        lines.append("dry run — nothing was written")
    lines.append(f"fetched {report.tracks_fetched} tracks in {duration:.1f}s")
    width = max((len(target.target) for target in report.targets), default=0)
    for target in report.targets:
        counts = "  ".join(f"{key} {getattr(target, key)}" for key in _STATUS_FIELDS)
        lines.append(f"  {target.target:<{width}}  {counts}")
    lines.append(f"result: {report.status} ({_tally(report)})")
    return "\n".join(lines)


def _tally(report: RunReport) -> str:
    failed = sum(target.failed for target in report.targets)
    unmatched = sum(target.unmatched for target in report.targets)
    return f"{unmatched} unmatched, {failed} failed"


def dumps(doc: dict) -> str:
    """The JSON report: indented, stable key order, never NaN."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False, default=str)


class Printer:
    """Per-track progress for ``--verbose``: one line each, flushed as it goes."""

    def __init__(self, sink: TextIO):
        self._sink = sink

    async def fetch_complete(self, count: int) -> None:
        print(f"fetched {count} tracks", file=self._sink, flush=True)

    async def outcome(self, outcome) -> None:
        reason = f" {outcome.reason.value}" if outcome.reason else ""
        print(
            f"{outcome.status.value:<9} {outcome.target:<13} "
            f"{outcome.track.artist} – {outcome.track.title}{reason}",
            file=self._sink,
            flush=True,
        )
