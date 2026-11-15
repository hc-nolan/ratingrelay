"""Serve the API against a throwaway database full of made-up runs.

For screenshots and for poking at the UI without a real library. Nothing here
talks to Plex, Last.fm or ListenBrainz: the credentials are fakes, the two
status checks that would call out are stubbed, and the job worker is not
started, so a run queued from the demo just sits in the queue.

    uv run python scripts/demo.py          # API on :8199
    just demo                              # API + Vite on :5174

The database is rebuilt on every start. `.config` is still read for things like
the timezone, but every credential it holds is overridden here.
"""

# pylint: disable=wrong-import-position,import-outside-toplevel,line-too-long,too-many-arguments,too-many-locals
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(tempfile.gettempdir()) / "ratingrelay-demo.db"
PORT = int(os.environ.get("DEMO_PORT", "8199"))

os.environ.update(
    {
        "DATABASE": str(DB_PATH),
        "AUTH": "false",
        "PORT": str(PORT),
        "PLEX_TOKEN": "demo",
        "PLEX_SERVER_URL": "http://plex.demo:32400",
        "LASTFM_TOKEN": "demo",
        "LASTFM_SECRET": "demo",
        "LASTFM_USERNAME": "demo-listener",
        "LASTFM_PASSWORD": "demo",
        "LISTENBRAINZ_TOKEN": "demo",
        "LISTENBRAINZ_USERNAME": "demo-listener",
    }
)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
DB_PATH.unlink(missing_ok=True)

import pylast
import uvicorn
from sqlmodel import Session, create_engine

import ratingrelay.main as app_module
import ratingrelay.routes.plex as plex_routes
from ratingrelay.models.jobs import Job, TrackEvent
from ratingrelay.models.services import ServiceCredential, ServiceName


class _FakeLastFM:
    def __init__(self, **kwargs):
        self.username = kwargs.get("username")

    def get_authenticated_user(self):
        return SimpleNamespace(get_name=lambda: self.username)


async def _no_worker() -> None:
    return None


plex_routes.MyPlexAccount = lambda token: SimpleNamespace(username="demo-listener")
pylast.LastFMNetwork = _FakeLastFM
app_module.worker_loop = _no_worker

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)

LOVED = [
    ("Radiohead", "Reckoner"),
    ("Talking Heads", "This Must Be the Place (Naive Melody)"),
    ("Fleetwood Mac", "Dreams"),
    ("Massive Attack", "Teardrop"),
    ("Kate Bush", "Running Up That Hill"),
    ("LCD Soundsystem", "All My Friends"),
    ("Portishead", "Roads"),
    ("Joni Mitchell", "A Case of You"),
    ("Boards of Canada", "Roygbiv"),
    ("The Cure", "Pictures of You"),
    ("Björk", "Jóga"),
    ("Arcade Fire", "Wake Up"),
]
UNMATCHED = [
    ("Sufjan Stevens", "Chicago (Adult Contemporary Easy Listening Version)", "lastfm",
     "no_match", None),
    ("Aphex Twin", "#3", "listenbrainz", "ambiguous_match",
     [{"artist": "Aphex Twin", "title": "#3", "recording_mbid": "3f1c0d4e-0000-4000-8000-000000000001"},
      {"artist": "Aphex Twin", "title": "Rhubarb", "recording_mbid": "3f1c0d4e-0000-4000-8000-000000000002"}]),
    ("Unknown Artist", "Track 07", "listenbrainz", "no_recording_mbid", None),
]


def _job(engine, *, ago, status, targets, dry_run=False, ok=0, err=0, skipped=0,
         fetched=0, recurring=False, events=(), source=None):
    with Session(engine) as db:
        started = NOW - ago
        job = Job(
            status=status,
            source_config=json.dumps(source or {"service": "plex", "rating_threshold": 8.0, "comparison": "gte"}),
            targets_config=json.dumps([{"service": t} for t in targets]),
            recurring=recurring,
            interval_minutes=1440 if recurring else None,
            created_at=started,
            started_at=started,
            completed_at=started + timedelta(minutes=3),
            tracks_fetched=fetched,
            tracks_ok=ok,
            tracks_err=err,
            tracks_skipped=skipped,
            dry_run=dry_run,
        )
        db.add(job)
        db.commit()
        for i, (artist, title, target, outcome, reason, candidates) in enumerate(events):
            db.add(TrackEvent(
                job_id=job.id,
                created_at=started + timedelta(seconds=i),
                artist=artist,
                title=title,
                target_service=target,
                success=outcome in ("written", "skipped"),
                outcome=outcome,
                reason=reason,
                candidates=json.dumps(candidates) if candidates else None,
            ))
        db.commit()


def seed() -> None:
    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as db:
        db.add(ServiceCredential(service=ServiceName.plex, token="demo", username="demo-listener",
                                 plex_server_url="http://plex.demo:32400", plex_server_name="Living room"))
        db.add(ServiceCredential(service=ServiceName.lastfm, token="demo", secret="demo",
                                 username="demo-listener", password_hash="demo"))
        db.add(ServiceCredential(service=ServiceName.listenbrainz, token="demo", username="demo-listener"))
        db.commit()

    targets = ["lastfm", "listenbrainz"]
    written = [(a, t, tgt, "written", None, None) for a, t in LOVED for tgt in targets]
    unmatched = [(a, t, tgt, "unmatched", r, c) for a, t, tgt, r, c in UNMATCHED]
    synced = [(a, t, tgt, "skipped", "already_synced", None) for a, t in LOVED for tgt in targets]

    _job(engine, ago=timedelta(days=6), status="completed", targets=targets, dry_run=True,
         fetched=15, ok=24, skipped=3, events=written + unmatched)
    _job(engine, ago=timedelta(days=5, hours=23), status="partial", targets=targets,
         fetched=15, ok=24, err=1, skipped=3,
         events=written + unmatched + [("Joy Division", "Atmosphere", "lastfm", "failed", "write_failed", None)])
    for day in range(4, 0, -1):
        _job(engine, ago=timedelta(days=day), status="completed", targets=targets, recurring=True,
             fetched=15, ok=0, skipped=27, events=synced + unmatched)
    _job(engine, ago=timedelta(hours=2), status="completed", targets=["plex"], source={"service": "lastfm"},
         fetched=6, ok=6, events=[(a, t, "plex", "written", None, None) for a, t in LOVED[:6]])


if __name__ == "__main__":
    app_module._run_migrations_sync()  # pylint: disable=protected-access
    seed()
    print(f"demo database: {DB_PATH}")
    uvicorn.run(app_module.app, host="127.0.0.1", port=PORT, log_level="warning")
